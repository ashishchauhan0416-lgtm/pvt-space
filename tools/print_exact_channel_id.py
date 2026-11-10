#!/usr/bin/env python3
"""Find exact Telegram chat IDs and check that channels.txt is usable.  (read-only)

Usage (from the PVT SPACE folder):

  py -3.11 .\\tools\\print_exact_channel_id.py glory
        Search every chat (channels, groups, bots, people, archived) by name, @username
        or part of an ID. Prints a line you can paste straight into channels.txt.

  py -3.11 .\\tools\\print_exact_channel_id.py
        List ALL chats, write reports\\available_chats_all.csv, then check channels.txt.

  py -3.11 .\\tools\\print_exact_channel_id.py --sources
        Only check channels.txt: which sources are reachable, protected from forwarding,
        duplicated, or missing from this Telegram account.

Nothing is forwarded, downloaded or deleted. Do not run it while the pipeline is running.
"""

import argparse
import asyncio
import csv
import logging
import shutil
import time

from telethon.utils import get_peer_id

from pvt_common import (REPORT_DIR, DialogIndex, SingleInstance, diane_reference, load_channels,
                        load_config, make_client, resolve_peer, setup_logging)

log = logging.getLogger("pvt.chat-ids")
CSV_PATH = REPORT_DIR / "available_chats_all.csv"
FIELDS = ["marked_id", "channels_txt_line", "type", "name", "username", "is_bot", "is_self",
          "role", "in_channels_txt", "forwards_protected", "archived"]


def kind_of(entity, dialog_name=""):
    cls = type(entity).__name__
    if cls == "User":
        if getattr(entity, "is_self", False) or getattr(entity, "self", False):
            return "Saved Messages (your account)", "Saved Messages"
        name = dialog_name or getattr(entity, "first_name", "") or "Unknown"
        return ("Bot / private chat" if getattr(entity, "bot", False) else "Person / private chat"), name
    title = getattr(entity, "title", None) or dialog_name or "Untitled"
    if cls == "Chat":
        return "Basic group", title
    if cls == "Channel":
        if getattr(entity, "megagroup", False):
            return "Supergroup", title
        if getattr(entity, "gigagroup", False):
            return "Gigagroup", title
        return "Broadcast channel", title
    return cls, dialog_name or title


def ref_to_id(ref):
    ref = str(ref).strip()
    if ref.lower().startswith("id:"):
        try:
            return int(ref[3:].strip())
        except ValueError:
            return None
    return None


def build_rows(index, pvt_id, diane_id, source_ids):
    rows = []
    for peer_id, dialog in index.by_id.items():
        entity = dialog.entity
        kind, name = kind_of(entity, getattr(dialog, "name", "") or "")
        role = "PVT" if peer_id == pvt_id else "DIANE" if peer_id == diane_id else ""
        rows.append({
            "marked_id": peer_id,
            "channels_txt_line": f"id:{peer_id}",
            "type": kind,
            "name": name,
            "username": getattr(entity, "username", None) or "",
            "is_bot": bool(getattr(entity, "bot", False)),
            "is_self": bool(getattr(entity, "is_self", False) or getattr(entity, "self", False)),
            "role": role,
            "in_channels_txt": "YES" if peer_id in source_ids else "NO",
            "forwards_protected": "YES" if getattr(entity, "noforwards", False) else "",
            "archived": "YES" if getattr(dialog, "archived", False) else "",
        })
    rows.sort(key=lambda r: (r["type"].casefold(), r["name"].casefold()))
    return rows


def matches(row, words):
    haystack = f"{row['name']} @{row['username']}".casefold()
    digits = str(abs(row["marked_id"]))
    for word in words:
        w = word.casefold().lstrip("@")
        if w.lstrip("-").isdigit():
            if w.lstrip("-") not in digits:
                return False
        elif w not in haystack:
            return False
    return True


def write_csv(rows):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if CSV_PATH.exists():                      # never overwrite earlier data: keep a dated copy
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(CSV_PATH.stat().st_mtime))
        shutil.copy2(CSV_PATH, CSV_PATH.with_name(f"{CSV_PATH.stem}_previous_{stamp}{CSV_PATH.suffix}"))
    temp = CSV_PATH.with_suffix(".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(CSV_PATH)


def print_matches(rows, words):
    found = [r for r in rows if matches(r, words)]
    if not found:
        print(f"\nNo chat matched {' '.join(words)!r}. Check the spelling, or make sure this "
              "Telegram account has joined the chat.")
        return
    print(f"\n{len(found)} chat(s) matched. Copy the full number (never from Excel's scientific notation):\n")
    for r in found:
        print(f"Name:      {r['name']}")
        print(f"Type:      {r['type']}" + ("   [forwarding protected]" if r["forwards_protected"] else ""))
        print(f"Username:  {'@' + r['username'] if r['username'] else '(none)'}")
        print(f"marked_id: {r['marked_id']}")
        note = r["role"] or ("already in channels.txt" if r["in_channels_txt"] == "YES" else "")
        print(f"channels.txt line:  {r['channels_txt_line']}" + (f"   <- {note}" if note else ""))
        print("-")


async def check_sources(client, index, pvt_id, diane_id):
    refs = load_channels()
    print(f"\n========== channels.txt CHECK ({len(refs)} source(s)) ==========")
    if not refs:
        print("channels.txt is empty or missing.")
        return {"ok": 0, "bad": 0}
    seen, ok, bad, protected = {}, 0, 0, 0
    for ref in refs:
        try:
            entity = await resolve_peer(client, ref, index)
            peer_id = get_peer_id(entity)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"  MISSING    {ref}  ->  {exc}")
            continue
        kind, name = kind_of(entity, getattr(entity, "title", "") or "")
        flags = []
        if peer_id in (pvt_id, diane_id):
            flags.append("IS A DESTINATION - must not be a source")
        if getattr(entity, "noforwards", False):
            flags.append("forwarding PROTECTED - will be skipped")
            protected += 1
        if peer_id in seen:
            flags.append(f"DUPLICATE of line for {seen[peer_id]}")
        seen.setdefault(peer_id, ref)
        status = "WARNING " if flags else "OK      "
        ok += 0 if flags else 1
        print(f"  {status}   {ref}  |  {name}  |  {kind}" + (("  |  " + "; ".join(flags)) if flags else ""))
    print(f"\nReachable and clean: {ok}   Missing: {bad}   Protected: {protected}   Total: {len(refs)}")
    if bad:
        print("Missing = this account is not in that chat (left, kicked, or wrong ID). "
              "Re-join it, or delete the line from channels.txt.")
    return {"ok": ok, "bad": bad}


async def run(args):
    config = load_config()
    client = make_client(config)
    try:
        await client.start()
        index = await DialogIndex.build(client)
        log.info("Found %s chats in this account.", len(index.by_id))
        pvt_id = diane_id = None
        try:
            pvt_id = get_peer_id(await resolve_peer(client, config["destination"], index))
        except Exception as exc:  # noqa: BLE001
            log.warning("PVT destination from config.json could not be resolved: %s", exc)
        try:
            diane_id = get_peer_id(await resolve_peer(client, diane_reference(config), index))
        except Exception as exc:  # noqa: BLE001
            log.warning("Diane could not be resolved: %s", exc)
        source_ids = {ref_to_id(r) for r in load_channels()} - {None}
        rows = build_rows(index, pvt_id, diane_id, source_ids)

        if args.query:
            print_matches(rows, args.query)
            return
        if not args.sources:
            write_csv(rows)
            counts = {}
            for r in rows:
                counts[r["type"]] = counts.get(r["type"], 0) + 1
            print(f"\n{len(rows)} chats saved to {CSV_PATH}")
            for kind, count in sorted(counts.items()):
                print(f"  {kind}: {count}")
        await check_sources(client, index, pvt_id, diane_id)
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Find exact chat IDs and check channels.txt (read-only).")
    parser.add_argument("query", nargs="*", help="Words from the chat name, @username, or part of its ID.")
    parser.add_argument("--sources", action="store_true", help="Only check channels.txt.")
    args = parser.parse_args()
    setup_logging("chat_ids")
    with SingleInstance():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
