#!/usr/bin/env python3
"""How much do your channels overlap?  (read-only, no Telegram, runs in seconds)

Uses the file list that measure_pvt_space_size.py already saved in data\\size_cache.sqlite3.

  py -3.11 .\\tools\\cross_source_overlap.py
  py -3.11 .\\tools\\cross_source_overlap.py --min-percent 60     (mirror threshold, default 80)

Two videos are treated as THE SAME FILE when
  A) they have the same Telegram file ID (certain), or
  B) they have exactly the same size in bytes AND the same duration in seconds (very likely the
     same file re-uploaded; two different videos matching to the byte and the second is very unlikely).
Photos and GIFs are ignored here: their sizes are too small to compare safely.

This only says what the duplicates ARE; it moves and deletes nothing.
"""

import argparse
import csv
import itertools
import sqlite3

from pvt_common import DATA_DIR, REPORT_DIR, human_size

DEFAULT_DB = DATA_DIR / "size_cache.sqlite3"
CSV_PATH = REPORT_DIR / "overlap_report.csv"
KEEP_SECONDS = 30.0


def load(db_path):
    if not db_path.exists():
        raise SystemExit(f"{db_path} not found. Run measure_pvt_space_size.py --sources first.")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        titles = {r[0]: r[1] for r in con.execute("SELECT chat_id, title FROM chats")}
        rows = con.execute("SELECT chat_id, size, fid, duration FROM media "
                           "WHERE kind='video' AND size IS NOT NULL").fetchall()
    finally:
        con.close()
    return titles, rows


def analyze(rows, exclude=()):
    """Pure function. rows = (chat_id, size, fid, duration). Returns a dict of results."""
    rows = [r for r in rows if r[0] not in exclude]
    key_of = lambda size, dur: (size, round(dur) if dur is not None else -1)
    total_files = len(rows)
    total_bytes = sum(r[1] for r in rows)

    by_fid = {}
    for chat, size, fid, dur in rows:
        if fid is not None:
            by_fid.setdefault(fid, size)
    fid_less = [r for r in rows if r[2] is None]
    unique_fid_bytes = sum(by_fid.values()) + sum(r[1] for r in fid_less)

    keys = {}                                        # key -> {chat_id: copies}
    key_over30 = {}
    for chat, size, fid, dur in rows:
        k = key_of(size, dur)
        keys.setdefault(k, {}).setdefault(chat, 0)
        keys[k][chat] += 1
        key_over30[k] = dur is None or dur > KEEP_SECONDS
    unique_bytes = sum(k[0] for k in keys)
    unique_over30 = sum(k[0] for k in keys if key_over30[k])

    within = {}                                      # chat -> (extra copies, bytes) repeated inside that chat
    per_chat_bytes = {}
    per_chat_keys = {}
    for k, chats in keys.items():
        for chat, copies in chats.items():
            per_chat_keys.setdefault(chat, {})[k] = k[0]
            per_chat_bytes[chat] = per_chat_bytes.get(chat, 0) + k[0] * copies
            if copies > 1:
                n, b = within.get(chat, (0, 0))
                within[chat] = (n + copies - 1, b + k[0] * (copies - 1))

    pairs = {}
    for k, chats in keys.items():
        if len(chats) >= 2:
            for a, b in itertools.combinations(sorted(chats), 2):
                n, byts = pairs.get((a, b), (0, 0))
                pairs[(a, b)] = (n + 1, byts + k[0])
    cross_extra_files = cross_extra_bytes = 0
    for k, chats in keys.items():
        if len(chats) >= 2:
            cross_extra_files += len(chats) - 1
            cross_extra_bytes += k[0] * (len(chats) - 1)
    return {"total_files": total_files, "total_bytes": total_bytes, "unique_fid_bytes": unique_fid_bytes,
            "unique_files": len(keys), "unique_bytes": unique_bytes, "unique_over30": unique_over30,
            "within": within, "pairs": pairs, "per_chat_unique": {c: sum(v.values()) for c, v in per_chat_keys.items()},
            "per_chat_bytes": per_chat_bytes, "cross_extra": (cross_extra_files, cross_extra_bytes)}


def mirror_percent(result, a, b):
    """Shared bytes as a percentage of the SMALLER channel's unique video bytes."""
    _, shared = result["pairs"].get((a, b), (0, 0))
    smaller = min(result["per_chat_unique"].get(a, 0), result["per_chat_unique"].get(b, 0))
    return 100.0 * shared / smaller if smaller else 0.0


def report(titles, result, min_percent):
    name = lambda c: titles.get(c, str(c))[:30]
    r = result
    print("\n================ CHANNEL OVERLAP (videos only) ================")
    print(f"Files measured:                      {r['total_files']:>9,}   {human_size(r['total_bytes'])}")
    print(f"After removing repeats (best guess): {r['unique_files']:>9,}   {human_size(r['unique_bytes'])}"
          f"   <- what you really need")
    saved = r["total_bytes"] - r["unique_bytes"]
    print(f"Repeated space:                                {human_size(saved)}"
          f"  ({100.0 * saved / r['total_bytes']:.0f}% of everything)" if r["total_bytes"] else "")
    print(f"  of which the same Telegram file ID (certain): {human_size(r['total_bytes'] - r['unique_fid_bytes'])}")
    print(f"Unique videos over {KEEP_SECONDS:.0f}s (would live in PVT): {human_size(r['unique_over30'])}")
    wf = sum(n for n, _ in r["within"].values()); wb = sum(b for _, b in r["within"].values())
    print(f"\nRepeated INSIDE the same channel: {wf:,} extra files, {human_size(wb)}")
    print(f"Repeated ACROSS channels:         {r['cross_extra'][0]:,} extra files, {human_size(r['cross_extra'][1])}")

    print("\n--- Channel pairs that share the most (by bytes) ---")
    top = sorted(r["pairs"].items(), key=lambda kv: kv[1][1], reverse=True)[:15]
    if not top:
        print("  No file is shared between channels.")
    for (a, b), (n, byts) in top:
        pct = mirror_percent(r, a, b)
        flag = "  <-- MIRROR" if pct >= min_percent else ""
        print(f"  {name(a):<30} + {name(b):<30} {n:>6,} files {human_size(byts):>12}  {pct:5.0f}% of the smaller{flag}")
    print("\n--- Per channel: size, unique inside itself, repeats inside itself ---")
    for chat in sorted(r["per_chat_bytes"], key=lambda c: r["per_chat_bytes"][c], reverse=True):
        n, b = r["within"].get(chat, (0, 0))
        print(f"  {name(chat):<30} {human_size(r['per_chat_bytes'][chat]):>12}  unique {human_size(r['per_chat_unique'][chat]):>12}"
              f"  repeats {n:>6,} files {human_size(b):>12}")


def write_csv(titles, result, min_percent):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    temp = CSV_PATH.with_suffix(".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as stream:
        w = csv.writer(stream)
        w.writerow(["channel_a", "channel_a_id", "channel_b", "channel_b_id", "shared_files", "shared_bytes",
                    "shared_human", "percent_of_smaller", "mirror"])
        for (a, b), (n, byts) in sorted(result["pairs"].items(), key=lambda kv: kv[1][1], reverse=True):
            pct = mirror_percent(result, a, b)
            w.writerow([titles.get(a, a), a, titles.get(b, b), b, n, byts, human_size(byts), f"{pct:.1f}",
                        "YES" if pct >= min_percent else ""])
    temp.replace(CSV_PATH)


def main():
    parser = argparse.ArgumentParser(description="Measure how much your channels overlap (read-only, no Telegram).")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Size cache database (default: data\\size_cache.sqlite3).")
    parser.add_argument("--min-percent", type=float, default=80.0, help="Mirror threshold (default 80).")
    parser.add_argument("--exclude", action="append", type=int, default=[], metavar="CHAT_ID",
                        help="Leave a cached chat out (for example PVT or Diane, if you measured them).")
    args = parser.parse_args()
    from pathlib import Path
    titles, rows = load(Path(args.db))
    if not rows:
        raise SystemExit("No videos in the cache yet. Run measure_pvt_space_size.py --sources first.")
    result = analyze(rows, exclude=set(args.exclude))
    report(titles, result, args.min_percent)
    write_csv(titles, result, args.min_percent)
    print(f"\nPair list saved: {CSV_PATH}")
    print("Same-size-and-duration is a strong hint, not proof. The pipeline's duplicate check will confirm with "
          "file IDs and a content sample before it skips anything.")


if __name__ == "__main__":
    main()
