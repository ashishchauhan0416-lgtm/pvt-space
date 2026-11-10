#!/usr/bin/env bash
# Put your saved progress (the data/ folder) back before a run.
# Source of truth = the "state" branch of this private repository. On the very first run that branch does
# not exist yet, so the data/ folder you uploaded with the code is used instead.
set -euo pipefail
if git ls-remote --exit-code --heads origin state >/dev/null 2>&1; then
  git fetch --depth=1 origin state
  rm -rf data
  git archive FETCH_HEAD data | tar -x
  echo "Progress restored from the 'state' branch: $(ls data | wc -l) file(s)."
else
  mkdir -p data
  echo "No 'state' branch yet - starting from the data folder in the repository: $(ls data | wc -l) file(s)."
fi
