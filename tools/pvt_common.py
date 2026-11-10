"""Shared helpers for the PVT SPACE tools (config, peers, FloodWait/reconnect, locking, logging).

Keep this file next to the tools. It contains no secrets and no Telegram actions on its own.
"""

import asyncio
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.utils import get_peer_id

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent if TOOLS_DIR.name.casefold() in ("tools", "pipeline") else TOOLS_DIR

# TELEGRAME layout:  config\  session\  data\  logs\  reports\  backups\  tools\  pipeline\  automation\
CONFIG_DIR = ROOT / "config"
SESSION_DIR = ROOT / "session"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
REPORT_DIR = ROOT / "reports"
BACKUP_DIR = ROOT / "backups" / "db_backups"


def _first_existing(*paths):
    """Prefer the new layout; fall back to the old flat layout so nothing breaks mid-migration."""
    return next((p for p in paths if p.exists()), paths[0])


CONFIG_PATH = _first_existing(CONFIG_DIR / "config.json", ROOT / "config.json")
CHANNELS_PATH = _first_existing(CONFIG_DIR / "channels.txt", ROOT / "channels.txt")
SESSION_PATH = (ROOT / "telegram_user"
                if (ROOT / "telegram_user.session").exists() and not (SESSION_DIR / "telegram_user.session").exists()
                else SESSION_DIR / "telegram_user")
LOCK_PATH = SESSION_DIR / "telegram_session.lock"
STOP_FILE_ENV = "PVTSPACE_STOP_FILE"
MB = 1024 * 1024

log = logging.getLogger("pvt.common")


def ensure_dirs():
    for folder in (CONFIG_DIR, SESSION_DIR, DATA_DIR, LOG_DIR, REPORT_DIR, BACKUP_DIR):
        folder.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- logging

def setup_logging(log_name):
    """Console + rotating file log (max 2 MB x 3 files) under logs/<log_name>.log."""
    ensure_dirs()
    root_logger = logging.getLogger("pvt")
    if root_logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = RotatingFileHandler(LOG_DIR / f"{log_name}.log", maxBytes=2 * MB,
                                       backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream)


# --------------------------------------------------------------------------- stop / lock

class StopRequested(Exception):
    """The idle controller asked us to stop at the next safe point."""


def check_stop():
    path = os.environ.get(STOP_FILE_ENV)
    if path and Path(path).exists():
        raise StopRequested("Stop requested by idle controller.")


class SingleInstance:
    """OS file lock so two programs never share the Telegram session. Freed even after a crash."""

    def __init__(self, path=LOCK_PATH):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise SystemExit("Another PVT SPACE Telegram process is running (it holds the session lock). "
                             "Stop it first; the Telegram session must never be shared by two programs.")
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()


# --------------------------------------------------------------------------- resilience

async def sleep_checked(seconds):
    remaining = float(seconds)
    while remaining > 0:
        check_stop()
        step = min(2.0, remaining)
        await asyncio.sleep(step)
        remaining -= step


async def reconnect(client):
    try:
        await client.disconnect()
    except Exception:
        pass
    last = None
    for attempt in range(1, 9):
        delay = min(2 ** (attempt - 1), 30)
        log.warning("Reconnecting to Telegram (attempt %s/8) in %ss ...", attempt, delay)
        await sleep_checked(delay)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram session is no longer authorized; log in again.")
            await client.get_me()
            log.info("Telegram connection restored.")
            return
        except StopRequested:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                await client.disconnect()
            except Exception:
                pass
    raise RuntimeError(f"Could not reconnect after 8 attempts: {last}")


async def tg_idempotent(client, factory):
    """Run a Telegram call that is safe to repeat (reads, downloads, deletes).

    NEVER use this for forwarding: a repeated forward can create a second copy.
    """
    failures = 0
    while True:
        check_stop()
        try:
            return await factory()
        except FloodWaitError as exc:
            wait = max(1, int(exc.seconds)) + 1
            log.warning("FloodWait %ss - waiting.", wait)
            await sleep_checked(wait)
        except (OSError, asyncio.TimeoutError) as exc:
            failures += 1
            if failures > 5:
                raise
            log.warning("Network problem (%s). Reconnecting.", exc)
            await reconnect(client)


async def iter_history(client, entity, after_id=0):
    """Yield messages oldest -> newest after `after_id`, surviving FloodWait and disconnects.

    On a failure it reconnects and continues after the last message it handed out,
    so callers never see duplicates or gaps.
    """
    last = after_id
    while True:
        try:
            async for msg in client.iter_messages(entity, reverse=True, offset_id=last):
                check_stop()
                yield msg
                last = msg.id
            return
        except FloodWaitError as exc:
            wait = max(1, int(exc.seconds)) + 1
            log.warning("Telegram asked us to wait %ss (FloodWait) while reading history. "
                        "This is normal; progress is kept and reading continues afterwards.", wait)
            await sleep_checked(wait)
        except (OSError, asyncio.TimeoutError) as exc:
            log.warning("Network problem while reading history (%s); resuming after ID %s.", exc, last)
            await reconnect(client)


async def iter_newest_first(client, entity, below_id=0):
    """Yield messages newest -> oldest with ID < below_id (0 = from the very newest).

    Survives FloodWait and disconnects and continues below the last message it handed out.
    """
    last = below_id
    while True:
        try:
            async for msg in client.iter_messages(entity, offset_id=last):
                check_stop()
                yield msg
                last = msg.id
            return
        except FloodWaitError as exc:
            wait = max(1, int(exc.seconds)) + 1
            log.warning("Telegram asked us to wait %ss (FloodWait) while reading history. "
                        "This is normal; progress is kept and reading continues afterwards.", wait)
            await sleep_checked(wait)
        except (OSError, asyncio.TimeoutError) as exc:
            log.warning("Network problem while reading history (%s); resuming below ID %s.", exc, last)
            await reconnect(client)


# --------------------------------------------------------------------------- config

def load_config(need_destination=True):
    """api_id / api_hash come from config.json, or from PVTSPACE_API_ID / PVTSPACE_API_HASH if set."""
    data = {}
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    api_id = os.environ.get("PVTSPACE_API_ID") or data.get("api_id")
    api_hash = os.environ.get("PVTSPACE_API_HASH") or data.get("api_hash")
    if not api_id or not api_hash:
        raise SystemExit(f"api_id / api_hash not found. Fill in {CONFIG_PATH} (see config.example.json).")
    destination = os.environ.get("PVTSPACE_DESTINATION") or data.get("destination")
    if need_destination and not destination:
        raise SystemExit("config.json is missing 'destination' (the PVT SPACE chat).")
    if destination:
        data["destination"] = destination
    if os.environ.get("PVTSPACE_DIANE"):
        data["diane"] = os.environ["PVTSPACE_DIANE"]
    data["api_id"], data["api_hash"] = int(api_id), api_hash
    return data


def diane_reference(config, override=None):
    reference = override or config.get("diane")
    if not reference:
        raise SystemExit("config.json is missing 'diane' (the chat that receives short videos, photos and duplicates).")
    return reference


def load_channels():
    """Source references from channels.txt (one per line; blank lines and # comments ignored).

    In the cloud they come from the PVTSPACE_CHANNELS secret instead of a file.
    """
    if os.environ.get("PVTSPACE_CHANNELS"):
        lines = os.environ["PVTSPACE_CHANNELS"].splitlines()
    elif CHANNELS_PATH.exists():
        lines = CHANNELS_PATH.read_text(encoding="utf-8-sig").splitlines()
    else:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def make_client(config, **kwargs):
    """Local PC: session file. Cloud: the PVTSPACE_SESSION_STRING secret (no file needed)."""
    session_string = os.environ.get("PVTSPACE_SESSION_STRING")
    if session_string:
        from telethon.sessions import StringSession
        session = StringSession(session_string)
    else:
        session = str(SESSION_PATH)
    return TelegramClient(session, config["api_id"], config["api_hash"], **kwargs)


# --------------------------------------------------------------------------- peers

class DialogIndex:
    """Every chat the account can see, fetched ONCE (the old tools re-fetched it per lookup)."""

    def __init__(self):
        self.by_id = {}
        self.dialogs = []

    @classmethod
    async def build(cls, client):
        index = cls()
        async for dialog in client.iter_dialogs():
            try:
                peer_id = get_peer_id(dialog.entity)
            except Exception:
                continue
            if peer_id not in index.by_id:
                index.by_id[peer_id] = dialog
                index.dialogs.append(dialog)
        return index

    def find_title(self, title):
        wanted = title.casefold()
        return [d.entity for d in self.dialogs
                if getattr(d.entity, "title", None) and d.entity.title.casefold() == wanted]


async def resolve_peer(client, reference, index=None):
    """Resolve 'id:-100...', an exact title, @username or t.me link to a Telegram entity."""
    ref = str(reference).strip()
    if ref.lower().startswith("id:"):
        try:
            wanted = int(ref[3:].strip())
        except ValueError as exc:
            raise ValueError(f"Invalid reference {ref!r}; use id:-1001234567890.") from exc
        if index is not None:
            dialog = index.by_id.get(wanted)
            if dialog is not None:
                return dialog.entity
        else:
            try:
                entity = await client.get_entity(wanted)
                if get_peer_id(entity) == wanted:
                    return entity
            except (ValueError, TypeError):
                pass
            index = await DialogIndex.build(client)
            dialog = index.by_id.get(wanted)
            if dialog is not None:
                return dialog.entity
        raise ValueError(f"Chat ID {wanted} is not in your dialogs. Join/open that chat with this account first.")
    if index is None:
        index = await DialogIndex.build(client)
    matches = index.find_title(ref)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"More than one chat is named {ref!r}; use its id:<number>.")
    return await client.get_entity(ref)


# --------------------------------------------------------------------------- media + formatting

def media_info(msg):
    """Metadata of a message's photo/file (no download), or None when it has no file."""
    if getattr(msg, "action", None) is not None:
        return None
    photo, doc = getattr(msg, "photo", None), getattr(msg, "document", None)
    if not (photo or doc):
        return None
    info = getattr(msg, "file", None)
    if info is None:
        return None
    if photo:
        kind, obj = "photo", photo
    else:
        obj = doc
        mime = (getattr(doc, "mime_type", "") or "").lower()
        if getattr(msg, "gif", None) or mime == "image/gif":
            kind = "gif"
        elif getattr(msg, "sticker", None):
            kind = "sticker"
        elif getattr(msg, "video", None) or getattr(msg, "video_note", None) or mime.startswith("video/"):
            kind = "video"
        elif getattr(msg, "voice", None):
            kind = "voice"
        elif getattr(msg, "audio", None) or mime.startswith("audio/"):
            kind = "audio"
        elif mime.startswith("image/"):
            kind = "image file"
        else:
            kind = "document"
    size = getattr(info, "size", None)
    duration = getattr(info, "duration", None)
    return {
        "kind": kind,
        "fid": getattr(obj, "id", None),
        "size": size if isinstance(size, int) and size >= 0 else None,
        "duration": float(duration) if duration is not None else None,
        "width": getattr(info, "width", None) or 0,
        "height": getattr(info, "height", None) or 0,
    }


def human_size(num_bytes):
    num = float(num_bytes or 0)
    for unit, limit in (("TiB", 1024 ** 4), ("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if num >= limit:
            return f"{num / limit:,.2f} {unit}"
    return f"{int(num):,} bytes"
