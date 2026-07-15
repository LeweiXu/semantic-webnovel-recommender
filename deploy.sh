#!/usr/bin/env bash
# Push the reader-app backend to the home server in one command.
#
# The backend imports the repo-root packages (recsys/, webnovel/, scraper.py,
# scripts/), so we sync the WHOLE repo, not just reader-app/backend/. Code only:
# library/ and data/ live on the server and are never touched (see
# deploy-exclude.txt). After syncing, restart the service on the server.
set -euo pipefail

SERVER="${NOVEL_SERVER:-lingwei@192.168.20.9}"
DEST="${NOVEL_DEST:-Novel_Project/}"          # relative to the server's home
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Syncing code -> $SERVER:~/$DEST"
rsync -avz --delete \
  --exclude-from="$HERE/deploy-exclude.txt" \
  "$HERE/" "$SERVER:$DEST"

echo
echo "Done. Restart the API on the server:"
echo "  ssh $SERVER 'systemctl --user restart novel-api'"
