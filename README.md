<div align="center">

# PVT SPACE

### A Windows-first Telegram media archiver, router, and duplicate manager

Organise Telegram media with configurable routing rules, album-aware handling, resumable duplicate scans, and a safe dry-run mode.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)
![API](https://img.shields.io/badge/API-Telethon-26A5E4?logo=telegram&logoColor=white)
![Status](https://img.shields.io/badge/Status-Personal%20project-orange)

</div>

PVT SPACE is a Python project for people who manage large Telegram media archives. It uses a Telegram **user session** through [Telethon](https://docs.telethon.dev) to inspect source chats, classify messages, and route content to configured destinations. It also includes utilities for duplicate detection, archive-size reporting, chat-ID lookup, and optional free cloud runs.

> **New to this? Start here: [Beginner's guide](README_BEGINNER.md)**: every step explained in simple language with examples.

> Personal project. Use it only with accounts and chats you own or are allowed to archive, and follow Telegram's Terms of Service and your local laws.
## What it does
- **Routes** posts by rule (30-second video cutoff by default):
  - long or unknown-length videos, and albums containing one, go to the *archive* chat (PVT SPACE);
  - photos, photo-only albums, GIFs, text and short videos go to a second chat (*Diane* in the config);
  - other files (PDF, audio, archives) go to the archive chat; albums are never split.
- **Detects duplicates** by Telegram file ID before forwarding, and cleans old duplicates out of the archive
  (the oldest copy always stays; the duplicate is forwarded to the second chat first, then removed).
- **New posts first, backlog second**, with saved positions per source, so one huge channel cannot starve the others.
- **Fail-safe:** session lock, FloodWait and reconnect handling, rotating logs, stop-file support, automatic dated database backups. A forward whose outcome is uncertain is never retried automatically.

## Layout
```
pipeline/   telegram_unified_pipeline.py   the main program
tools/      pvt_common.py  pvt_dedupe.py  print_exact_channel_id.py  measure_pvt_space_size.py  cross_source_overlap.py  make_cloud_session.py
config/     config.example.json  channels.example.txt   (copy to config.json / channels.txt - never commit the real ones)
docs/       PIPELINE.md  TOOLS.md  README_CLOUD.md
cloud-example/   optional GitHub Actions template (use it only in a PRIVATE repository)
```

## Quick start (Windows PowerShell)
```powershell
git clone https://github.com/ashishchauhan0416-lgtm/pvt-space.git; Set-Location pvt-space
py -3.11 -m pip install -r requirements.txt
Copy-Item config\config.example.json config\config.json      # fill in api_id, api_hash (from my.telegram.org), destination, diane
Copy-Item config\channels.example.txt config\channels.txt    # one source per line
py -3.11 .\tools\print_exact_channel_id.py --sources         # check the sources are reachable
py -3.11 .\pipeline\telegram_unified_pipeline.py             # DRY RUN: changes nothing
py -3.11 .\pipeline\telegram_unified_pipeline.py --execute --limit 20   # small live test
```
Always dry-run first, then a small live test, then the full run. Only one Telegram program may run at a time (a lock enforces this).

## Safety and privacy
- `config/config.json`, `config/channels.txt`, `session/`, `data/`, `logs/`, `reports/`, `backups/` and `*.session`, `*.sqlite3` are in `.gitignore`. **Never publish them**: the session file is equivalent to your Telegram password.
- Existing databases are never reset; new tables are added only, after a dated backup.
- Source chats are never changed. Nothing changes in Telegram without `--execute`.

## Documentation
See [docs/PIPELINE.md](docs/PIPELINE.md) for options, [docs/TOOLS.md](docs/TOOLS.md) for the helper tools and [docs/README_CLOUD.md](docs/README_CLOUD.md) for running it free on GitHub Actions in a private repository.
