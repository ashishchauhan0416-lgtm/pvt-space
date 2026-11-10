# Run PVT SPACE free in the cloud (GitHub Actions)

> **Use a PRIVATE repository for cloud runs, never this public one.** Cloud mode needs your progress databases and
> secrets (API hash, login session). Copy `cloud-example/telegrame.yml.example` to `.github/workflows/telegrame.yml`
> and the two `.sh` files to `cloud/` in your own private repository, and add your secrets there.


Cost: **0**. No credit card. GitHub gives private repositories 2,000 free minutes a month. TELEGRAME runs once a day
for at most 60 minutes (about 30 hours a month), so it stays inside the free allowance. When the backlog is finished,
a daily run takes only a few minutes.

Why this works: forwarding happens inside Telegram's servers. The cloud machine never downloads your videos; it only
tells Telegram what to forward.

## Before you start
- Let the laptop job finish (the duplicate move) and close every TELEGRAME window. **Never run it on the laptop and in the cloud at the same time.**
- Adding the secrets in step 6 is the "on" switch. The cloud run uses `--execute` (real forwarding). Do step 6 only when you are happy with the
  rules (short videos, photos, GIFs and text from sources go to Diane).

## Steps
1. **Make a free GitHub account** at github.com (Sign up). No card needed.
2. **Create a repository:** green "New" button, name it `telegrame`, choose **Private** (very important), click Create.
3. **Upload the code.** Unzip `telegrame-cloud.zip`. In your repository click *Add file > Upload files* and drag in everything from inside
   the unzipped folder (`tools`, `pipeline`, `cloud`, `.github`, `requirements.txt`). Click *Commit changes*.
   If the `.github` folder did not upload: *Add file > Create new file*, type `.github/workflows/telegrame.yml` as the name, paste the contents of that file, commit.
4. **Upload your progress.** This is what stops it repeating work. Run this in PowerShell (it only copies):
   ```powershell
   $d = "$HOME\Desktop\data"; New-Item -ItemType Directory -Force $d | Out-Null
   foreach ($f in 'archive_progress','standalone_image_move','file_index') {
     Copy-Item "$HOME\pvt-space\data\$f.sqlite3" $d -ErrorAction SilentlyContinue }
   ```
   Then in the repository: *Add file > Upload files*, drag the folder `data` from your Desktop into the page, *Commit changes*.
5. **Make the cloud login.** Copy `tools\make_cloud_session.py` and `tools\pvt_common.py` into `$HOME\pvt-space\tools\`, then:
   ```powershell
   Set-Location "$HOME\pvt-space"
   py -3.11 .\tools\make_cloud_session.py
   ```
   Type your phone number, the code Telegram sends, and your 2-step password if you have one. It prints one long line. Copy it (do not save it in a file).
6. **Add 6 secrets.** Repository *Settings > Secrets and variables > Actions > New repository secret*. Add these names exactly:

   | Name | Value |
   |---|---|
   | `TG_API_ID` | `api_id` from `config\config.json` |
   | `TG_API_HASH` | `api_hash` from `config\config.json` |
   | `TG_SESSION` | the long line from step 5 |
   | `TG_DESTINATION` | `id:-100XXXXXXXXXX` |
   | `TG_DIANE` | `id:XXXXXXXXXX` |
   | `TG_CHANNELS` | everything inside `config\channels.txt` |

   Shortcut to copy the channel list: `Get-Content "$HOME\pvt-space\config\channels.txt" -Raw | Set-Clipboard`, then paste.
7. **First run by hand.** Open the repository's **Actions** tab (click the green "I understand my workflows, go ahead and enable them" button if it appears),
   click **TELEGRAME** on the left, then **Run workflow > Run workflow**. Click the run to watch it. It stops by itself after 60 minutes.
8. **Check it.** The run should end with a green tick. Open the *Run TELEGRAME* step: you should see "Logged in as Telegram user ID" and the
   per-source numbers. New posts should appear in PVT / Diane, and a branch named `state` now exists in your repository (that is your saved progress).
   The full log is under the run's *Artifacts > logs* for 14 days.
9. **That is all. It now runs by itself every day at 02:00 India time.** To change the time, edit the `cron` line in `.github/workflows/telegrame.yml`.
   To pause it: Actions > TELEGRAME > the three dots > *Disable workflow*.
10. **Only after two good runs**, make a backup copy of `config\`, `session\` and `data\` somewhere safe (for example a zip in Google Drive), then you may delete the folder
    from the laptop and phone. In Telegram *Settings > Devices*, end the old computer session.

## Things to know
- **Keep the repository Private** and never paste a secret anywhere else. The `TG_SESSION` line is the same as your Telegram login.
- If the session ever stops working, run step 5 again and replace `TG_SESSION`.
- The first backlog is big, so it takes days at 60 minutes a day. Press *Run workflow* again any time for an extra hour. If GitHub's free minutes run out,
  runs simply pause until next month; nothing is charged unless you add a payment method.
- Telegram sees the cloud login as a new device and may send a "new login" message. That is expected.
- I tested the scripts and code here against a simulated Telegram and a local git repository. I could not run GitHub itself, so the first run in step 7 is the real test.
  If it fails, open the failed step and send me the last 30 lines.
