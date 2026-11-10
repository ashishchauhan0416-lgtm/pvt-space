#!/usr/bin/env bash
# Save progress (the data/ folder) after a run, even if the run was stopped or failed.
# Everything goes into ONE commit on the "state" branch that replaces the previous one, so the repository
# never grows. REMOTE_URL must be the authenticated URL of this repository.
set -euo pipefail
: "${REMOTE_URL:?REMOTE_URL is required}"
if [ ! -d data ] || [ -z "$(ls -A data 2>/dev/null)" ]; then
  echo "No data folder to save - nothing pushed."
  exit 0
fi
WORK="$(mktemp -d)"
cp -r data "$WORK/data"
cd "$WORK"
git init -q -b state
git config user.name "telegrame-bot"
git config user.email "telegrame-bot@users.noreply.github.com"
git add -A
git commit -q -m "progress $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push -q --force "$REMOTE_URL" state
echo "Progress saved to the 'state' branch."
