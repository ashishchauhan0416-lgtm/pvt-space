# PVT SPACE — the complete beginner's guide

*A Windows tool that sorts and saves your Telegram media for you, and never saves the same thing twice.*

You do **not** need to know how to code. If you can copy and paste, you can use this guide. Every step has an example, and every step tells you what you should see on your screen.

---

## Table of contents
1. [What is this, in one minute?](#1-what-is-this-in-one-minute)
2. [Words you will see (mini dictionary)](#2-words-you-will-see-mini-dictionary)
3. [The big picture](#3-the-big-picture)
4. [The sorting rules, with examples](#4-the-sorting-rules-with-examples)
5. [Before you start: what you need](#5-before-you-start-what-you-need)
6. [Step-by-step setup](#6-step-by-step-setup)
7. [Your first run (safe, changes nothing)](#7-your-first-run-safe-changes-nothing)
8. [A small real test, then the full run](#8-a-small-real-test-then-the-full-run)
9. [Duplicates explained (with a story)](#9-duplicates-explained-with-a-story)
10. [The other tools](#10-the-other-tools)
11. [Optional: run it free in the cloud](#11-optional-run-it-free-in-the-cloud)
12. [Safety rules (read this once)](#12-safety-rules-read-this-once)
13. [Something went wrong? Troubleshooting](#13-something-went-wrong-troubleshooting)
14. [Cheat sheet](#14-cheat-sheet)

---

## 1. What is this, in one minute?

Imagine you follow many Telegram channels. They post photos, videos and files all day. You want to **keep** the good stuff in one place, and you do not want ten copies of the same video.

PVT SPACE is like a **very careful mail sorter** that works for you:

1. It looks at the new posts in the channels you chose (the *sources*).
2. It decides where each post belongs, using simple rules (for example: "long video → box A, photo → box B").
3. It sends a copy to the right box (a Telegram chat that only you can see).
4. If it already has that exact file, it **skips** it, so you never get duplicates.
5. It writes down how far it got, so if your computer shuts down, it continues from the same place next time.

It never changes or deletes anything in the source channels. It only makes copies.

> **Example.** A channel posts a 2-minute video, a photo, and the same 2-minute video again an hour later.
> PVT SPACE puts the video in box A, the photo in box B, and **ignores** the second video because it is a repeat.

---

## 2. Words you will see (mini dictionary)

| Word | What it means | Everyday comparison |
|---|---|---|
| **Telegram** | The chat app. | — |
| **Channel** | A Telegram page that posts to many followers. | A TV channel. |
| **Source** | A channel PVT SPACE reads from. | The place the mail comes from. |
| **PVT SPACE** | Your private chat/channel that gets the long videos and files. | Box A. |
| **Diane** (second chat) | Your second private chat that gets photos, GIFs, text and short videos. You can give it any name you like. | Box B. |
| **Forward** | Telegram's "send a copy of this post to another chat". | Photocopy and post it. |
| **Album** | Several photos/videos posted together as one post. | A photo book. Never torn apart. |
| **Duplicate** | The same file seen more than once. | Two identical copies of one book. |
| **File ID** | Telegram's own ID number for a file. Two posts of the same uploaded file share it. | A barcode. |
| **Session** | A saved login so you do not type the code every time. | A key card. **Keep it secret.** |
| **API ID / API hash** | Two values from Telegram that allow programs to use your account. | A library card number. **Keep secret.** |
| **Dry run** | Pretend to run. Shows what *would* happen. Changes nothing. | Rehearsal. |
| **Backlog** | Old posts not handled yet. | The pile of old mail. |
| **PowerShell** | The Windows window where you type commands. | A text-message chat with your computer. |
| **Python** | The programming language this tool is written in. | The engine. |
| **Telethon** | A helper library that lets Python talk to Telegram. | The phone line. |
| **Repository (repo)** | A project folder stored on GitHub. | An online filing cabinet. |

---

## 3. The big picture

```
   SOURCE CHANNELS                       YOUR PRIVATE CHATS
 (you only read these)

 ┌───────────────┐                      ┌────────────────────────────┐
 │ Channel one   │  long videos,        │ PVT SPACE  (Box A)         │
 │ Channel two   │ ───other files ────► │ long videos, files         │
 │ Channel three │                      └────────────────────────────┘
 │ ...           │
 │               │  photos, GIFs,       ┌────────────────────────────┐
 │               │ ──text, short ─────► │ Diane  (Box B)             │
 └───────────────┘   videos             │ photos, GIFs, text, shorts │
        │                               └────────────────────────────┘
        │
        └─► Already have this exact file?  ──►  SKIP it (no duplicate)
```

PVT SPACE also keeps a small **notebook** on your computer (database files in the `data` folder). It writes down which posts it has finished. That notebook is why it can stop and continue without repeating work.

---

## 4. The sorting rules, with examples

The "cutoff" between a short and a long video is **30 seconds**.

| What the post is | Goes to | Example |
|---|---|---|
| A video **longer than 30 seconds** | PVT SPACE | A 5-minute video |
| A video of **unknown length** | PVT SPACE (to be safe) | A video Telegram has no length for |
| A video of **30 seconds or less** | Diane | A 12-second clip |
| A single **photo** or an image file | Diane | A holiday picture |
| An album with **only photos** | Diane | 4 photos posted together |
| A **GIF** | Diane | A looping animation |
| A **text-only** post | Diane | "Meeting moved to 5 pm" |
| An album with **at least one video longer than 30 s** | The **whole album** to PVT SPACE | 3 photos + one 2-minute video |
| An album with only photos and **short** videos | The whole album to Diane | 2 photos + a 10-second clip |
| Other files (PDF, audio, zip) | PVT SPACE | A PDF book |
| Something unsupported | Skipped | A system message |

**Two golden rules**
- **Albums are never split.** The whole album goes to one place.
- **Source channels are never changed.** Nothing is deleted from them.

> **Example in action.** An album arrives with 5 photos and one 45-second video.
> Because one video is over 30 seconds, **all 6 items** go to PVT SPACE together.

---

## 5. Before you start: what you need

- A **Windows 10 or 11** computer.
- A **Telegram account** (the app on your phone is fine).
- **Python 3.11 or newer** (Step 1 below shows you how to install it).
- Internet.
- About **30 minutes** for the first setup.

You must also be a **member of the source channels** you want to read, using the same Telegram account.

> This project uses *your own Telegram account*, so only use it for chats you own or are allowed to archive, and follow Telegram's Terms of Service and the law where you live.

---

## 6. Step-by-step setup

Work **one step at a time**. After each step, check that you see what the guide says. If you do not, go to [Troubleshooting](#13-something-went-wrong-troubleshooting).

### How to open PowerShell
1. Press the **Windows key** on your keyboard.
2. Type `powershell`.
3. Press **Enter**. A blue (or black) window opens. This is where you paste commands.

> **Tip:** To paste in PowerShell, **right-click** inside the window or press **Ctrl+V**. Press **Enter** to run.

---

### Step 1 — Install Python

1. Go to **python.org/downloads** and download Python **3.11 or newer** for Windows.
2. Run the installer.
3. **Important:** on the first screen, tick **"Add python.exe to PATH"** and also keep **"py launcher"** ticked. Then click **Install Now**.

**Check it worked.** In PowerShell type:

```powershell
py -3.11 --version
```

You should see something like: `Python 3.11.9`.
If you installed a newer version (for example 3.12) and the command says it cannot find 3.11, run `py --version` instead and use `py` instead of `py -3.11` in every command of this guide.

---

### Step 2 — Get the project

**Easiest way (no extra programs):**
1. Open the project page on GitHub (`github.com/ashishchauhan0416-lgtm/pvt-space`).
2. Click the green **Code** button, then **Download ZIP**.
3. Open your **Downloads** folder, right-click the ZIP and choose **Extract All**.
4. Move the extracted folder to a simple place and rename it to `pvt-space`, for example `C:\Users\YourName\pvt-space`.

**Check it worked.** In PowerShell (change `YourName` to your Windows user name):

```powershell
Set-Location "C:\Users\YourName\pvt-space"
Get-ChildItem
```

You should see folders named `pipeline`, `tools`, `config`, `docs`, plus `README.md` and `requirements.txt`.

> `Set-Location` means "go into this folder". Every command in this guide is run **from inside that folder**.

---

### Step 3 — Install the helper (Telethon)

In PowerShell, inside the project folder:

```powershell
py -3.11 -m pip install -r requirements.txt
```

**You should see** lines such as `Successfully installed telethon-...`. That means the phone line to Telegram is ready.

---

### Step 4 — Get your Telegram API ID and API hash

Telegram gives these to you for free. They are like a username and password **for programs**.

1. Open **my.telegram.org** in your browser.
2. Type your phone number (with country code, e.g. `+91...`), then the code Telegram sends to your Telegram app.
3. Click **API development tools**.
4. Fill in the short form (App title: anything, e.g. `My archive`; Short name: e.g. `myarchive`). Leave the rest simple.
5. You will see two values: **api_id** (a number) and **api_hash** (32 letters and numbers).

> **Keep these two values private.** Never post them in chat, on GitHub or in a screenshot.

---

### Step 5 — Make your two private chats

You need two places to send copies to.

1. In Telegram, create a **private channel** called, for example, **PVT SPACE** (this is *Box A*).
2. Create a second private chat or channel called, for example, **Diane** (this is *Box B*). You can use a name you like.
3. Keep both private (do not add other people).

---

### Step 6 — Fill in the settings file (`config.json`)

1. In the `config` folder you will find `config.example.json`. It is a **sample**.
2. Make a copy of it called `config.json`. In PowerShell:

```powershell
Copy-Item config\config.example.json config\config.json
notepad config\config.json
```

3. Notepad opens. Replace the placeholder values. It looks like this:

```json
{
  "api_id": "1234567",
  "api_hash": "0123456789abcdef0123456789abcdef",
  "destination": "id:-1001234567890",
  "diane": "id:1234567890"
}
```

What each line means:

| Line | Meaning | Where it comes from |
|---|---|---|
| `api_id` | Your number from Step 4 | my.telegram.org |
| `api_hash` | Your 32-character value from Step 4 | my.telegram.org |
| `destination` | Box A = PVT SPACE | Step 7 shows how to find the ID |
| `diane` | Box B = the second chat | Step 7 shows how to find the ID |

4. Save the file (**Ctrl+S**) and close Notepad.

> The numbers above are **made-up examples**. Use your own.

---

### Step 7 — Find the exact ID of a chat

Telegram chats have ID numbers. A private channel's ID usually starts with `-100`. The tool can search for you.

The **first time** you run a tool that talks to Telegram, it asks for your **phone number**, then the **code** Telegram sends you (and your 2-step password, if you set one). After that, it saves a login in the `session` folder.

```powershell
py -3.11 .\tools\print_exact_channel_id.py "PVT SPACE"
```

**You should see** a line with the chat name and its ID, for example:

```
PVT SPACE   id:-1001234567890
```

Copy that `id:-100...` text into `config.json` as `destination`. Do the same for your second chat (search its name) and put it as `diane`.

To list **all** your chats at once (also saved as a spreadsheet file in `reports`):

```powershell
py -3.11 .\tools\print_exact_channel_id.py
```

---

### Step 8 — List the source channels (`channels.txt`)

1. Copy the sample and open it:

```powershell
Copy-Item config\channels.example.txt config\channels.txt
notepad config\channels.txt
```

2. Put **one channel per line**. Examples of what you can write:

```
# Lines starting with # are notes and are ignored.
@my_favourite_channel
https://t.me/another_public_channel
id:-1001234567890
```

- A **public** channel: use its `@username` or its `https://t.me/...` link.
- A **private** channel: use its `id:-100...` (find it with Step 7).

3. Save and close.

**Check your list:**

```powershell
py -3.11 .\tools\print_exact_channel_id.py --sources
```

Each line is reported as one of:

| Word | Meaning | What to do |
|---|---|---|
| **OK** | Reachable | Nothing. |
| **MISSING** | Your account is not a member of that chat | Join the channel in Telegram, or remove the line. |
| **PROTECTED** | The channel blocks forwarding | It will be skipped. |
| **DUPLICATE** | The same channel is listed twice | Remove one line. |

---

## 7. Your first run (safe, changes nothing)

This is the **dry run**. It only *pretends*. It is safe to run any time.

```powershell
py -3.11 .\pipeline\telegram_unified_pipeline.py
```

At the end it prints a **report**. Here is an example and how to read it:

```
My Channel | scanned=40 to_pvt=9 to_diane=7 duplicates_skipped=20 skipped=4 failures=0 uncertain=0 | newest handled ID 95089 | BACKLOG REMAINS below ID 91541
```

| Part | Meaning |
|---|---|
| `scanned=40` | It looked at 40 posts |
| `to_pvt=9` | 9 posts **would** go to PVT SPACE |
| `to_diane=7` | 7 posts **would** go to Diane |
| `duplicates_skipped=20` | 20 were repeats, so they would be skipped |
| `skipped=4` | 4 were unsupported things |
| `failures=0` | Nothing went wrong (good) |
| `uncertain=0` | Nothing is in doubt (good) |
| `BACKLOG REMAINS below ID 91541` | Older posts are still waiting for later runs |
| `backlog complete` | All old posts of that channel are done |

In a dry run you will also see lines like `Would SKIP duplicate` and the count of posts that would be forwarded. **Nothing is sent in a dry run.**

---

## 8. A small real test, then the full run

### Step 9 — Tiny live test (`--execute --limit 20`)

`--execute` means "do it for real". `--limit 20` means "only a small amount".

```powershell
py -3.11 .\pipeline\telegram_unified_pipeline.py --execute --limit 20
```

Now open Telegram and look at your **PVT SPACE** and **Diane** chats. You should see a few new forwarded posts.

> The limit is per channel and per pass, so a few dozen posts per channel can go through. That is normal.

### Step 10 — The full run

If the test looked good:

```powershell
py -3.11 .\pipeline\telegram_unified_pipeline.py --execute
```

How it works, in plain words:
- **Pass 1: new posts first.** It handles the newest posts of **every** channel first, so one huge channel cannot make the others wait.
- **Pass 2: the old pile.** Then it works through old posts from newest to oldest.
- Posts younger than **2 minutes** are left for the next run, so an album that is still being posted is never cut in half.

**You can stop any time** with **Ctrl+C**. It saves its place. Run the same command again later and it continues.

### Useful options

| Add this to the command | What it does |
|---|---|
| `--execute` | Do it for real (without it: dry run) |
| `--limit 20` | Only a small test amount |
| `--new-only` | Only new posts, skip the old pile (fast) |
| `--skip-pvt-cleanup` | Do not clean duplicates inside PVT SPACE this time |
| `--skip-sources` | Skip the source channels (only clean PVT) |
| `--list-uncertain` | Show items that are in doubt |
| `--retry-unsent` | Preview, then (with `--execute`) retry items that were never actually sent |
| `--reindex` | Rebuild the "what do I already have?" list |
| `--no-dedupe` | Turn the duplicate check off (not recommended) |

> **Only one Telegram program can run at a time.** A lock protects your login. If you see a message that another program is using the session, close the other window first.

---

## 9. Duplicates explained (with a story)

**The story.** Sam posts a funny video in *Channel One*. Later, *Channel Two* forwards the same video. Later still, *Channel One* posts it again. Without help, your PVT SPACE would fill up with three copies of the same video.

**How PVT SPACE solves it.**
Every file on Telegram has a **file ID**, like a barcode. When a post is forwarded, the barcode stays the same. So the tool:

1. keeps a list of the barcodes of everything already in PVT SPACE and Diane;
2. before forwarding, checks the new post's barcode against the list;
3. **skips** the post only if **every file** in it is already known.

> **Album example.** An album has 4 videos. 3 are already known and 1 is new. The album is **not** skipped, and it is forwarded as a whole, because albums are never split.

**Cleaning old duplicates already inside PVT SPACE.** Use the duplicate tool:

```powershell
py -3.11 .\tools\pvt_dedupe.py --fast
```

- `--fast` means "no downloading; compare exact size, length and picture size". It takes seconds.
- This is a **dry run**. It writes a report (`reports\pvt_duplicates.csv`) and changes nothing.
- The **oldest copy always stays** in PVT SPACE.

If the report looks right, do a **small test move** and then the full move:

```powershell
py -3.11 .\tools\pvt_dedupe.py --fast --execute --max-moves 3
py -3.11 .\tools\pvt_dedupe.py --fast --execute
```

"Move" means: **copy the duplicate to Diane first, wait until Telegram confirms the copy, and only then remove it from PVT SPACE.** If anything is unclear, nothing is removed.

**What it cannot see yet:** the same video **re-uploaded** by someone as a brand-new upload has a new barcode. The tool then has to compare size and length instead (that is what `--fast` does for the cleanup tool). The main pipeline's forward-time check works by file ID only.

---

## 10. The other tools

All of these are in the `tools` folder. Only `pvt_dedupe.py --execute` changes anything in Telegram.

| Tool | What it does | Example |
|---|---|---|
| `print_exact_channel_id.py` | Finds chat IDs and checks `channels.txt` | `py -3.11 .\tools\print_exact_channel_id.py --sources` |
| `measure_pvt_space_size.py` | Counts how much space your files use | `py -3.11 .\tools\measure_pvt_space_size.py --count-only` |
| `cross_source_overlap.py` | Shows how much your channels repeat each other (needs the size run first; no login needed) | `py -3.11 .\tools\cross_source_overlap.py` |
| `pvt_dedupe.py` | Finds and moves duplicates in PVT SPACE | `py -3.11 .\tools\pvt_dedupe.py --fast` |
| `make_cloud_session.py` | Makes a *separate* login for cloud use | see [section 11](#11-optional-run-it-free-in-the-cloud) |

Full details: [`docs/TOOLS.md`](docs/TOOLS.md) and [`docs/PIPELINE.md`](docs/PIPELINE.md).

---

## 11. Optional: run it free in the cloud

**Why?** So it runs every day by itself, even when your computer is off.

**How?** GitHub offers free "Actions": a small computer in the cloud that runs your program on a schedule. A private repository gets 2,000 free minutes per month, and a daily run fits comfortably. The cloud computer does not download your videos; it only tells Telegram what to forward.

> **VERY IMPORTANT: use a PRIVATE repository for this, never this public one.** Cloud mode needs your progress databases and secrets. In a public repository, everyone could see them.

The outline (details are in [`docs/README_CLOUD.md`](docs/README_CLOUD.md)):

1. Make a **private** repository on GitHub.
2. Upload the code (`pipeline`, `tools`, `requirements.txt`), your progress databases in a `data` folder, and the files from `cloud-example` (the workflow file goes to `.github/workflows/telegrame.yml` and the two `.sh` files to a `cloud` folder).
3. Create a **separate cloud login** (do **not** upload your laptop session):

```powershell
py -3.11 .\tools\make_cloud_session.py
```

   It prints one long line of text. Copy it straight into a GitHub secret. **Never paste it anywhere else.**
4. In the repository go to **Settings → Secrets and variables → Actions** and add these six secrets:

| Secret name | What to put |
|---|---|
| `TG_API_ID` | your api_id |
| `TG_API_HASH` | your api_hash |
| `TG_SESSION` | the long line from step 3 |
| `TG_DESTINATION` | `id:-100...` of PVT SPACE |
| `TG_DIANE` | `id:...` of your second chat |
| `TG_CHANNELS` | the full text of your `channels.txt` |

5. Open **Actions → your workflow → Run workflow**. Type `20` in the **limit** box for a small test. A green tick means it worked.
6. After that it runs by itself every day.

> **Never run it on your computer and in the cloud at the same time.** Two logins fighting over one account can break things.

---

## 12. Safety rules (read this once)

**Never share, post or upload:**
- `config\config.json` (has your API values)
- `session\` and any file ending in `.session` (it is **your Telegram login**)
- the `data\` folder (your private progress notebook)
- the long text printed by `make_cloud_session.py`
- screenshots that show any of the above

The included `.gitignore` file helps stop these from being uploaded to GitHub by accident, but **you** are the final check.

**How the tool protects you**
- **Dry run by default.** Nothing changes unless you add `--execute`.
- **Test small first.** Use `--limit 20` or `--max-moves 3` before a big run.
- **Databases are never reset.** New tables are only added, after an automatic dated backup in `backups\db_backups`.
- **Sources are never touched.**
- **Move = copy first, delete after Telegram confirms.**
- **Doubtful forwards are never retried automatically**, so you never get surprise duplicates.
- **A lock** allows only one Telegram program at a time.
- It slows down by itself when Telegram says "wait" (FloodWait) and reconnects if the internet drops.

**A good habit:** make a backup copy (a ZIP) of your `config`, `session` and `data` folders now and then, and keep it somewhere private like your own cloud drive.

---

## 13. Something went wrong? Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `'py' is not recognized` | Python is not installed or not on PATH | Reinstall Python and tick **Add python.exe to PATH** (Step 1) |
| `No module named 'telethon'` | Step 3 was skipped | Run `py -3.11 -m pip install -r requirements.txt` |
| `api_id / api_hash not found` | `config.json` missing or not filled | Redo Step 6 |
| `config.json is missing 'destination'` | The PVT SPACE ID is missing | Add `destination` (Step 7) |
| `config.json is missing 'diane'` | The second chat ID is missing | Add `diane` (Step 7) |
| It asks for a phone number every time | The login was not saved, or the `session` folder was deleted | Log in again; keep the `session` folder |
| `database is locked` / "another program is using the session" | Another copy of the tool is open | Close the other window and try again |
| `FloodWait` | Telegram asked to slow down | Wait; the tool waits by itself and continues |
| A channel shows **MISSING** | Your account is not in that channel | Join it in Telegram, or remove the line |
| A channel shows **PROTECTED** | Forwarding is blocked there | Nothing can be done; it is skipped |
| Dry run shows `uncertain=...` | Some items are in doubt | Run `--list-uncertain` and look at those posts in your chats by hand |
| GitHub says "Failed to add secret" | The pasted value had a hidden line break or space | Clean it first: `$s = Read-Host "Paste"` then `($s -replace '\s','') \| Set-Clipboard`, then paste into GitHub |
| GitHub Actions page says "Choose a workflow" | The workflow file is in the wrong folder | It must be exactly `.github/workflows/telegrame.yml` (note the **s**) |
| Hidden folders like `.github` did not upload | GitHub's drag-and-drop skips hidden files | Use **Add file → Create new file** and type the path |

When asking for help, copy the **last 30 lines** of the output, but **remove** anything secret first.

---

## 14. Cheat sheet

Always start with this, with your own folder:

```powershell
Set-Location "C:\Users\YourName\pvt-space"
```

| I want to… | Command |
|---|---|
| Check my channel list | `py -3.11 .\tools\print_exact_channel_id.py --sources` |
| Find a chat's ID | `py -3.11 .\tools\print_exact_channel_id.py "name"` |
| Rehearse (changes nothing) | `py -3.11 .\pipeline\telegram_unified_pipeline.py` |
| Small real test | `py -3.11 .\pipeline\telegram_unified_pipeline.py --execute --limit 20` |
| Full real run | `py -3.11 .\pipeline\telegram_unified_pipeline.py --execute` |
| Only new posts | `py -3.11 .\pipeline\telegram_unified_pipeline.py --execute --new-only` |
| Show doubtful items | `py -3.11 .\pipeline\telegram_unified_pipeline.py --list-uncertain` |
| Look for duplicates in PVT SPACE | `py -3.11 .\tools\pvt_dedupe.py --fast` |
| Move 3 duplicates (test) | `py -3.11 .\tools\pvt_dedupe.py --fast --execute --max-moves 3` |
| Move all duplicates | `py -3.11 .\tools\pvt_dedupe.py --fast --execute` |
| Quick counts of my channels | `py -3.11 .\tools\measure_pvt_space_size.py --sources --count-only` |
| Stop a running program | press **Ctrl+C** (progress is saved) |

---

### Where things live

```
pvt-space/
├─ pipeline/        the main program (telegram_unified_pipeline.py)
├─ tools/           helper programs (dedupe, find IDs, measure sizes, ...)
├─ config/          config.json and channels.txt  ← YOURS, private (created by you)
├─ session/         your saved login               ← PRIVATE, created on first login
├─ data/            progress notebooks (databases) ← PRIVATE, created automatically
├─ logs/ reports/ backups/   created automatically; also private
├─ docs/            deeper guides for the pipeline, tools and cloud use
└─ cloud-example/   templates for optional cloud runs (use in a PRIVATE repository only)
```

*You made it. Start with the dry run in section 7. Nothing bad can happen until you add `--execute`.*
