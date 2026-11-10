#!/usr/bin/env python3
"""TELEGRAME unified pipeline: source channels -> PVT or Diane, plus PVT cleanup.  (version 2)

Routing rules (30-second cutoff) are UNCHANGED from version 1:
- Standalone images/image documents, photo-only albums, GIFs, text-only messages -> Diane.
- Standalone videos <= 30 s -> Diane. Videos > 30 s or unknown duration -> PVT.
- Albums with a video > 30 s / unknown duration -> whole album to PVT; all-short video albums -> Diane.
- Other attached files (PDF, audio, archives ...) -> PVT. Unsupported objects are skipped.
- Albums are never split. Source-channel originals are never deleted.

What is new in version 2
- NEW POSTS FIRST: every source is visited twice. Pass 1 handles messages newer than the saved
  high-water mark (plus anything left pending) for ALL sources. Pass 2 works through the old backlog
  newest -> oldest. One huge channel can no longer starve the others.
- INCREMENTAL: each source remembers its position, so a normal run no longer re-reads whole histories.
  Your existing progress rows are used as-is (they become the starting position).
- PVT cleanup checks only PVT messages added since the last run (use --rescan-pvt to check everything).
- A request that provably never left the computer (connection was down) is retried instead of being
  parked as "uncertain" forever. Truly uncertain forwards are still never retried automatically.
- DUPLICATE CHECK: a file already in PVT or Diane (or forwarded earlier in the run) is never forwarded again,
  whichever channel it comes from. Matching uses Telegram's own file ID: nothing is downloaded. An album is
  skipped only when EVERY file in it is already known; otherwise it is forwarded whole.
- One Telegram process at a time (session lock), reconnect + FloodWait handling, rotating logs.
- Your databases are never reset. New tables are added only, after an automatic dated backup.

Dry-run is the default. Use --execute to make changes.
"""

import argparse
import asyncio
import logging
import mimetypes
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from telethon.errors import FloodWaitError, RPCError
from telethon.utils import get_peer_id

from pvt_common import (BACKUP_DIR, DATA_DIR, DialogIndex, SingleInstance, StopRequested, check_stop,
                        diane_reference, iter_history, iter_newest_first, load_channels, load_config,
                        make_client, media_info, reconnect, resolve_peer, setup_logging, tg_idempotent)

SOURCE_DB_PATH = DATA_DIR / "archive_progress.sqlite3"
MOVE_DB_PATH = DATA_DIR / "standalone_image_move.sqlite3"
VIDEO_KEEP_SECONDS = 30.0  # PVT only when duration is strictly greater than 30 seconds.
SETTLE_SECONDS = 120       # posts younger than this are left for the next run (albums may still be arriving)
RETRY_FAILED_AFTER = 6 * 3600
UNSENT_MARK = "Cannot send requests while disconnected"

log = logging.getLogger("pvt.pipeline")
PipelineStopRequested = StopRequested
check_stop_requested = check_stop


def is_young(message):
    date = getattr(message, "date", None)
    return bool(date) and (time.time() - date.timestamp()) < SETTLE_SECONDS


def backup_database(path, tag):
    """Dated copy of a SQLite database using SQLite's own backup API (safe while it is in use)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{Path(path).stem}_{tag}_{time.strftime('%Y%m%d_%H%M%S')}{Path(path).suffix}"
    src, dst = sqlite3.connect(path), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    log.info("Database backup saved: %s", dest)
    return dest


def _has_table(path, name):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    finally:
        con.close()


def init_source_db():
    """Open archive_progress.sqlite3. Existing rows are never touched; new tables are only ADDED."""
    SOURCE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOURCE_DB_PATH.exists() and not _has_table(SOURCE_DB_PATH, "source_state"):
        backup_database(SOURCE_DB_PATH, "before_v2")
    con = sqlite3.connect(SOURCE_DB_PATH, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            source_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (source_id, message_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS source_state (
            source_id TEXT PRIMARY KEY,
            high_water INTEGER NOT NULL,
            backlog_cursor INTEGER NOT NULL,
            backlog_done INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS status_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            old_status TEXT,
            new_status TEXT NOT NULL,
            reason TEXT,
            changed_at INTEGER NOT NULL
        )
    """)
    con.commit()
    return con


def init_move_db():
    MOVE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MOVE_DB_PATH.exists() and not _has_table(MOVE_DB_PATH, "pvt_cleanup_state"):
        backup_database(MOVE_DB_PATH, "before_v2")
    con = sqlite3.connect(MOVE_DB_PATH, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("""
        CREATE TABLE IF NOT EXISTS moves (
            source_peer_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            target_peer_id INTEGER NOT NULL,
            target_message_id INTEGER,
            status TEXT NOT NULL,
            detail TEXT DEFAULT '',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (source_peer_id, source_message_id, target_peer_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pvt_cleanup_state (
            peer_id INTEGER PRIMARY KEY,
            last_id INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    con.commit()
    return con


def get_state(con, source_id):
    row = con.execute("SELECT high_water, backlog_cursor, backlog_done FROM source_state WHERE source_id=?",
                      (str(source_id),)).fetchone()
    return {"high_water": row[0], "cursor": row[1], "done": row[2]} if row else None


def save_state(con, source_id, high_water, cursor, done):
    con.execute("""
        INSERT INTO source_state(source_id, high_water, backlog_cursor, backlog_done, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET high_water=excluded.high_water,
            backlog_cursor=excluded.backlog_cursor, backlog_done=excluded.backlog_done,
            updated_at=excluded.updated_at
    """, (str(source_id), int(high_water), int(cursor), int(done), int(time.time())))
    con.commit()


def get_cleanup_state(con, peer_id):
    row = con.execute("SELECT last_id FROM pvt_cleanup_state WHERE peer_id=?", (int(peer_id),)).fetchone()
    return row[0] if row else None


def save_cleanup_state(con, peer_id, last_id):
    con.execute("""
        INSERT INTO pvt_cleanup_state(peer_id, last_id, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(peer_id) DO UPDATE SET last_id=excluded.last_id, updated_at=excluded.updated_at
    """, (int(peer_id), int(last_id), int(time.time())))
    con.commit()


def source_status(con, source_id, message_id):
    row = con.execute(
        "SELECT status, detail FROM transfers WHERE source_id=? AND message_id=?",
        (str(source_id), int(message_id)),
    ).fetchone()
    return row


def set_source_status(con, source_id, message_id, status, detail=""):
    con.execute("""
        INSERT INTO transfers(source_id, message_id, status, detail, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_id, message_id) DO UPDATE SET
            status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at
    """, (str(source_id), int(message_id), status, str(detail)[:1000], int(time.time())))
    con.commit()


def set_source_batch(con, source_id, messages, status, detail=""):
    now = int(time.time())
    con.execute("BEGIN IMMEDIATE")
    try:
        for msg in messages:
            con.execute("""
                INSERT INTO transfers(source_id, message_id, status, detail, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, message_id) DO UPDATE SET
                    status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at
            """, (str(source_id), int(msg.id), status, str(detail)[:1000], now))
        con.commit()
    except Exception:
        con.rollback()
        raise


def get_move(con, source_id, message_id, target_id):
    return con.execute(
        "SELECT status, target_message_id FROM moves "
        "WHERE source_peer_id=? AND source_message_id=? AND target_peer_id=?",
        (int(source_id), int(message_id), int(target_id)),
    ).fetchone()


def set_move(con, source_id, message_id, target_id, status, target_message_id=None, detail=""):
    con.execute("""
        INSERT INTO moves(source_peer_id, source_message_id, target_peer_id,
                          target_message_id, status, detail, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_peer_id, source_message_id, target_peer_id) DO UPDATE SET
            target_message_id=COALESCE(excluded.target_message_id, moves.target_message_id),
            status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at
    """, (int(source_id), int(message_id), int(target_id), target_message_id,
          status, str(detail)[:1000], int(time.time())))
    con.commit()


def set_moves(con, source_id, messages, target_id, status, target_ids=None, detail=""):
    now = int(time.time())
    target_ids = target_ids or [None] * len(messages)
    con.execute("BEGIN IMMEDIATE")
    try:
        for msg, target_message_id in zip(messages, target_ids):
            con.execute("""
                INSERT INTO moves(source_peer_id, source_message_id, target_peer_id,
                                  target_message_id, status, detail, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_peer_id, source_message_id, target_peer_id) DO UPDATE SET
                    target_message_id=COALESCE(excluded.target_message_id, moves.target_message_id),
                    status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at
            """, (int(source_id), int(msg.id), int(target_id), target_message_id,
                  status, str(detail)[:1000], now))
        con.commit()
    except Exception:
        con.rollback()
        raise


def document_of(message):
    return getattr(message, "document", None)


def document_mime(message):
    doc = document_of(message)
    return (getattr(doc, "mime_type", "") or "").lower() if doc else ""


def document_attributes(message):
    doc = document_of(message)
    return list(getattr(doc, "attributes", []) or []) if doc else []


def document_filename(message):
    for attr in document_attributes(message):
        name = getattr(attr, "file_name", None)
        if name:
            return str(name)
    return ""


def is_gif_message(message):
    if getattr(message, "gif", None):
        return True
    if document_mime(message) == "image/gif" or document_filename(message).lower().endswith(".gif"):
        return True
    return any(a.__class__.__name__ == "DocumentAttributeAnimated" for a in document_attributes(message))


def is_video_message(message):
    if is_gif_message(message):
        return False
    if getattr(message, "video", None):
        return True
    if document_mime(message).startswith("video/"):
        return True
    return any(a.__class__.__name__ == "DocumentAttributeVideo" for a in document_attributes(message))


def video_duration_seconds(message):
    for attr in document_attributes(message):
        if attr.__class__.__name__ == "DocumentAttributeVideo":
            try:
                return float(attr.duration)
            except (TypeError, ValueError, AttributeError):
                pass
    video = getattr(message, "video", None)
    try:
        duration = getattr(video, "duration", None)
        if duration is not None:
            return float(duration)
    except (TypeError, ValueError):
        pass
    return None


def is_image_message(message):
    if is_video_message(message):
        return False
    if getattr(message, "photo", None) or is_gif_message(message):
        return True
    if document_mime(message).startswith("image/"):
        return True
    filename = document_filename(message)
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        return bool(guessed and guessed.startswith("image/"))
    return False


def is_text_only_message(message):
    text = getattr(message, "message", None)
    if not text or not str(text).strip():
        return False
    media = getattr(message, "media", None)
    if media is None:
        return True
    return media.__class__.__name__ == "MessageMediaWebPage"


def is_supported_other_media(message):
    """Treat non-image/video attached files as wanted and route them to PVT."""
    media = getattr(message, "media", None)
    if media is None or media.__class__.__name__ == "MessageMediaWebPage":
        return False
    # Keep polls, contacts, locations and other non-file service objects out of routing.
    if media.__class__.__name__ in {
        "MessageMediaPoll", "MessageMediaContact", "MessageMediaGeo",
        "MessageMediaVenue", "MessageMediaGame", "MessageMediaInvoice",
    }:
        return False
    return bool(getattr(message, "document", None) or getattr(message, "audio", None)
                or getattr(message, "sticker", None) or getattr(message, "voice", None))


async def wait_flood(error):
    seconds = max(1, int(error.seconds)) + 2
    log.warning("Telegram requested FloodWait=%s seconds; waiting safely.", seconds)
    remaining = seconds
    while remaining > 0:
        check_stop_requested()
        interval = min(2, remaining)
        await asyncio.sleep(interval)
        remaining -= interval
    check_stop_requested()


# --------------------------------------------------------------------------- duplicate check (file index)

FILE_INDEX_PATH = DATA_DIR / "file_index.sqlite3"
FILE_INDEX = None          # set in run(); None = duplicate check off
TARGET_LABELS = {}         # marked peer id -> "PVT" / "Diane"


class FileIndex:
    """Telegram file IDs already present in PVT or Diane (or forwarded earlier in this run).

    A forwarded message keeps its Telegram file ID, so the same video posted again - in the same
    channel or in another one - is recognised without downloading anything.
    Stored in its own database (data\\file_index.sqlite3); your progress databases are not involved.
    """

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, timeout=30)
        self.con.execute("PRAGMA busy_timeout=30000")
        self.con.execute("CREATE TABLE IF NOT EXISTS known_files (fid INTEGER PRIMARY KEY, location TEXT NOT NULL, "
                         "msg_id INTEGER, updated_at INTEGER NOT NULL)")
        self.con.execute("CREATE TABLE IF NOT EXISTS index_state (peer_id INTEGER PRIMARY KEY, last_id INTEGER NOT NULL, "
                         "updated_at INTEGER NOT NULL)")
        self.con.commit()
        self.cache = {fid: loc for fid, loc in self.con.execute("SELECT fid, location FROM known_files")}

    def where(self, fid):
        return self.cache.get(fid)

    def remember(self, fid, location, msg_id=None, persist=True):
        if fid is None or fid in self.cache:
            return
        self.cache[fid] = location
        if persist:
            self.con.execute("INSERT OR IGNORE INTO known_files(fid, location, msg_id, updated_at) VALUES (?,?,?,?)",
                             (int(fid), location, msg_id, int(time.time())))

    def commit(self):
        self.con.commit()

    def last_id(self, peer_id):
        row = self.con.execute("SELECT last_id FROM index_state WHERE peer_id=?", (int(peer_id),)).fetchone()
        return row[0] if row else 0

    def set_last_id(self, peer_id, last_id):
        self.con.execute("INSERT INTO index_state(peer_id,last_id,updated_at) VALUES (?,?,?) "
                         "ON CONFLICT(peer_id) DO UPDATE SET last_id=excluded.last_id, updated_at=excluded.updated_at",
                         (int(peer_id), int(last_id), int(time.time())))
        self.con.commit()

    def reset(self):
        self.con.execute("DELETE FROM known_files")
        self.con.execute("DELETE FROM index_state")
        self.con.commit()
        self.cache.clear()

    def close(self):
        self.con.commit()
        self.con.close()


def message_fids(messages):
    return [info["fid"] for info in (media_info(m) for m in messages) if info and info["fid"] is not None]


def known_duplicate(messages):
    """Where the files already are (text) if EVERY file in these messages is known, else None."""
    if FILE_INDEX is None:
        return None
    fids = message_fids(messages)
    if not fids:
        return None
    places = [FILE_INDEX.where(f) for f in fids]
    return None if any(place is None for place in places) else ",".join(sorted(set(places)))


def remember_forwarded(messages, location, persist=True):
    if FILE_INDEX is None:
        return
    for m in messages:
        info = media_info(m)
        if info and info["fid"] is not None:
            FILE_INDEX.remember(info["fid"], location, m.id, persist)
    if persist:
        FILE_INDEX.commit()


async def index_destination(client, peer, label, index, rescan=False):
    """Read PVT / Diane metadata (no downloads) and remember every file ID. Incremental and resumable."""
    peer_id = get_peer_id(peer)
    start = 0 if rescan else index.last_id(peer_id)
    last, seen, added = start, 0, 0
    try:
        async for msg in iter_history(client, peer, after_id=start):
            seen += 1
            info = media_info(msg)
            if info and info["fid"] is not None:
                before = len(index.cache)
                index.remember(info["fid"], label, msg.id)
                added += len(index.cache) - before
            last = msg.id
            if seen % 1000 == 0:
                index.set_last_id(peer_id, last)
                log.info("File index: %s messages read in %s ...", seen, label)
    finally:
        index.set_last_id(peer_id, last)
    log.info("File index: %s new message(s) read in %s, %s new file(s) learned.", seen, label, added)


async def forward_to_target(client, target, messages, source_con, source_id, category, dry_run):
    """Copy source messages to a chosen destination; never delete source-channel originals."""
    check_stop_requested()
    messages = sorted(messages, key=lambda m: m.id)
    if not messages:
        return "empty"
    pending = []
    uncertain = []
    for msg in messages:
        row = source_status(source_con, source_id, msg.id)
        status = row[0] if row else None
        if status in ("done", "skipped", "duplicate_skipped"):
            continue
        if status in ("routing_started", "route_uncertain", "forward_uncertain"):
            uncertain.append(msg.id)
        else:
            pending.append(msg)

    if uncertain:
        log.error("Not retrying source IDs %s automatically: a previous route may have succeeded. Check target manually.", uncertain)
        if pending:
            set_source_batch(source_con, source_id, pending, "route_uncertain",
                             "Album/message batch has mixed or uncertain prior routing state.")
        return "uncertain"
    if not pending:
        return "already-processed"
    if len(pending) != len(messages):
        log.error("Not splitting/re-routing album IDs %s because some members were already processed.",
                  [m.id for m in messages])
        set_source_batch(source_con, source_id, pending, "route_uncertain",
                         "Partial prior state in an album; manual review required.")
        return "uncertain"
    if any(getattr(m, "noforwards", False) for m in messages):
        set_source_batch(source_con, source_id, messages, "skipped", "Source content is protected from forwarding.")
        log.warning("Protected message/group skipped; IDs=%s.", [m.id for m in messages])
        return "skipped"

    duplicate_of = known_duplicate(messages)
    if duplicate_of:
        if dry_run:
            log.info("[DRY RUN] Would SKIP duplicate (already in %s): IDs=%s.", duplicate_of, [m.id for m in messages])
            return "duplicate"
        set_source_batch(source_con, source_id, messages, "duplicate_skipped",
                         f"Already in {duplicate_of}; not forwarded again.")
        log.info("Skipped duplicate (already in %s): source IDs=%s.", duplicate_of, [m.id for m in messages])
        return "duplicate"
    target_label = TARGET_LABELS.get(get_peer_id(target), str(get_peer_id(target)))

    if dry_run:
        log.info("[DRY RUN] Would route %s message(s), category=%s, target=%s, IDs=%s, captions=%s.",
                 len(messages), category, getattr(target, "title", None) or getattr(target, "first_name", None) or get_peer_id(target),
                 [m.id for m in messages], [bool(getattr(m, "message", None)) for m in messages])
        remember_forwarded(messages, target_label, persist=False)   # preview only: later repeats count as duplicates
        return "preview"

    while True:
        check_stop_requested()
        set_source_batch(source_con, source_id, messages, "routing_started",
                         f"{category}; target_peer_id={get_peer_id(target)}")
        try:
            payload = messages if len(messages) > 1 else messages[0]
            result = await client.forward_messages(target, payload)
            result_items = result if isinstance(result, list) else ([result] if result is not None else [])
            if len(result_items) != len(messages) or any(item is None for item in result_items):
                set_source_batch(source_con, source_id, messages, "route_uncertain",
                                 "Forward returned incomplete result; inspect destination before retrying.")
                log.error("Incomplete forward result for IDs=%s; source was not deleted.", [m.id for m in messages])
                return "uncertain"
            target_ids = [getattr(item, "id", None) for item in result_items]
            details = f"routed_to={get_peer_id(target)};category={category};target_message_ids={target_ids}"
            set_source_batch(source_con, source_id, messages, "done", details)
            remember_forwarded(messages, target_label)
            log.info("Routed %s message(s) (%s) to marked target %s; source IDs=%s.",
                     len(messages), category, get_peer_id(target), [m.id for m in messages])
            return "done"
        except FloodWaitError as exc:
            set_source_batch(source_con, source_id, messages, "route_pending",
                             "FloodWait rejected this attempt; safe to retry after wait.")
            await wait_flood(exc)
        except RPCError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if "CHAT_FORWARDS_RESTRICTED" in str(exc).upper():
                set_source_batch(source_con, source_id, messages, "skipped", "Source content protected.")
                log.warning("Forwarding restricted; skipped IDs=%s.", [m.id for m in messages])
                return "skipped"
            set_source_batch(source_con, source_id, messages, "failed", detail)
            log.error("Forward failed for IDs=%s: %s", [m.id for m in messages], detail)
            return "failed"
        except Exception as exc:
            if isinstance(exc, ConnectionError) and "while disconnected" in str(exc):
                # Telethon refuses to send while offline: the request provably never left this computer.
                set_source_batch(source_con, source_id, messages, "route_pending",
                                 "Connection was down before sending; safe to retry.")
                log.warning("Connection down before sending IDs=%s; reconnecting and retrying.", [m.id for m in messages])
                await reconnect(client)
                continue
            set_source_batch(source_con, source_id, messages, "route_uncertain", repr(exc))
            log.exception("Forward result uncertain for IDs=%s; inspect target before retrying.",
                          [m.id for m in messages])
            return "uncertain"


class RouteCounters:
    def __init__(self):
        self.scanned = 0
        self.to_pvt = 0
        self.to_diane = 0
        self.skipped = 0
        self.failures = 0
        self.uncertain = 0
        self.duplicates = 0


async def route_source_group(client, pvt, diane, messages, source_con, source_id, dry_run, counters):
    check_stop_requested()
    messages = sorted(messages, key=lambda m: m.id)
    if not messages:
        return
    if any(getattr(m, "action", None) is not None for m in messages):
        counters.skipped += len(messages)
        return

    videos = [m for m in messages if is_video_message(m)]
    images_or_gifs = [m for m in messages if is_image_message(m)]
    is_album = len(messages) > 1

    if is_album:
        if videos:
            durations = [video_duration_seconds(m) for m in videos]
            has_long_or_unknown = any(d is None or d > VIDEO_KEEP_SECONDS for d in durations)
            only_visual_media = all(is_video_message(m) or is_image_message(m) for m in messages)
            if has_long_or_unknown or not only_visual_media:
                target, category = pvt, "video_album_pvt"
            else:
                target, category = diane, "short_video_album_diane"
        elif len(images_or_gifs) == len(messages):
            target, category = diane, "photo_only_album_diane"
        elif all(is_supported_other_media(m) for m in messages):
            target, category = pvt, "other_file_album_pvt"
        else:
            counters.skipped += len(messages)
            log.info("Skipping unsupported album IDs=%s.", [m.id for m in messages])
            return
    else:
        msg = messages[0]
        if getattr(msg, "noforwards", False):
            set_source_status(source_con, source_id, msg.id, "skipped", "Source content is protected.")
            counters.skipped += 1
            return
        if is_gif_message(msg):
            target, category = diane, "gif_diane"
        elif is_video_message(msg):
            duration = video_duration_seconds(msg)
            if duration is not None and duration <= VIDEO_KEEP_SECONDS:
                target, category = diane, "short_video_diane"
            else:
                target, category = pvt, "long_or_unknown_video_pvt"
        elif is_image_message(msg):
            target, category = diane, "standalone_image_diane"
        elif is_text_only_message(msg):
            target, category = diane, "text_only_diane"
        elif is_supported_other_media(msg):
            target, category = pvt, "other_file_pvt"
        else:
            counters.skipped += 1
            set_source_status(source_con, source_id, msg.id, "skipped", "Unsupported non-media Telegram message.")
            return

    result = await forward_to_target(client, target, messages, source_con, source_id, category, dry_run)
    if result in ("done", "preview"):
        if get_peer_id(target) == get_peer_id(pvt):
            counters.to_pvt += len(messages)
        else:
            counters.to_diane += len(messages)
    elif result == "failed":
        counters.failures += len(messages)
    elif result == "uncertain":
        counters.uncertain += len(messages)
    elif result == "skipped":
        counters.skipped += len(messages)
    elif result == "duplicate":
        counters.duplicates += len(messages)


class SourceRun:
    """Everything known about one source channel during this run."""

    def __init__(self, ref, source, source_id, name, state, con, persist):
        self.ref, self.source, self.source_id, self.name, self.con, self.persist = ref, source, source_id, name, con, persist
        self.high_water, self.cursor, self.done = state["high_water"], state["cursor"], bool(state["done"])
        self.origin = state.get("origin", "saved")
        self.counters = RouteCounters()

    def save(self):
        if self.persist:
            save_state(self.con, self.source_id, self.high_water, self.cursor, int(self.done))

    def advance(self, phase, group):
        ids = [m.id for m in group]
        if phase == "new":
            self.high_water = max(self.high_water, max(ids))
        else:
            self.cursor = min(self.cursor, min(ids))
        self.save()


async def resilient(client, label, factory, attempts=3):
    """Re-run an idempotent step after a network drop. Progress lives in the database, so re-running is safe."""
    for attempt in range(1, attempts + 1):
        try:
            return await factory()
        except (OSError, asyncio.TimeoutError) as exc:
            if attempt == attempts:
                raise
            log.warning("Network problem during %s (%s); reconnecting (try %s/%s).", label, exc, attempt, attempts)
            await reconnect(client)


async def newest_settled_id(client, source, now=None):
    """Highest message ID that is old enough to be complete; everything above it is 'new'."""
    now = time.time() if now is None else now
    recent = await tg_idempotent(client, lambda: client.get_messages(source, limit=50))
    recent = [m for m in (recent or []) if m is not None]
    if not recent:
        return 0
    settled = [m for m in recent if m.date and now - m.date.timestamp() >= SETTLE_SECONDS]
    if not settled:
        return max(min(m.id for m in recent) - 1, 0)
    frontier = max(m.id for m in settled)
    top = next(m for m in settled if m.id == frontier)
    gid = getattr(top, "grouped_id", None)
    if gid is not None and any(m.id > frontier and getattr(m, "grouped_id", None) == gid for m in recent):
        frontier = min(m.id for m in recent if getattr(m, "grouped_id", None) == gid) - 1   # album still arriving
    return max(frontier, 0)


async def init_state(client, source, con, source_id):
    top = con.execute("SELECT MAX(message_id) FROM transfers WHERE source_id=?", (str(source_id),)).fetchone()[0]
    if top is not None:
        # Version 1 worked oldest -> newest, so everything up to its highest row was already handled.
        return {"high_water": int(top), "cursor": 0, "done": 1, "origin": "existing progress"}
    frontier = await newest_settled_id(client, source)
    return {"high_water": frontier, "cursor": frontier + 1, "done": 1 if frontier == 0 else 0, "origin": "first visit"}


async def prepare_source(client, pvt, diane, ref, con, index, persist):
    log.info("Opening source channel: %s", ref)
    try:
        source = await resolve_peer(client, ref, index)
    except Exception as exc:  # noqa: BLE001
        log.error("Cannot access source %s: %s", ref, exc)
        return None
    source_id = str(source.id)
    name = getattr(source, "title", None) or getattr(source, "username", None) or ref
    if get_peer_id(source) in (get_peer_id(pvt), get_peer_id(diane)):
        log.warning("Skipping %s because it is one of the destination chats.", name)
        return None
    if getattr(source, "noforwards", False):
        log.warning("Skipping protected source channel '%s'.", name)
        return None
    state = get_state(con, source_id)
    if state is None:
        state = await init_state(client, source, con, source_id)
        log.info("'%s': starting position from %s: new posts after ID %s; backlog %s.", name, state["origin"],
                 state["high_water"], "complete" if state["done"] else f"below ID {state['cursor']}")
    run = SourceRun(ref, source, source_id, name, state, con, persist)
    run.save()
    return run


def pending_retry_ids(con, source_id):
    cutoff = int(time.time()) - RETRY_FAILED_AFTER
    return [r[0] for r in con.execute(
        "SELECT message_id FROM transfers WHERE source_id=? AND "
        "(status='route_pending' OR (status='failed' AND updated_at<?)) ORDER BY message_id",
        (str(source_id), cutoff))]


async def retry_pending(client, pvt, diane, run, dry_run):
    ids = pending_retry_ids(run.con, run.source_id)
    if not ids:
        return
    log.info("'%s': retrying %s message(s) left pending or failed earlier.", run.name, len(ids))
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        got = await tg_idempotent(client, lambda c=chunk: client.get_messages(run.source, ids=c))
        got = got if isinstance(got, list) else [got]
        live = [m for m in got if m is not None]
        for mid in set(chunk) - {m.id for m in live}:
            if not dry_run:
                set_source_status(run.con, run.source_id, mid, "skipped", "Source message no longer exists.")
        groups, singles = defaultdict(list), []
        for m in live:
            gid = getattr(m, "grouped_id", None)
            if gid is None:
                singles.append([m])
            else:
                groups[gid].append(m)
        for batch in singles + list(groups.values()):
            await route_source_group(client, pvt, diane, batch, run.con, run.source_id, dry_run, run.counters)


async def route_stream(client, pvt, diane, run, stream, phase, dry_run, limit):
    """Route messages from `stream`, keeping albums together. True = stream fully consumed."""
    group, gid, scanned, complete = [], None, 0, True

    async def finish(batch):
        if phase == "new" and is_young(max(batch, key=lambda m: m.id)):
            return False                         # still being posted; next run will take it
        await route_source_group(client, pvt, diane, batch, run.con, run.source_id, dry_run, run.counters)
        if not dry_run:
            run.advance(phase, batch)
        return True

    async for msg in stream:
        check_stop_requested()
        mgid = getattr(msg, "grouped_id", None)
        if limit is not None and scanned >= limit and (not group or mgid != gid):
            complete = False
            break
        scanned += 1
        run.counters.scanned += 1
        if group and mgid != gid:
            if not await finish(group):
                return False
            group, gid = [], None
        if mgid is None:
            if not await finish([msg]):
                return False
        else:
            if not group:
                gid = mgid
            group.append(msg)
    if group and not await finish(group):
        return False
    return complete


async def pass_new(client, pvt, diane, run, dry_run, limit):
    await retry_pending(client, pvt, diane, run, dry_run)
    stream = iter_history(client, run.source, after_id=run.high_water)
    await route_stream(client, pvt, diane, run, stream, "new", dry_run, limit)


async def pass_backlog(client, pvt, diane, run, dry_run, limit):
    if run.done or run.cursor <= 1:
        if not run.done:
            run.done = True
            run.save()
        return
    log.info("'%s': backlog, newest to oldest, below message ID %s.", run.name, run.cursor)
    stream = iter_newest_first(client, run.source, below_id=run.cursor)
    finished = await route_stream(client, pvt, diane, run, stream, "backlog", dry_run, limit)
    if finished and not dry_run:
        run.done = True
        run.save()


async def process_sources(client, pvt, diane, channels, con, index, dry_run, limit, new_only):
    runs = []
    log.info("PHASE 2a: NEW posts first, for every source in channels.txt.")
    for ref in channels:
        check_stop_requested()
        run = await resilient(client, f"opening {ref}",
                              lambda r=ref: prepare_source(client, pvt, diane, r, con, index, not dry_run))
        if run is None:
            continue
        runs.append(run)
        await resilient(client, f"new posts of '{run.name}'",
                        lambda rn=run: pass_new(client, pvt, diane, rn, dry_run, limit))
        c = run.counters
        log.info("'%s' new posts done: scanned=%s, to_pvt=%s, to_diane=%s, duplicates=%s, skipped=%s, failures=%s, uncertain=%s.",
                 run.name, c.scanned, c.to_pvt, c.to_diane, c.duplicates, c.skipped, c.failures, c.uncertain)
    if new_only:
        log.info("Backlog skipped (--new-only).")
        return runs
    log.info("PHASE 2b: BACKLOG, newest to oldest, one source after another.")
    for run in runs:
        check_stop_requested()
        await resilient(client, f"backlog of '{run.name}'",
                        lambda rn=run: pass_backlog(client, pvt, diane, rn, dry_run, limit))
    return runs


def print_run_report(runs):
    log.info("=========== RUN REPORT ===========")
    for run in runs:
        c = run.counters
        backlog = "backlog complete" if run.done else f"BACKLOG REMAINS below ID {run.cursor}"
        log.info("%s | scanned=%s to_pvt=%s to_diane=%s duplicates_skipped=%s skipped=%s failures=%s uncertain=%s | newest handled ID %s | %s",
                 run.name, c.scanned, c.to_pvt, c.to_diane, c.duplicates, c.skipped, c.failures, c.uncertain,
                 run.high_water, backlog)
    open_backlog = [r.name for r in runs if not r.done]
    log.info("Sources processed: %s. Sources with backlog remaining: %s.", len(runs), len(open_backlog))


class CleanupCounters:
    def __init__(self):
        self.inspected = 0
        self.images = 0
        self.photo_albums = 0
        self.short_videos = 0
        self.short_video_albums = 0
        self.gifs = 0
        self.text_only = 0
        self.kept_long_video = 0
        self.kept_video_albums = 0
        self.kept_other = 0
        self.moved = 0
        self.already = 0
        self.failures = 0
        self.uncertain = 0


async def forward_pvt_then_delete(client, source, target, messages, move_con, source_id, target_id, category):
    """Forward PVT message(s) to Diane, persist result, then delete from PVT."""
    check_stop_requested()
    messages = sorted(messages, key=lambda m: m.id)
    rows = [get_move(move_con, source_id, m.id, target_id) for m in messages]
    statuses = [row[0] if row else "new" for row in rows]
    if all(status == "deleted" for status in statuses):
        return "already-deleted"
    if any(status in ("forwarding_started", "forward_uncertain") for status in statuses):
        log.error("Not retrying PVT IDs %s automatically: prior transfer may be uncertain. Check Diane first.",
                  [m.id for m in messages])
        return "uncertain"
    if not all(status in ("forwarded", "deleted") for status in statuses):
        if any(status in ("forwarded", "deleted") for status in statuses):
            log.error("Mixed checkpoint for PVT album IDs=%s; leaving it for manual review.", [m.id for m in messages])
            return "uncertain"
        while True:
            check_stop_requested()
            set_moves(move_con, source_id, messages, target_id, "forwarding_started", detail=category)
            try:
                payload = messages if len(messages) > 1 else messages[0]
                result = await client.forward_messages(target, payload)
                result_items = result if isinstance(result, list) else ([result] if result is not None else [])
                if len(result_items) != len(messages) or any(item is None for item in result_items):
                    set_moves(move_con, source_id, messages, target_id, "forward_uncertain",
                              detail="Incomplete Telegram result; inspect Diane. Source was not deleted.")
                    return "uncertain"
                target_ids = [getattr(item, "id", None) for item in result_items]
                set_moves(move_con, source_id, messages, target_id, "forwarded",
                          target_ids=target_ids, detail=category)
                log.info("Forwarded PVT item(s) to Diane (%s); IDs=%s.", category, [m.id for m in messages])
                # If activity resumed during the forward, leave the PVT copy in place.
                # The checkpoint is already "forwarded", so the next run only deletes it.
                check_stop_requested()
                break
            except PipelineStopRequested:
                # Preserve the already-written "forwarded" checkpoint. The next run
                # will resume with PVT deletion only, without forwarding again.
                raise
            except FloodWaitError as exc:
                set_moves(move_con, source_id, messages, target_id, "pending", detail="FloodWait; safe to retry.")
                await wait_flood(exc)
            except RPCError as exc:
                set_moves(move_con, source_id, messages, target_id, "forward_failed",
                          detail=f"{type(exc).__name__}: {exc}")
                log.error("Could not forward PVT IDs=%s; they remain in PVT: %s", [m.id for m in messages], exc)
                return "forward-failed"
            except Exception as exc:
                if isinstance(exc, ConnectionError) and "while disconnected" in str(exc):
                    set_moves(move_con, source_id, messages, target_id, "pending",
                              detail="Connection was down before sending; safe to retry.")
                    log.warning("Connection down before sending PVT IDs=%s; reconnecting and retrying.", [m.id for m in messages])
                    await reconnect(client)
                    continue
                set_moves(move_con, source_id, messages, target_id, "forward_uncertain", detail=repr(exc))
                log.exception("Uncertain PVT forwarding for IDs=%s; source left untouched.", [m.id for m in messages])
                return "uncertain"

    rows = [get_move(move_con, source_id, m.id, target_id) for m in messages]
    to_delete = [m for m, row in zip(messages, rows) if not row or row[0] != "deleted"]
    if not to_delete:
        return "already-deleted"
    while True:
        check_stop_requested()
        try:
            await client.delete_messages(source, [m.id for m in to_delete], revoke=True)
            set_moves(move_con, source_id, to_delete, target_id, "deleted", detail=category)
            log.info("Deleted PVT source IDs=%s after successful forwarding to Diane.", [m.id for m in to_delete])
            return "moved"
        except FloodWaitError as exc:
            await wait_flood(exc)
        except RPCError as exc:
            for msg in to_delete:
                set_move(move_con, source_id, msg.id, target_id, "forwarded",
                         detail=f"Forwarded; source deletion failed: {type(exc).__name__}: {exc}")
            log.error("Forwarded to Diane but could not delete PVT IDs=%s: %s", [m.id for m in to_delete], exc)
            return "delete-failed"
        except Exception as exc:
            for msg in to_delete:
                set_move(move_con, source_id, msg.id, target_id, "forwarded",
                         detail=f"Forwarded; source deletion failed: {exc!r}")
            log.exception("Forwarded to Diane but deletion failed for PVT IDs=%s.", [m.id for m in to_delete])
            return "delete-failed"


async def clean_pvt_group(client, pvt, diane, messages, move_con, pvt_id, diane_id,
                          dry_run, counters):
    check_stop_requested()
    messages = sorted(messages, key=lambda m: m.id)
    if not messages:
        return
    videos = [m for m in messages if is_video_message(m)]
    if len(messages) > 1:
        if videos:
            durations = [video_duration_seconds(m) for m in videos]
            if any(d is None or d > VIDEO_KEEP_SECONDS for d in durations):
                counters.kept_video_albums += 1
                log.info("Keeping PVT album IDs=%s: contains video >30s or unknown duration.",
                         [m.id for m in messages])
                return
            if all(is_video_message(m) or is_image_message(m) for m in messages):
                category = "short_video_album"
                counters.short_video_albums += 1
            else:
                counters.kept_other += len(messages)
                return
        elif all(is_image_message(m) for m in messages):
            category = "photo_only_album"
            counters.photo_albums += 1
        else:
            counters.kept_other += len(messages)
            return
    else:
        msg = messages[0]
        if is_gif_message(msg):
            category = "gif"
            counters.gifs += 1
        elif is_video_message(msg):
            duration = video_duration_seconds(msg)
            if duration is None or duration > VIDEO_KEEP_SECONDS:
                counters.kept_long_video += 1
                return
            category = "short_video"
            counters.short_videos += 1
        elif is_image_message(msg):
            category = "standalone_image"
            counters.images += 1
        elif is_text_only_message(msg):
            category = "text_only"
            counters.text_only += 1
        else:
            counters.kept_other += 1
            return

    if dry_run:
        log.info("[DRY RUN] Would move PVT %s item(s) to Diane, category=%s, IDs=%s, captions=%s.",
                 len(messages), category, [m.id for m in messages],
                 [bool(getattr(m, "message", None)) for m in messages])
        return
    result = await forward_pvt_then_delete(client, pvt, diane, messages, move_con,
                                           pvt_id, diane_id, category)
    if result == "moved":
        counters.moved += len(messages)
    elif result == "already-deleted":
        counters.already += len(messages)
    elif result in ("forward-failed", "delete-failed"):
        counters.failures += len(messages)
    elif result == "uncertain":
        counters.uncertain += len(messages)


async def cleanup_existing_pvt(client, pvt, diane, move_con, dry_run=False, limit=None, rescan=False):
    pvt_id, diane_id = get_peer_id(pvt), get_peer_id(diane)
    counters = CleanupCounters()
    start = 0 if rescan else (get_cleanup_state(move_con, pvt_id) or 0)
    checkpoint, blocked, flushed = start, False, 0
    log.info("PHASE 1: PVT cleanup into Diane (30-second threshold): %s.",
             f"checking only messages after ID {start}" if start else "checking all PVT messages")
    group, gid = [], None

    async def flush():
        nonlocal group, gid, checkpoint, blocked, flushed
        batch, group, gid = group, [], None
        if not batch:
            return True
        newest = max(batch, key=lambda m: m.id)
        if is_young(newest):
            return False
        before = counters.failures
        await clean_pvt_group(client, pvt, diane, batch, move_con, pvt_id, diane_id, dry_run, counters)
        if counters.failures > before:
            blocked = True                       # a retryable failure: keep re-checking from here next run
        if not blocked:
            checkpoint = max(checkpoint, newest.id)
            flushed += 1
            if not dry_run and flushed % 200 == 0:
                save_cleanup_state(move_con, pvt_id, checkpoint)
        return True

    stopped_young = False
    try:
        async for msg in iter_history(client, pvt, after_id=start):
            check_stop_requested()
            mgid = getattr(msg, "grouped_id", None)
            if limit is not None and counters.inspected >= limit and (not group or mgid != gid):
                break
            counters.inspected += 1
            if group and mgid != gid and not await flush():
                stopped_young = True
                break
            if mgid is None:
                group = [msg]
                if not await flush():
                    stopped_young = True
                    break
            else:
                if not group:
                    gid = mgid
                group.append(msg)
        if group and not stopped_young:
            await flush()
    finally:
        if not dry_run and checkpoint > start:
            save_cleanup_state(move_con, pvt_id, checkpoint)
    log.info("PVT cleanup complete: inspected=%s, images=%s, photo_albums=%s, short_videos=%s, "
             "short_video_albums=%s, gifs=%s, text_only=%s, kept_long_videos=%s, "
             "kept_video_albums=%s, kept_other=%s, moved=%s, already_deleted=%s, failures=%s, uncertain=%s.",
             counters.inspected, counters.images, counters.photo_albums, counters.short_videos,
             counters.short_video_albums, counters.gifs, counters.text_only,
             counters.kept_long_video, counters.kept_video_albums, counters.kept_other,
             counters.moved, counters.already, counters.failures, counters.uncertain)


# --------------------------------------------------------------------------- stuck-item tools (opt-in)

def classify_uncertain(con):
    rows = con.execute("SELECT source_id, message_id, status, detail FROM transfers "
                       "WHERE status IN ('route_uncertain','routing_started','forward_uncertain') "
                       "ORDER BY source_id, message_id").fetchall()
    unsent = [r for r in rows if UNSENT_MARK in (r[3] or "")]
    ambiguous = [r for r in rows if UNSENT_MARK not in (r[3] or "")]
    return unsent, ambiguous


def stuck_items_tool(con, execute):
    unsent, ambiguous = classify_uncertain(con)
    print(f"\nUncertain items in {SOURCE_DB_PATH.name}: {len(unsent) + len(ambiguous)}")
    print(f"  Never sent (connection was down; safe to retry): {len(unsent)}")
    print(f"  Truly uncertain (Telegram may have accepted them; check Diane/PVT yourself): {len(ambiguous)}")
    for source_id, message_id, status, detail in ambiguous[:40]:
        print(f"    source {source_id}  message {message_id}  {status}  {(detail or '')[:80]}")
    if not execute:
        print("\nPreview only. Nothing was changed. Add --execute to reset the 'never sent' items for retry.")
        return
    if not unsent:
        print("Nothing to reset.")
        return
    backup_database(SOURCE_DB_PATH, "before_retry_unsent")
    now = int(time.time())
    con.execute("BEGIN IMMEDIATE")
    try:
        for source_id, message_id, status, detail in unsent:
            con.execute("UPDATE transfers SET status='route_pending', detail=?, updated_at=? "
                        "WHERE source_id=? AND message_id=? AND status=?",
                        (f"Reset for retry: request was never sent. Was: {detail}"[:1000], now, source_id, message_id, status))
            con.execute("INSERT INTO status_audit(source_id,message_id,old_status,new_status,reason,changed_at) "
                        "VALUES (?,?,?,?,?,?)", (source_id, message_id, status, "route_pending",
                                                  "retry-unsent: connection was down before sending", now))
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"\n{len(unsent)} item(s) reset to 'route_pending'. They will be retried in the next normal run. "
          "Audit trail: table status_audit. Backup saved in backups\\db_backups.")


# --------------------------------------------------------------------------- main

async def run(args):
    global FILE_INDEX
    config = load_config()
    channels = load_channels()
    if not args.skip_sources and not channels:
        raise SystemExit("channels.txt is empty or missing; add at least one source channel.")
    source_con, move_con = init_source_db(), init_move_db()
    client = make_client(config)
    runs = []
    try:
        await client.start()
        me = await client.get_me()
        index = await DialogIndex.build(client)
        pvt = await resolve_peer(client, config["destination"], index)
        diane = await resolve_peer(client, diane_reference(config, args.diane_target), index)
        pvt_id, diane_id = get_peer_id(pvt), get_peer_id(diane)
        if pvt_id == diane_id:
            raise SystemExit("PVT and Diane resolve to the same chat; refusing to continue.")
        TARGET_LABELS.update({pvt_id: "PVT", diane_id: "Diane"})
        if args.no_dedupe:
            FILE_INDEX = None
            log.warning("Duplicate check is OFF (--no-dedupe).")
        else:
            FILE_INDEX = FileIndex(FILE_INDEX_PATH)
            if args.reindex:
                FILE_INDEX.reset()
                log.info("File index cleared; rebuilding from PVT and Diane.")
            await resilient(client, "indexing PVT", lambda: index_destination(client, pvt, "PVT", FILE_INDEX, args.reindex))
            await resilient(client, "indexing Diane", lambda: index_destination(client, diane, "Diane", FILE_INDEX, args.reindex))
            log.info("Duplicate check ON: %s files already known in PVT/Diane.", len(FILE_INDEX.cache))
        log.info("Logged in as Telegram user ID %s.", me.id)
        log.info("PVT destination: %s (marked ID %s).", getattr(pvt, "title", None) or config["destination"], pvt_id)
        log.info("Diane destination: %s (marked ID %s).",
                 getattr(diane, "first_name", None) or getattr(diane, "title", None), diane_id)
        log.info("Routing policy: videos >30 seconds -> PVT; videos <=30 seconds -> Diane; unknown-duration videos -> PVT.")
        log.info("Images/photo-only albums, GIFs and text-only messages -> Diane; other attached files -> PVT.")
        log.info("Video albums with any >30s/unknown video -> PVT intact; all-short video albums -> Diane intact.")
        if not args.execute:
            log.info("DRY RUN ONLY. No Telegram messages will be forwarded or deleted and no positions saved. Add --execute to apply.")

        if not args.skip_pvt_cleanup:
            await cleanup_existing_pvt(client, pvt, diane, move_con, dry_run=not args.execute,
                                       limit=args.limit, rescan=args.rescan_pvt)
        else:
            log.info("Skipping existing PVT cleanup by request.")

        check_stop_requested()
        if not args.skip_sources:
            runs = await process_sources(client, pvt, diane, channels, source_con, index,
                                         dry_run=not args.execute, limit=args.limit, new_only=args.new_only)
        else:
            log.info("Skipping source channel scan by request.")
        log.info("Pass complete. Source progress totals: %s",
                 dict(source_con.execute("SELECT status, COUNT(*) FROM transfers GROUP BY status").fetchall()))
    except PipelineStopRequested as stop:
        log.warning("GRACEFUL STOP: %s Progress checkpoints saved; restart on the next idle period.", stop)
    finally:
        if runs:
            print_run_report(runs)
        source_con.close()
        move_con.close()
        if FILE_INDEX is not None:
            FILE_INDEX.close()
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="TELEGRAME one-command PVT/Diane router and PVT cleaner.")
    parser.add_argument("--execute", action="store_true", help="Perform Telegram forwards/deletions. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum messages to inspect per phase and chat (testing).")
    parser.add_argument("--skip-pvt-cleanup", action="store_true", help="Only route source channels.")
    parser.add_argument("--skip-sources", action="store_true", help="Only clean PVT; do not scan channels.txt.")
    parser.add_argument("--new-only", action="store_true", help="Only new posts (skip the old backlog) - a fast run.")
    parser.add_argument("--rescan-pvt", action="store_true", help="Re-check every PVT message, not just the new ones.")
    parser.add_argument("--no-dedupe", action="store_true", help="Switch the duplicate check OFF (not recommended).")
    parser.add_argument("--reindex", action="store_true", help="Rebuild the duplicate index from PVT and Diane.")
    parser.add_argument("--list-uncertain", action="store_true", help="Show items stuck as uncertain, then exit.")
    parser.add_argument("--retry-unsent", action="store_true",
                        help="Preview (or with --execute, reset) uncertain items whose request never left this computer.")
    parser.add_argument("--diane-target", default=None, help="Diane destination (default: 'diane' from config.json).")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be a positive integer.")
    setup_logging("telegram_pipeline")
    with SingleInstance():
        if args.list_uncertain or args.retry_unsent:
            con = init_source_db()
            try:
                stuck_items_tool(con, execute=args.retry_unsent and args.execute)
            finally:
                con.close()
            return
        try:
            asyncio.run(run(args))
        except KeyboardInterrupt:
            print("Interrupted. Progress checkpoints were saved; review the log before resuming.")


if __name__ == "__main__":
    main()
