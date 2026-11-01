#!/usr/bin/env bash
# Push the reader-app backend to the home server in one command.
#
# The backend imports the repo-root packages (recsys/, webnovel/, scraper.py,
# scripts/), so we sync the WHOLE repo, not just reader-app/backend/. Code only:
# library/ and data/ live on the server and are never touched (see
# scripts/deploy-exclude.txt). After syncing, restart the service on the server.
#
# The backend's env (.env at the repo root: NOVEL_JWT_SECRET, NOVEL_CORS_ORIGINS)
# ships with the code. It's git-ignored, so it only exists on this machine and
# the server; the novel-api unit reads it from ~/Novel_Project/.env.
#
# SERVER defaults to the "homeserver" ssh alias (see ~/.ssh/config: HostName,
# User, IdentityFile), so a change of the server's IP is a one-line edit there,
# not here. Override with NOVEL_SERVER if needed.
set -euo pipefail

SERVER="${NOVEL_SERVER:-homeserver}"
DEST="${NOVEL_DEST:-Novel_Project/}"          # relative to the server's home
# This script lives in scripts/ but syncs the whole repo, so resolve the root
# one level up. Getting this wrong would rsync --delete just scripts/ over the
# server and take everything else with it.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --delete would remove the server's .env if there's no local one, and the
# backend would come back without its JWT secret (every login invalidated).
if [[ ! -f "$ROOT/.env" ]]; then
  echo "No .env at $ROOT; refusing to deploy (it would delete the server's copy)." >&2
  echo "Copy it back with: scp $SERVER:${DEST%/}/.env $ROOT/.env" >&2
  exit 1
fi
chmod 600 "$ROOT/.env"

echo "Syncing code -> $SERVER:~/$DEST"
rsync -avz --delete \
  --exclude-from="$ROOT/scripts/deploy-exclude.txt" \
  "$ROOT/" "$SERVER:$DEST"

# uvicorn holds the code in memory, so new .py files only take effect after a
# restart. Do it here so a deploy is a single command. Restart=on-failure in the
# unit brings it back if the new code fails to import; we surface is-active so a
# broken deploy doesn't look successful.
echo
echo "Restarting novel-api on $SERVER"
ssh "$SERVER" 'systemctl --user restart novel-api && sleep 1 && systemctl --user is-active novel-api'

echo "Done."
