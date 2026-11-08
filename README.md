<div align="center">

# PVT SPACE

### A Windows-first Telegram media archiver, router, and duplicate manager

Organise Telegram media with configurable routing rules, album-aware handling, resumable duplicate scans, and optional idle-based automation.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D4?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![Telegram](https://img.shields.io/badge/API-Telethon-26A5E4?logo=telegram&logoColor=white)](https://docs.telethon.dev/)
[![Status](https://img.shields.io/badge/Status-Personal%20project-orange)](#project-status)

</div>

PVT SPACE is a Python project for people who manage large Telegram media archives. It uses a Telegram **user session** through [Telethon](https://docs.telethon.dev/) to inspect source chats, classify messages, and route content to configured destinations. It also includes utilities for duplicate detection, archive-size reporting, chat-ID lookup, and Windows idle automation.

> **Important:** This is a personal project shared as a reusable starting point. Review the configuration and run a dry-run before enabling any operation that forwards or deletes Telegram messages. It is not affiliated with Telegram.

## Highlights

- **Rule-based media routing** — classify photos, GIFs, videos, albums, text posts, and other attachments.
- **Album-aware processing** — keep media groups together instead of splitting an album into individual messages.
- **Configurable destinations** — set the archive and secondary destination in your local configuration.
- **Dry-run by default** — inspect classifications before applying Telegram changes.
- **Resumable duplicate scanning** — compare standalone media, complete albums, and text-only posts; persist scan progress in SQLite.
- **Approval-based duplicate moving** — review a CSV, approve selected duplicates, forward them, then remove only the approved duplicate copy from the archive.
- **Windows automation** — optional Task Scheduler scripts can start the pipeline after an idle period and request a graceful stop when activity resumes.
- **Utility scripts** — measure media sizes and find exact Telegram channel IDs.

## How message routing works

The main pipeline (`telegram_unified_pipeline.py`) applies the following policy:

| Message type | Destination |
|---|---|
| Standalone photo | Secondary destination |
| Photo-only album | Secondary destination, as one album |
| GIF / animated GIF | Secondary destination |
| Text-only message | Secondary destination |
| Standalone video up to and including 30 seconds | Secondary destination |
| Standalone video longer than 30 seconds | Archive destination |
| Video with unknown duration | Archive destination |
| Album with only photos and short videos | Secondary destination, as one album |
| Album containing any long or unknown-duration video | Archive destination, as one album |
| Other documents and attachments | Archive destination |

The source channel list is supplied locally in `channels.txt`. The existing archive backlog can also be processed by the pipeline's cleanup phase. **That cleanup phase can forward and delete eligible messages from the archive when `--execute` is enabled.** Use `--skip-pvt-cleanup` when you only want to route new source-chat messages.

## Project layout

```text
PVT SPACE/
├── telegram_unified_pipeline.py           # Main router and archive processor
├── telegram_unified_pipeline_idle_ready.py # Graceful-stop compatible pipeline copy
├── config.example.json                    # Safe configuration template
├── channels.example.txt                   # Example source-chat list
├── requirements.txt                       # Python dependencies
├── README.md
├── README_IDLE_AUTOSTOP.md                # Idle automation guide
├── size.py                                # Archive-size utility
├── automation/
│   ├── pvt_space_idle_controller.ps1
│   └── run_pvt_pipeline_scheduled.ps1
├── tools/
│   ├── measure_pvt_space_size.py
│   ├── print_exact_channel_id.py
│   ├── scan_pvt_duplicates_resumable.py
│   └── scan_pvt_duplicates_with_albums.py # Earlier, non-resumable album scanner
├── install_pvt_space_idle_task_v3.ps1
├── install_pvt_space_scheduler_v2.ps1
├── uninstall_pvt_space_idle_task_v3.ps1
└── uninstall_pvt_space_scheduler_v2.ps1
```

Runtime files are created locally and are intentionally not included in this repository:

- `config.json` — your Telegram API credentials and destination configuration.
- `channels.txt` — your private source-chat list.
- `telegram_user.session` — your Telegram login session.
- SQLite databases, logs, and generated reports.

## Requirements

- Windows 10 or Windows 11
- Python 3.11 or newer
- A Telegram account with access to the chats you want to process
- Telegram API credentials from [my.telegram.org](https://my.telegram.org/)
- An internet connection

## Quick start

### 1. Download the project

Download the repository ZIP from GitHub and extract it to a folder such as:

```text
%USERPROFILE%\Music\PVT SPACE
```

Open PowerShell in that folder:

```powershell
Set-Location "$HOME\Music\PVT SPACE"
```

### 2. Install dependencies

```powershell
py -3.11 -m pip install -r .\requirements.txt
```

### 3. Create your private configuration

Copy the template and edit it locally:

```powershell
Copy-Item .\config.example.json .\config.json
notepad .\config.json
```

Fill in your own values:

| Key | Meaning |
|---|---|
| `api_id` | Telegram API ID from `my.telegram.org` |
| `api_hash` | Telegram API hash from `my.telegram.org` |
| `destination` | Your archive chat name, username, or `id:<marked-id>` |
| `diane_destination` | Your secondary destination name, username, or `id:<marked-id>` |

`diane_destination` is used by the scripts as the default secondary target. You can override it for an individual run with `--diane-target`.

### 4. Create your source-chat list

```powershell
Copy-Item .\channels.example.txt .\channels.txt
notepad .\channels.txt
```

Add one source reference per line. Examples:

```text
@public_channel
id:-1001234567890
```

Use `#` at the beginning of a line for a comment. Do not publish your actual `channels.txt`.

### 5. Start the pipeline safely

First inspect the available options:

```powershell
py -3.11 .\telegram_unified_pipeline.py --help
```

Run a small dry-run preview (no Telegram messages are changed):

```powershell
py -3.11 .\telegram_unified_pipeline.py --limit 20
```

When the classifications and destinations are correct, process source chats **without cleaning the existing archive backlog**:

```powershell
py -3.11 .\telegram_unified_pipeline.py --execute --skip-pvt-cleanup
```

To also run the archive cleanup phase, omit `--skip-pvt-cleanup` only after reviewing the routing rules and understanding that eligible archive messages may be forwarded and deleted.

## Duplicate detection and approved moving

The recommended duplicate tool is the resumable scanner:

```powershell
py -3.11 .\tools\scan_pvt_duplicates_resumable.py --scan
```

It scans the configured archive chat and writes:

```text
reports\pvt_duplicate_report.csv
```

The scan is read-only. It saves progress to a local SQLite database and can resume after common connection interruptions. Run the same `--scan` command again to resume. Use `--fresh-scan` only when you intentionally want to rebuild the scan index:

```powershell
py -3.11 .\tools\scan_pvt_duplicates_resumable.py --scan --fresh-scan
```

Review the CSV carefully. Change `APPROVE` to `YES` only for duplicate rows you want moved. Then run:

```powershell
py -3.11 .\tools\scan_pvt_duplicates_resumable.py --move-approved
```

The mover revalidates approved items, forwards a complete approved message or album to the secondary destination, and only then attempts to delete that duplicate copy from the archive. The oldest matching copy is retained. If Telegram's forwarding result is uncertain, the tool leaves the source in place for manual review.

**Do not run the main pipeline and a duplicate utility at the same time.** They use the same Telegram session.

## Other utilities

### Measure archive media size

```powershell
py -3.11 .\tools\measure_pvt_space_size.py
```

The utility reads Telegram-reported media sizes. It does not download or alter media. By default it measures the archive configured in `config.json`.

### Find an exact channel ID

```powershell
py -3.11 .\tools\print_exact_channel_id.py "part of channel name"
```

The account must already have access to the chat. Use the full marked ID in `channels.txt` when a private channel has no username or public link.

## Optional Windows idle automation

The repository includes PowerShell installers and a controller for an optional single idle-based scheduled task. The controller can start the pipeline after a configured idle period and request a cooperative stop when keyboard or mouse activity resumes.

Read [`README_IDLE_AUTOSTOP.md`](README_IDLE_AUTOSTOP.md) before installing it. Review the installer's actions and paths first. Task Scheduler automation is Windows-specific and should be tested on a small archive before relying on it.

## Safety and privacy

- Never commit `config.json`, `channels.txt`, `telegram_user.session`, login codes, API hashes, or personal chat exports.
- The `.gitignore` file excludes common credentials, session files, local databases, logs, reports, and channel lists. Still inspect `git status` before any manual Git upload.
- Treat the Telegram session file like a password: anyone who obtains it may gain access to the Telegram account.
- Run one Telethon process at a time because project scripts share the same session file.
- Keep local SQLite progress databases between runs; deleting them can lose checkpoints and cause items to be processed again.
- Start with a dry run and a small `--limit` before enabling `--execute`.
- Back up important Telegram data before using any operation that deletes messages.

## Troubleshooting

| Problem | What to check |
|---|---|
| `config.json` not found | Copy `config.example.json` to `config.json` in the project root. |
| `channels.txt` not found | Copy `channels.example.txt` and add your source chats. |
| Chat cannot be resolved | Confirm the logged-in Telegram account has joined it; use `id:<marked-id>` for private chats. |
| Telegram connection drops | Check the log, then rerun the resumable duplicate scan with the same command. |
| FloodWait | Respect Telegram's requested wait; do not start another client in parallel. |
| SQLite database locked | Stop other project processes before restarting. Do not delete the database as a shortcut. |
| Duplicate report is empty or missing | Confirm the scan completed; an interrupted scan may still be in progress and should be resumed. |

## Project status

This repository is shared as a practical personal project and may require adaptation for your Telegram chats, Windows setup, and routing preferences. It is not a hosted service, and no guarantee is made that Telegram API behavior or every script will remain unchanged over time. Test changes on non-critical data first.

## Contributing

Bug reports and improvements are welcome. Before opening an issue, remove personal chat IDs, API credentials, session data, logs containing private messages, and other account-specific information from screenshots and diagnostic output.

## License

No license has been added yet. Until a license is selected and included, the code is publicly viewable but is not granted an open-source reuse license.
