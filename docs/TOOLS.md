# Tools  (all read-only except pvt_dedupe.py --execute)

Run from `$HOME\pvt-space`. Stop the pipeline first: only one Telegram program at a time.

## print_exact_channel_id.py - find IDs, check channels.txt
```powershell
py -3.11 .\tools\print_exact_channel_id.py glory      # search by name / @username / part of ID
py -3.11 .\tools\print_exact_channel_id.py            # all chats -> reports\available_chats_all.csv (+ channels.txt check)
py -3.11 .\tools\print_exact_channel_id.py --sources  # only the channels.txt check
```
Reports each channels.txt line as OK, MISSING (account not in that chat), PROTECTED (forwarding blocked),
DUPLICATE, or a destination used as a source. An existing CSV is saved with a dated name before being replaced.

## measure_pvt_space_size.py - total file size
```powershell
py -3.11 .\tools\measure_pvt_space_size.py            # PVT SPACE
py -3.11 .\tools\measure_pvt_space_size.py --diane
py -3.11 .\tools\measure_pvt_space_size.py --sources  # every channel in channels.txt
py -3.11 .\tools\measure_pvt_space_size.py --all
py -3.11 .\tools\measure_pvt_space_size.py --sources --count-only   # QUICK (seconds): counts only, no sizes
py -3.11 .\tools\measure_pvt_space_size.py --chat id:-1001234567890
```
The first full run reads every message, so big channels take a while (progress is saved; Ctrl+C is safe; watch logs\measure_size.log). Size by type, videos over/under 30 s, bytes held by repeated copies; `reports\size_report.csv`. Source channels
are cached in `data\size_cache.sqlite3` so re-runs read only new posts (`--fresh` to re-read).

## cross_source_overlap.py - how much do the channels overlap?  (read-only, no Telegram)
```powershell
py -3.11 .\tools\cross_source_overlap.py                 # uses data\size_cache.sqlite3 from the size run
py -3.11 .\tools\cross_source_overlap.py --min-percent 60
```
Reports the real unique size after removing repeats, repeats inside one channel vs across channels, and
channel pairs that mirror each other (`reports\overlap_report.csv`). Same Telegram file ID = certain; same
size + duration = very likely the same file. It moves nothing, needs no login, and can run any time.

## pvt_dedupe.py - duplicates in PVT -> Diane
```powershell
py -3.11 .\tools\pvt_dedupe.py                         # dry run + reports\pvt_duplicates.csv
py -3.11 .\tools\pvt_dedupe.py --fast                  # NO downloads: seconds instead of hours
py -3.11 .\tools\pvt_dedupe.py --workers 6             # content check, 6 files at a time (default 4)
py -3.11 .\tools\pvt_dedupe.py --execute --max-moves 3 # small live test
py -3.11 .\tools\pvt_dedupe.py --execute               # move all
```
Oldest copy stays. Same Telegram file ID = certain duplicate (no download); same size/duration/dimensions =
compare three 1 MB samples (`--strict` = whole file, streamed, nothing saved). Albums are never split; a
standalone file that is also inside a kept album counts as its duplicate. "Move" = forward to Diane, confirm,
then delete from PVT. Uncertain forwards are never retried. Progress: `data\pvt_dedupe.sqlite3`.
Speed: the content check downloads three 1 MB pieces per candidate file, which can take ~16 s each on a slow link.
Checked files are saved, so Ctrl+C and re-running never repeats work. `--fast` skips downloads and treats files
with exactly the same size, duration and dimensions as the same file (report column MATCH_METHOD =
`size_duration_match`; moving a wrong match only puts a video in Diane, never loses it). `--strict` hashes whole files.
Statuses: `pending`, `deleted`, `forward_failed` (retried), `forward_uncertain` (check Diane), `keeper_missing`,
`changed`, `protected`, `not_duplicate` (a later, stricter analysis rejected it; never moved), `delete_failed` (already in Diane; deletion retried).

## Not tested against live Telegram yet
All tools and the pipeline were tested offline against a simulated Telegram. The first live checks are the
dry runs and the small `--max-moves 3` / `--limit 20` runs above.
