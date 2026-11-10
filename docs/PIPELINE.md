# Main pipeline (version 2)

`py -3.11 .\pipeline\telegram_unified_pipeline.py [options]` - dry run unless `--execute`.

## What changed from version 1
| Before | Now |
|---|---|
| Every run re-read each source from message 1 | Each source remembers its position; normal runs read only new posts |
| One huge channel blocked the channels after it (8 of 19 were never reached) | Pass 1 handles NEW posts of ALL sources first; pass 2 does the old backlog |
| Backlog oldest -> newest | Backlog newest -> oldest, resumable at any point |
| PVT cleanup re-read all PVT messages every run | Only PVT messages added since the last run (`--rescan-pvt` for everything) |
| A dropped connection parked messages as "uncertain" forever | A request that never left the computer is retried; truly uncertain ones are still never auto-retried |
| Chat list fetched ~21 times per run | Fetched once |
| Log grew without limit | Rotating log in `logs\telegram_pipeline.log` |
| Two programs could share the session | Session lock |

Routing rules are the same code as before.

## Duplicate check (new)
Before anything is forwarded, every file in the message or album is looked up by its **Telegram file ID** in
an index of what is already in PVT and Diane (plus whatever this run forwarded earlier). A forwarded message
keeps its file ID, so the same video posted again - in the same channel or another one - is recognised
without downloading anything. Your size report showed that file IDs alone account for 98.5% of the repeated
space.
- A message or album is skipped only if **every** file in it is already known (status `duplicate_skipped`).
- An album with even one new file is forwarded whole; albums are never split.
- A file known in Diane is not forwarded to PVT, and the other way round.
- The index lives in its own database, `data\file_index.sqlite3` (a cache; safe to delete and rebuild with
  `--reindex`). The first run reads PVT and Diane metadata once; later runs read only what is new.
- A dry run builds that index and shows "Would SKIP duplicate" lines, but changes nothing in Telegram or in
  your progress databases.
- `--no-dedupe` switches the check off. `--reindex` rebuilds the index from PVT and Diane.
- Not covered yet: the same video re-uploaded under a NEW file ID (about 61 GiB in your channels) and
  duplicate text-only posts.

## Your existing data
Existing progress is used as the starting position: for each source, everything up to its highest saved
message ID is treated as handled, and anything above it is "new". Messages left `route_pending` or `failed`
are retried automatically (failed ones after 6 hours). The database gets 3 NEW tables (`source_state`,
`status_audit`, `pvt_cleanup_state`) after a dated backup; no existing row is changed.
Tested on a copy of your real databases: all 31,969 progress rows and 2,935 move rows stayed identical.

## Options
| Option | Meaning |
|---|---|
| `--execute` | Really forward/delete and save positions (default is a dry run that saves nothing) |
| `--limit N` | At most N messages per phase and chat (testing) |
| `--new-only` | New posts only, skip the backlog (fast) |
| `--skip-pvt-cleanup` / `--skip-sources` | Run only one half |
| `--rescan-pvt` | Re-check every PVT message |
| `--no-dedupe` / `--reindex` | Duplicate check off / rebuild its index from PVT and Diane |
| `--list-uncertain` | Show items stuck as uncertain, then exit |
| `--retry-unsent` | Preview; with `--execute` reset items whose request never left the computer (backup + audit trail) |

## Items stuck as "uncertain"
`--list-uncertain` shows them. Two kinds exist: requests that provably never left the computer ("Cannot send requests
while disconnected"; `--retry-unsent --execute` resets only these, after a backup, with an audit table) and truly
uncertain forwards, which you must check by hand in the destination chats. They are never retried automatically.

## Positions and the report
A run ends with a report per source: counts, newest handled ID, and either "backlog complete" or
"BACKLOG REMAINS below ID n". Posts younger than 2 minutes are left for the next run so an album that is
still being posted is never split.
