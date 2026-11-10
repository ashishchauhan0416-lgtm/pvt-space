#!/usr/bin/env python3
"""PVT SPACE duplicate finder and mover  (Step 1).

What it does
------------
1. SCAN    - reads every message in PVT SPACE (metadata only, no downloads) and
             remembers it in pvt_dedupe.sqlite3. Resumable; later runs only read
             new messages.
2. MATCH   - finds duplicates cheaply:
               a) same Telegram file id          -> certain duplicate, no download
               b) same size/duration/dimensions  -> compare a 3 x 1 MB sample hash
                  (--strict hashes the whole file, streamed, nothing saved to disk)
3. MOVE    - (only with --execute) forwards each duplicate to Diane, confirms
             Telegram accepted it, then deletes it from PVT.

Rules
-----
* The OLDEST copy always stays in PVT.
* Albums are never split. Album-vs-album duplicates need the same set of files.
  A standalone file that also sits inside a kept album is a duplicate of it.
* An album that only partly overlaps another album is left alone.
* Default is a DRY RUN. Nothing changes in Telegram without --execute.
* A forward whose result is uncertain is NEVER retried automatically.
* Never run this at the same time as the main pipeline (same Telegram session).
"""

import argparse
import asyncio
import csv
import hashlib
import logging
import sqlite3
import time
from collections import defaultdict

from telethon.errors import FloodWaitError, RPCError
from telethon.utils import get_peer_id

from pvt_common import (MB, DATA_DIR, REPORT_DIR, StopRequested, SingleInstance,
                        check_stop, diane_reference, iter_history, load_config, make_client, media_info,
                        resolve_peer, setup_logging, sleep_checked, tg_idempotent)

DB_PATH = DATA_DIR / "pvt_dedupe.sqlite3"
REPORT_PATH = REPORT_DIR / "pvt_duplicates.csv"
CHUNK = 512 * 1024              # Telegram-friendly download chunk
SAMPLE_FULL_LIMIT = 3 * MB      # files up to this size are hashed completely
SETTLE_SECONDS = 300            # ignore albums whose newest post is younger than this
SCAN_COMMIT_EVERY = 500

log = logging.getLogger("pvt.dedupe")


# --------------------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    msg_id INTEGER PRIMARY KEY,
    grouped_id INTEGER,
    kind TEXT NOT NULL,
    fid INTEGER,
    size INTEGER,
    duration INTEGER,
    width INTEGER,
    height INTEGER,
    msg_date INTEGER NOT NULL,
    protected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sample_cache (
    fid INTEGER NOT NULL,
    mode TEXT NOT NULL,
    sig TEXT NOT NULL,
    PRIMARY KEY (fid, mode)
);
CREATE TABLE IF NOT EXISTS groups (
    dup_key TEXT PRIMARY KEY,
    first_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    keeper_ids TEXT NOT NULL,
    method TEXT NOT NULL,
    msg_date INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    detail TEXT NOT NULL DEFAULT '',
    target_ids TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
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


def get_meta(con, key, default=""):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def set_group_status(con, dup_key, status, detail="", target_ids=""):
    con.execute("UPDATE groups SET status=?, detail=?, target_ids=CASE WHEN ?<>'' THEN ? ELSE target_ids END,"
                " updated_at=? WHERE dup_key=?",
                (status, str(detail)[:900], target_ids, target_ids, int(time.time()), dup_key))
    con.commit()


# --------------------------------------------------------------------------- scanning (no downloads)

def describe(msg):
    """Metadata of a message's media, or None when it carries no photo/file."""
    base = media_info(msg)
    if base is None:
        return None
    date = getattr(msg, "date", None)
    return {
        "msg_id": int(msg.id),
        "grouped_id": getattr(msg, "grouped_id", None),
        "kind": base["kind"],
        "fid": base["fid"],
        "size": base["size"],
        "duration": int(round(base["duration"])) if base["duration"] is not None else 0,
        "width": base["width"],
        "height": base["height"],
        "msg_date": int(date.timestamp()) if date else 0,
        "protected": 1 if getattr(msg, "noforwards", False) else 0,
    }


def store_file(con, d):
    con.execute(
        "INSERT OR REPLACE INTO files(msg_id,grouped_id,kind,fid,size,duration,width,height,msg_date,protected)"
        " VALUES(:msg_id,:grouped_id,:kind,:fid,:size,:duration,:width,:height,:msg_date,:protected)", d)


async def scan_pvt(client, pvt, con):
    last_id = int(get_meta(con, "scan_last_id", "0"))
    log.info("SCAN: reading PVT metadata after message ID %s (no downloads).", last_id)
    seen = stored = pending = 0
    async for msg in iter_history(client, pvt, after_id=last_id):
        seen += 1
        d = describe(msg)
        if d:
            store_file(con, d)
            stored += 1
        last_id = msg.id
        pending += 1
        if pending >= SCAN_COMMIT_EVERY:
            set_meta(con, "scan_last_id", last_id)
            con.commit()
            pending = 0
            log.info("SCAN: %s messages read, %s with media (at ID %s).", seen, stored, last_id)
    set_meta(con, "scan_last_id", last_id)
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    log.info("SCAN complete: %s new messages read; %s media messages indexed in total.", seen, total)


# --------------------------------------------------------------------------- matching

class DSU:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def meta_key(f):
    return (f["kind"], f["size"], f["duration"], f["width"], f["height"])


async def sample_signature(client, pvt, msg_id, size, strict):
    """SHA-256 of the whole file (strict / small) or of 3 x 1 MB samples. Streams; saves nothing."""
    async def work():
        msg = await client.get_messages(pvt, ids=msg_id)
        if msg is None or not getattr(msg, "media", None):
            return None
        digest = hashlib.sha256(str(size).encode())
        if strict or size <= SAMPLE_FULL_LIMIT:
            async for chunk in client.iter_download(msg.media, request_size=CHUNK):
                digest.update(chunk)
        else:
            last_start = ((size - 1) // CHUNK) * CHUNK
            offsets = [0, ((size // 2) // CHUNK) * CHUNK, max(0, last_start - CHUNK)]
            for offset in offsets:
                async for chunk in client.iter_download(
                        msg.media, offset=offset, limit=2, request_size=CHUNK, file_size=size):
                    digest.update(chunk)
        return digest.hexdigest()
    return await tg_idempotent(client, work)


async def build_classes(client, pvt, con, files, strict, fast=False, workers=4):
    """Map every file id to a content class. Same class == same file content.

    fast=True  : files with exactly the same size, duration and dimensions count as the same file
                 (nothing is downloaded).
    otherwise  : candidates are compared by a content hash, `workers` files at a time.
    Returns (class_of: fid -> class, multi: set of classes that merged several file ids).
    """
    mode = "full" if strict else "sample"
    by_meta = defaultdict(dict)                      # meta key -> {fid: oldest msg_id}
    for f in files:                                  # files are ordered by msg_id
        if f["fid"] is None or f["size"] is None:
            continue
        by_meta[meta_key(f)].setdefault(f["fid"], f["msg_id"])

    todo = [(k, m) for k, m in by_meta.items() if len(m) >= 2]
    dsu = DSU()
    needed = sum(len(m) for _, m in todo)
    hashed = skipped = 0
    if fast:
        log.info("MATCH (--fast): %s groups of files (%s files) share exact size, duration and dimensions; "
                 "treated as the same file. Nothing is downloaded.", len(todo), needed)
        for _, fid_map in todo:
            fids = list(fid_map)
            for other in fids[1:]:
                dsu.union(fids[0], other)
    else:
        sigs = {}                                    # fid -> signature
        jobs = []
        for key, fid_map in todo:
            for fid, mid in fid_map.items():
                row = con.execute("SELECT sig FROM sample_cache WHERE fid=? AND mode=?", (fid, mode)).fetchone()
                if row:
                    sigs[fid] = row["sig"]
                else:
                    jobs.append((fid, mid, key[1]))
        log.info("MATCH: %s groups share size/duration/dimensions (%s files). %s already checked earlier, "
                 "%s to check now with %s at a time.", len(todo), needed, needed - len(jobs), len(jobs), workers)
        started = time.time()
        gate = asyncio.Semaphore(max(1, workers))

        async def check(fid, mid, size):
            nonlocal hashed, skipped
            async with gate:
                sig = await sample_signature(client, pvt, mid, size, strict)
            if sig is None:
                skipped += 1
                log.warning("Could not read message %s for hashing; treated as unique.", mid)
                return
            sigs[fid] = sig
            con.execute("INSERT OR REPLACE INTO sample_cache(fid,mode,sig) VALUES(?,?,?)", (fid, mode, sig))
            con.commit()
            hashed += 1
            if hashed % 10 == 0 or hashed == len(jobs):
                rate = (time.time() - started) / hashed
                log.info("MATCH: %s of %s checked (%.0f%%), about %.0f minute(s) left.",
                         hashed, len(jobs), 100.0 * hashed / len(jobs), rate * (len(jobs) - hashed) / 60)

        tasks = [asyncio.ensure_future(check(*job)) for job in jobs]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)   # finished checks are already saved
            raise
        for key, fid_map in todo:
            by_sig = defaultdict(list)
            for fid in fid_map:
                if fid in sigs:
                    by_sig[sigs[fid]].append(fid)
            for fids in by_sig.values():
                for other in fids[1:]:
                    dsu.union(fids[0], other)
    class_of, multi = {}, set()
    members = defaultdict(set)
    for f in files:
        if f["fid"] is not None:
            members[dsu.find(f["fid"])].add(f["fid"])
    for root, fids in members.items():
        for fid in fids:
            class_of[fid] = root
        if len(fids) > 1:
            multi.add(root)
    if not fast:
        log.info("MATCH: %s files checked this run, %s unreadable.", hashed, skipped)
    return class_of, multi


def build_units(files):
    units, cur = [], None
    for f in files:
        gid = f["grouped_id"]
        if gid is not None and cur is not None and cur["gid"] == gid:
            cur["files"].append(f)
        else:
            if cur:
                units.append(cur)
            cur = {"gid": gid, "files": [f]}
    if cur:
        units.append(cur)
    for u in units:
        u["ids"] = [f["msg_id"] for f in u["files"]]
        u["album"] = u["gid"] is not None and len(u["files"]) > 1
        u["date"] = max(f["msg_date"] for f in u["files"])
    return units


def find_duplicates(units, class_of, multi, now=None, settle=SETTLE_SECONDS, merged_label="content_hash"):
    """Pure function: returns the duplicate groups to move.

    Order matters so that every duplicate points at a copy that STAYS in PVT:
      1. albums  - the oldest album with an identical set of files is kept;
      2. singles - a standalone file that also lives inside a kept album is a
                   duplicate of that album (albums are never split);
      3. singles - among the remaining standalone files the oldest is kept.
    """
    now = now if now is not None else time.time()

    def klass(f):
        if f["fid"] is None:
            return f"U{f['msg_id']}"
        return str(class_of.get(f["fid"], f["fid"]))

    def fids(u):
        return sorted(f["fid"] for f in u["files"] if f["fid"] is not None)

    def method(dup, keeper):
        same_file = set(fids(dup)) <= set(fids(keeper)) and len(fids(dup)) > 0
        return "same_telegram_file" if same_file else merged_label

    ready = [u for u in units if now - u["date"] >= settle]   # skip posts still being added
    for u in ready:
        u["sig"] = tuple(sorted(klass(f) for f in u["files"]))

    groups = []
    kept_albums, album_seen = [], {}
    for u in (x for x in ready if x["album"]):
        keeper = album_seen.get(u["sig"])
        if keeper is None:
            album_seen[u["sig"]] = u
            kept_albums.append(u)
        else:
            groups.append({"ids": u["ids"], "keeper": keeper["ids"], "kind": "album",
                           "method": method(u, keeper), "date": u["date"]})

    album_class_host = {}
    for album in kept_albums:
        for f in album["files"]:
            album_class_host.setdefault(klass(f), album)

    single_seen = {}
    for u in (x for x in ready if not x["album"]):
        host = album_class_host.get(u["sig"][0]) if len(u["sig"]) == 1 else None
        if host is not None:
            groups.append({"ids": u["ids"], "keeper": host["ids"], "kind": "album_member",
                           "method": method(u, host), "date": u["date"]})
            continue
        keeper = single_seen.get(u["sig"])
        if keeper is None:
            single_seen[u["sig"]] = u
        else:
            groups.append({"ids": u["ids"], "keeper": keeper["ids"], "kind": "single",
                           "method": method(u, keeper), "date": u["date"]})
    groups.sort(key=lambda g: g["ids"][0])
    return groups


def save_groups(con, groups):
    now = int(time.time())
    new = 0
    for g in groups:
        key = ",".join(map(str, g["ids"]))
        keeper = ",".join(map(str, g["keeper"]))
        cur = con.execute(
            "INSERT OR IGNORE INTO groups(dup_key,first_id,kind,keeper_ids,method,msg_date,status,updated_at)"
            " VALUES(?,?,?,?,?,?, 'pending', ?)", (key, g["ids"][0], g["kind"], keeper, g["method"], g["date"], now))
        new += cur.rowcount
        con.execute("UPDATE groups SET keeper_ids=?, method=?, kind=? WHERE dup_key=? "
                    "AND status IN ('pending','changed','keeper_missing')", (keeper, g["method"], g["kind"], key))
    # Groups that are waiting to be moved but are NOT duplicates in this latest analysis (for example because a
    # stricter mode rejected them, or a copy disappeared) must never be moved.
    current = {",".join(map(str, g["ids"])) for g in groups}
    stale = [r["dup_key"] for r in con.execute("SELECT dup_key FROM groups WHERE status IN ('pending','forward_failed')")
             if r["dup_key"] not in current]
    for key in stale:
        con.execute("UPDATE groups SET status='not_duplicate', detail=?, updated_at=? WHERE dup_key=?",
                    ("Not a duplicate in the latest analysis; will not be moved.", now, key))
    if stale:
        log.info("%s earlier duplicate group(s) are no longer duplicates in this analysis and were cancelled.", len(stale))
    con.commit()
    return new


def export_report(con):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = REPORT_PATH.with_suffix(".tmp")
    rows = con.execute("SELECT kind,dup_key,keeper_ids,method,msg_date,status,detail FROM groups "
                       "ORDER BY first_id").fetchall()
    with temp.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["TYPE", "DUPLICATE_MESSAGE_IDS", "KEPT_OLDEST_MESSAGE_IDS", "MATCH_METHOD",
                         "DATE_UTC", "STATUS", "DETAIL"])
        for r in rows:
            date = time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["msg_date"])) if r["msg_date"] else ""
            writer.writerow([r["kind"], r["dup_key"], r["keeper_ids"], r["method"], date, r["status"], r["detail"]])
    temp.replace(REPORT_PATH)


# --------------------------------------------------------------------------- moving to Diane

def parse_ids(text):
    return [int(p) for p in str(text).split(",") if p.strip()]


def stored_row(con, msg_id):
    return con.execute("SELECT fid,size,grouped_id FROM files WHERE msg_id=?", (msg_id,)).fetchone()


async def validate_group(client, pvt, con, ids, keeper_ids):
    """Re-check everything against live Telegram state right before touching it."""
    async def fetch(wanted):
        got = await tg_idempotent(client, lambda: client.get_messages(pvt, ids=wanted))
        return got if isinstance(got, list) else [got]

    msgs = await fetch(ids)
    if len(msgs) != len(ids) or any(m is None for m in msgs):
        return "changed", "A duplicate message is no longer in PVT.", None
    keepers = await fetch(keeper_ids)
    if len(keepers) != len(keeper_ids) or any(m is None for m in keepers):
        return "keeper_missing", "The kept (oldest) copy is no longer in PVT; refusing to move.", None
    for m in msgs + keepers:
        live, row = describe(m), stored_row(con, m.id)
        if live is None or row is None or live["fid"] != row["fid"] or live["size"] != row["size"]:
            return "changed", f"Message {m.id} changed since it was indexed.", None
    if any(getattr(m, "noforwards", False) for m in msgs):
        return "protected", "Telegram marks this content as protected from forwarding.", None
    gids = {getattr(m, "grouped_id", None) for m in msgs}
    if len(ids) > 1:
        gid = next(iter(gids)) if len(gids) == 1 else None
        if gid is None:
            return "changed", "Message IDs no longer form one album.", None
        around = [i for i in range(max(1, min(ids) - 10), max(ids) + 11)]
        near = await fetch(around)
        actual = sorted(m.id for m in near if m is not None and getattr(m, "grouped_id", None) == gid)
        if actual != sorted(ids):
            return "changed", f"Album membership changed (now {actual}).", None
    elif gids != {None} and None not in gids:
        # a single indexed message that is now part of an album: do not split it
        if stored_row(con, ids[0])["grouped_id"] is None:
            return "changed", "Message became part of an album; not splitting it.", None
    return "ok", "", sorted(msgs, key=lambda m: m.id)


async def forward_group(client, diane, con, key, msgs):
    """Forward once. Returns target ids or None. Uncertain results are never retried."""
    payload = msgs if len(msgs) > 1 else msgs[0]
    while True:
        set_group_status(con, key, "forwarding_started", "Forward in progress.")
        try:
            result = await client.forward_messages(diane, payload)
        except FloodWaitError as exc:
            set_group_status(con, key, "pending", "FloodWait rejected the forward; safe to retry.")
            await sleep_checked(max(1, int(exc.seconds)) + 2)
            continue
        except RPCError as exc:
            set_group_status(con, key, "forward_failed", f"{type(exc).__name__}: {exc}")
            log.error("Forward rejected for %s: %s (PVT copy kept).", key, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            set_group_status(con, key, "forward_uncertain", f"{type(exc).__name__}: {exc}")
            log.error("Forward result UNCERTAIN for %s: %s. PVT copy kept; check Diane before retrying.", key, exc)
            return None
        items = result if isinstance(result, list) else ([result] if result is not None else [])
        if len(items) != len(msgs) or any(i is None for i in items):
            set_group_status(con, key, "forward_uncertain", "Telegram returned an incomplete forward result.")
            log.error("Incomplete forward result for %s. PVT copy kept; check Diane.", key)
            return None
        target_ids = ",".join(str(getattr(i, "id", "")) for i in items)
        set_group_status(con, key, "forwarded", "Forward confirmed; PVT deletion pending.", target_ids)
        return target_ids


async def delete_from_pvt(client, pvt, con, key, ids):
    try:
        await tg_idempotent(client, lambda: client.delete_messages(pvt, ids, revoke=True))
    except StopRequested:
        raise
    except Exception as exc:  # noqa: BLE001
        set_group_status(con, key, "delete_failed", f"Forwarded to Diane, but PVT deletion failed: {exc}")
        log.error("Forwarded %s to Diane but could not delete it from PVT: %s", key, exc)
        return False
    con.executemany("DELETE FROM files WHERE msg_id=?", [(i,) for i in ids])
    set_group_status(con, key, "deleted", "Forwarded to Diane and removed from PVT.")
    return True


async def move_groups(client, pvt, diane, con, max_moves):
    rows = con.execute("SELECT * FROM groups WHERE status IN "
                       "('pending','forward_failed','forwarded','delete_failed') ORDER BY first_id").fetchall()
    log.info("MOVE: %s duplicate group(s) to process.", len(rows))
    moved = 0
    for g in rows:
        check_stop()
        if max_moves and moved >= max_moves:
            log.info("MOVE: reached --max-moves %s; stopping.", max_moves)
            break
        key, ids, keeper = g["dup_key"], parse_ids(g["dup_key"]), parse_ids(g["keeper_ids"])
        if g["status"] in ("forwarded", "delete_failed"):
            log.info("Resuming deletion only (already forwarded): %s", key)
            ok = await delete_from_pvt(client, pvt, con, key, ids)
            moved += 1 if ok else 0
            continue
        verdict, detail, msgs = await validate_group(client, pvt, con, ids, keeper)
        if verdict != "ok":
            set_group_status(con, key, verdict, detail)
            log.warning("Skipped %s: %s", key, detail)
            continue
        if await forward_group(client, diane, con, key, msgs) is None:
            continue
        if await delete_from_pvt(client, pvt, con, key, ids):
            moved += 1
            log.info("Moved %s %s to Diane (kept oldest %s).", g["kind"], key, g["keeper_ids"])
    return moved


# --------------------------------------------------------------------------- main

def summarize(con):
    rows = con.execute("SELECT status,COUNT(*) c FROM groups GROUP BY status").fetchall()
    log.info("Duplicate groups by status: %s", {r["status"]: r["c"] for r in rows} or "none")
    attention = con.execute("SELECT COUNT(*) FROM groups WHERE status IN "
                            "('forward_uncertain','keeper_missing','changed','protected')").fetchone()[0]
    if attention:
        log.warning("%s group(s) need review - see %s", attention, REPORT_PATH)


async def run(args):
    config = load_config()
    con = init_db()
    client = make_client(config)
    try:
        await client.start()
        me = await client.get_me()
        pvt = await resolve_peer(client, config["destination"])
        log.info("Logged in as user %s. PVT peer: %s.", me.id, get_peer_id(pvt))
        log.info("Mode: %s%s", "EXECUTE (will move duplicates to Diane)" if args.execute else "DRY RUN (no Telegram changes)",
                 " | strict full-file hashing" if args.strict else " | FAST (size+duration match, no downloads)" if args.fast else "")

        if args.rescan:
            con.execute("DELETE FROM files")
            set_meta(con, "scan_last_id", 0)
            con.commit()
            log.info("Rescan requested: file index cleared (move history kept).")

        await scan_pvt(client, pvt, con)
        files = [dict(r) for r in con.execute("SELECT * FROM files ORDER BY msg_id")]
        units = build_units(files)
        log.info("Indexed %s media messages = %s posts/albums.", len(files), len(units))

        class_of, multi = await build_classes(client, pvt, con, files, args.strict, fast=args.fast, workers=args.workers)
        groups = find_duplicates(units, class_of, multi,
                                 merged_label="size_duration_match" if args.fast else "content_hash")
        new = save_groups(con, groups)
        export_report(con)
        log.info("DUPLICATES: %s group(s) found (%s new). Oldest copies stay in PVT. Report: %s",
                 len(groups), new, REPORT_PATH)

        if args.execute:
            diane = await resolve_peer(client, diane_reference(config, args.diane_target))
            if get_peer_id(diane) == get_peer_id(pvt):
                raise SystemExit("PVT and Diane resolve to the same chat; refusing to continue.")
            log.info("Diane peer: %s.", get_peer_id(diane))
            moved = await move_groups(client, pvt, diane, con, args.max_moves)
            log.info("MOVE finished: %s group(s) moved to Diane.", moved)
        else:
            pending = con.execute("SELECT COUNT(*) FROM groups WHERE status='pending'").fetchone()[0]
            log.info("DRY RUN: %s group(s) would be moved. Review the report, then run again with --execute.", pending)
        summarize(con)
    except StopRequested as stop:
        log.warning("GRACEFUL STOP: %s Progress is saved.", stop)
    finally:
        try:
            export_report(con)
        finally:
            con.close()
            await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Find duplicates in PVT SPACE and move them to Diane.")
    parser.add_argument("--execute", action="store_true", help="Actually forward duplicates to Diane and delete them from PVT.")
    parser.add_argument("--max-moves", type=int, default=0, help="Move at most N groups (use 3 for a first live test).")
    speed = parser.add_mutually_exclusive_group()
    speed.add_argument("--strict", action="store_true", help="Hash entire files instead of samples (slower, certain).")
    speed.add_argument("--fast", action="store_true",
                       help="Do not download anything: files with exactly the same size, duration and dimensions "
                            "count as the same file (seconds instead of hours).")
    parser.add_argument("--workers", type=int, default=4, help="Files checked at the same time (1-8, default 4).")
    parser.add_argument("--rescan", action="store_true", help="Clear the PVT index and read PVT again from the start.")
    parser.add_argument("--diane-target", default=None, help="Override Diane (default: config 'diane' or the built-in ID).")
    args = parser.parse_args()
    if args.max_moves < 0:
        raise SystemExit("--max-moves must be 0 or positive.")
    if not 1 <= args.workers <= 8:
        raise SystemExit("--workers must be between 1 and 8.")
    setup_logging("pvt_dedupe")
    with SingleInstance():
        try:
            asyncio.run(run(args))
        except KeyboardInterrupt:
            print("Interrupted. Progress is saved; run the same command to resume.")


if __name__ == "__main__":
    main()
