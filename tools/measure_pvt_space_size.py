#!/usr/bin/env python3
"""Measure how much media a Telegram chat holds.  (read-only, nothing is downloaded)

Usage (from the PVT SPACE folder):

  py -3.11 .\\tools\\measure_pvt_space_size.py                 PVT SPACE (default)
  py -3.11 .\\tools\\measure_pvt_space_size.py --diane         Diane
  py -3.11 .\\tools\\measure_pvt_space_size.py --sources       every channel in channels.txt
  py -3.11 .\\tools\\measure_pvt_space_size.py --all           PVT + Diane + all sources
  py -3.11 .\\tools\\measure_pvt_space_size.py --chat id:-1001234567890   any chat (repeatable)
  py -3.11 .\\tools\\measure_pvt_space_size.py --sources --count-only      QUICK: message/file COUNTS only (seconds, no sizes)

Speed: PVT and Diane are re-read every time (messages move in and out of them). Source
channels are remembered in size_cache.sqlite3, so a re-run only reads their NEW messages.
Add --fresh to re-read everything (needed if a source deleted old posts).

Reports sizes by type, how much of the video space is over/under 30 seconds (the PVT rule),
and how many bytes are repeated copies of the same Telegram file. Writes
reports\\size_report.csv. Do not run it while the pipeline is running.
"""

import argparse
import asyncio
import csv
import logging
import sqlite3
import time

from telethon.utils import get_peer_id

from pvt_common import (DATA_DIR, REPORT_DIR, DialogIndex, SingleInstance, StopRequested, diane_reference,
                        human_size, iter_history, load_channels, load_config, make_client, media_info,
                        resolve_peer, setup_logging, tg_idempotent)

log = logging.getLogger("pvt.size")
DB_PATH = DATA_DIR / "size_cache.sqlite3"
CSV_PATH = REPORT_DIR / "size_report.csv"
VIDEO_KEEP_SECONDS = 30.0       # same cut-off as the pipeline: PVT only above this
COMMIT_EVERY = 1000
LOG_EVERY = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL, kind TEXT NOT NULL,
    size INTEGER, fid INTEGER, duration REAL,
    PRIMARY KEY (chat_id, msg_id)
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY, title TEXT, last_id INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0, scanned_at INTEGER NOT NULL DEFAULT 0
);
"""


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.executescript(SCHEMA)
    con.commit()
    return con


async def measure_chat(client, con, entity, title, fresh):
    """Read new messages of one chat into the cache. Resumable at any point."""
    chat_id = get_peer_id(entity)
    if fresh:
        con.execute("DELETE FROM media WHERE chat_id=?", (chat_id,))
        con.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
        con.commit()
    row = con.execute("SELECT last_id, messages FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
    last_id, messages = (row["last_id"], row["messages"]) if row else (0, 0)
    log.info("Reading '%s' (ID %s)%s ...", title, chat_id,
             f" - continuing after message {last_id}" if last_id else " - full read")
    new = pending = 0
    try:
        async for msg in iter_history(client, entity, after_id=last_id):
            new += 1
            pending += 1
            info = media_info(msg)
            if info:
                con.execute("INSERT OR REPLACE INTO media(chat_id,msg_id,kind,size,fid,duration) VALUES(?,?,?,?,?,?)",
                            (chat_id, int(msg.id), info["kind"], info["size"], info["fid"], info["duration"]))
            last_id = msg.id
            if pending >= COMMIT_EVERY:
                save_progress(con, chat_id, title, last_id, messages + new)
                pending = 0
            if new % LOG_EVERY == 0:
                log.info("  '%s': %s new messages read ...", title, new)
    finally:                                    # also runs on stop request / crash
        save_progress(con, chat_id, title, last_id, messages + new)
    return chat_id


def save_progress(con, chat_id, title, last_id, messages):
    con.execute("INSERT INTO chats(chat_id,title,last_id,messages,scanned_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,last_id=excluded.last_id,"
                "messages=excluded.messages,scanned_at=excluded.scanned_at",
                (chat_id, title, last_id, messages, int(time.time())))
    con.commit()


def summarize(con, chat_id):
    """Pure SQL aggregation of the cached rows for one chat."""
    by_kind = {r["kind"]: (r["n"], r["bytes"], r["unknown"]) for r in con.execute(
        "SELECT kind, COUNT(*) n, COALESCE(SUM(size),0) bytes, SUM(size IS NULL) unknown "
        "FROM media WHERE chat_id=? GROUP BY kind", (chat_id,))}
    video = {}
    for label, cond in (("over", "duration > ?"), ("under", "duration <= ?")):
        r = con.execute(f"SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM media "
                        f"WHERE chat_id=? AND kind='video' AND duration IS NOT NULL AND {cond}",
                        (chat_id, VIDEO_KEEP_SECONDS)).fetchone()
        video[label] = (r["n"], r["b"])
    r = con.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM media "
                    "WHERE chat_id=? AND kind='video' AND duration IS NULL", (chat_id,)).fetchone()
    video["unknown"] = (r["n"], r["b"])
    rep = con.execute(
        "WITH g AS (SELECT fid, MIN(size) s, COUNT(*) c FROM media "
        "WHERE chat_id=? AND fid IS NOT NULL AND size IS NOT NULL GROUP BY fid HAVING c>1) "
        "SELECT COALESCE(SUM(c-1),0) n, COALESCE(SUM(s*(c-1)),0) b FROM g", (chat_id,)).fetchone()
    messages = con.execute("SELECT messages FROM chats WHERE chat_id=?", (chat_id,)).fetchone()["messages"]
    return {"by_kind": by_kind, "video": video, "repeated": (rep["n"], rep["b"]), "messages": messages}


COUNT_FILTERS = (("photos", "InputMessagesFilterPhotos"), ("videos", "InputMessagesFilterVideo"),
                 ("gifs", "InputMessagesFilterGif"), ("documents", "InputMessagesFilterDocument"))


async def quick_counts(client, entity):
    """Telegram's own totals, one tiny request each - no message is downloaded and no sizes are known."""
    from telethon.tl import types as tl_types
    out = {}
    first = await tg_idempotent(client, lambda: client.get_messages(entity, limit=0))
    out["messages"] = getattr(first, "total", 0)
    for label, filter_name in COUNT_FILTERS:
        flt = getattr(tl_types, filter_name)()
        result = await tg_idempotent(client, lambda f=flt: client.get_messages(entity, limit=0, filter=f))
        out[label] = getattr(result, "total", 0)
    return out


def print_count_table(rows):
    print(f"\n{'Channel':<40}{'Messages':>10}{'Photos':>9}{'Videos':>9}{'GIFs':>8}{'Docs':>8}")
    print("-" * 84)
    totals = dict.fromkeys(("messages", "photos", "videos", "gifs", "documents"), 0)
    for title, c in rows:
        for key in totals:
            totals[key] += c.get(key, 0) or 0
        print(f"{title[:38]:<40}{c['messages']:>10,}{c['photos']:>9,}{c['videos']:>9,}{c['gifs']:>8,}{c['documents']:>8,}")
    print("-" * 84)
    print(f"{'TOTAL':<40}{totals['messages']:>10,}{totals['photos']:>9,}{totals['videos']:>9,}{totals['gifs']:>8,}{totals['documents']:>8,}")
    print("Counts come straight from Telegram (instant). Sizes need the full measure run.")


def print_summary(title, chat_id, s):
    total_files = sum(n for n, _, _ in s["by_kind"].values())
    total_bytes = sum(b for _, b, _ in s["by_kind"].values())
    unknown = sum(u or 0 for _, _, u in s["by_kind"].values())
    print(f"\n========== {title}  (ID {chat_id}) ==========")
    print(f"Messages read: {s['messages']:,}    Files: {total_files:,}")
    print("-" * 56)
    for kind in sorted(s["by_kind"]):
        n, b, _ = s["by_kind"][kind]
        print(f"  {kind:<14}{n:>9,} files  {human_size(b):>14}")
    print("-" * 56)
    print(f"  TOTAL SIZE: {human_size(total_bytes)}   ({total_bytes:,} bytes)")
    v = s["video"]
    if any(n for n, _ in v.values()):
        print(f"  Videos over {VIDEO_KEEP_SECONDS:.0f}s (belong in PVT): {v['over'][0]:,} files, {human_size(v['over'][1])}")
        print(f"  Videos {VIDEO_KEEP_SECONDS:.0f}s or shorter (Diane):   {v['under'][0]:,} files, {human_size(v['under'][1])}")
        if v["unknown"][0]:
            print(f"  Videos with unknown duration:       {v['unknown'][0]:,} files, {human_size(v['unknown'][1])}")
    n, b = s["repeated"]
    if n:
        print(f"  Repeated copies of the same Telegram file: {n:,} extra files = {human_size(b)}")
    if unknown:
        print(f"  Note: {unknown:,} files had no size from Telegram, so the total may be slightly low.")
    return total_files, total_bytes


def write_csv(con, measured):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    temp = CSV_PATH.with_suffix(".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["chat_name", "chat_id", "type", "files", "size_bytes", "size_human", "files_without_size"])
        for title, chat_id in measured:
            for kind, (n, b, unknown) in sorted(summarize(con, chat_id)["by_kind"].items()):
                writer.writerow([title, chat_id, kind, n, b, human_size(b), unknown or 0])
    temp.replace(CSV_PATH)


def pick_targets(args, config):
    """[(label, reference, always_reread)] in the order they will be measured."""
    targets = []
    if args.pvt or args.all or not (args.diane or args.sources or args.chat):
        if not config.get("destination"):
            raise SystemExit("config.json is missing 'destination' (PVT SPACE).")
        targets.append(("PVT SPACE", config["destination"], True))
    if args.diane or args.all:
        targets.append(("Diane", diane_reference(config), True))
    if args.sources or args.all:
        targets += [(ref, ref, False) for ref in load_channels()]
    targets += [(ref, ref, False) for ref in (args.chat or [])]
    return targets


async def run(args):
    config = load_config(need_destination=False)
    con = init_db()
    client = make_client(config)
    measured, missing, count_rows = [], [], []
    try:
        await client.start()
        index = await DialogIndex.build(client)
        for label, ref, always_reread in pick_targets(args, config):
            try:
                entity = await resolve_peer(client, ref, index)
            except Exception as exc:  # noqa: BLE001
                log.error("Cannot open %s: %s", label, exc)
                missing.append(label)
                continue
            title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or label
            if args.count_only:
                count_rows.append((title, await quick_counts(client, entity)))
                continue
            chat_id = await measure_chat(client, con, entity, title, args.fresh or always_reread)
            measured.append((title, chat_id))
    except StopRequested as stop:
        log.warning("GRACEFUL STOP: %s Progress is saved; run again to continue.", stop)
    finally:
        try:
            grand_files = grand_bytes = 0
            for title, chat_id in measured:
                files, size = print_summary(title, chat_id, summarize(con, chat_id))
                grand_files += files
                grand_bytes += size
            if len(measured) > 1:
                print(f"\n########## ALL {len(measured)} CHATS: {grand_files:,} files, {human_size(grand_bytes)} ##########")
            if count_rows:
                print_count_table(count_rows)
            if measured:
                write_csv(con, measured)
                print(f"\nReport saved: {CSV_PATH}")
            if missing:
                print(f"NOT MEASURED (not accessible): {', '.join(missing)}")
            if measured:
                print("Sizes are what Telegram reports for each message; a file appearing twice is counted twice.")
        finally:
            con.close()
            await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Measure the media size of Telegram chats (read-only).")
    parser.add_argument("--pvt", action="store_true", help="PVT SPACE (the default when nothing is chosen).")
    parser.add_argument("--diane", action="store_true", help="Diane.")
    parser.add_argument("--sources", action="store_true", help="Every channel in channels.txt.")
    parser.add_argument("--all", action="store_true", help="PVT SPACE + Diane + all sources.")
    parser.add_argument("--chat", action="append", metavar="REF", help="Any chat: id:-100..., @name, or exact title.")
    parser.add_argument("--fresh", action="store_true", help="Ignore the cache and re-read the chosen chats.")
    parser.add_argument("--count-only", action="store_true",
                        help="QUICK: only message/photo/video/GIF/document counts from Telegram (seconds, no sizes).")
    args = parser.parse_args()
    setup_logging("measure_size")
    with SingleInstance():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
