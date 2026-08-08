#!/usr/bin/env bash
# Copy only .txt novels from the Windows library into the server's library/.
#
# The directory tree is mirrored, top-level folders and all nesting:
#   <SRC>/English/foo.txt     -> library/English/foo.txt
#   <SRC>/GL/sub/bar.txt      -> library/GL/sub/bar.txt
# Anything that isn't a .txt (epub, pdf, docx, images, zip, ...) is skipped, and
# folders that would end up empty aren't created (rsync -m prunes them).
#
# This only adds/updates .txt files. It never deletes anything on the server, so
# the existing metadata categories (gl, yanqing, ...) and any other content are
# left untouched. Re-running is cheap: unchanged files are skipped.
#
# Config via env (defaults match deploy.sh):
#   NOVEL_SRC     source dir  (the Windows Novels folder)
#   NOVEL_SERVER  ssh target  (set empty to copy into a LOCAL path instead)
#   NOVEL_DEST    project dir on the server, relative to its home
# Flags: --dry-run/-n to preview, --help/-h.
set -euo pipefail

SRC="${NOVEL_SRC:-/mnt/c/Users/lewei/OneDrive - UWA/Documents/Novels}"
SERVER="${NOVEL_SERVER-homeserver}"           # ssh alias (~/.ssh/config); "" = local copy
DEST="${NOVEL_DEST:-Novel_Project/}"          # relative to the server's home
DRY=""

for arg in "$@"; do
  case "$arg" in
    -n|--dry-run) DRY="--dry-run" ;;
    -h|--help)
      # Print the leading comment block (everything from line 2 up to the first
      # non-comment line), stripped of the leading "# ".
      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
      exit 0 ;;
    *) echo "unknown arg: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [[ ! -d "$SRC" ]]; then
  echo "source not found: $SRC" >&2
  exit 1
fi

# With NOVEL_SERVER set (the default), copy to the home server over ssh; set it
# empty to copy into a local library/ under NOVEL_DEST instead.
if [[ -n "$SERVER" ]]; then
  TARGET="$SERVER:${DEST}library/"
else
  TARGET="${DEST}library/"
fi

echo "Copying .txt only:"
echo "  from: $SRC/"
echo "  to:   $TARGET"
[[ -n "$DRY" ]] && echo "  (dry run, nothing is written)"
echo

# -r recurse, -t keep mtimes (so re-runs skip unchanged), -m prune empty dirs,
# -v list files. --chmod normalises perms (the Windows mount reports 777).
# The include/exclude trio is the standard "only these files, keep the tree":
# descend into every dir, take *.txt, drop everything else.
rsync -rtvm $DRY \
  --chmod=D755,F644 \
  --include='*/' \
  --include='*.txt' \
  --exclude='*' \
  "$SRC/" "$TARGET"

echo
echo "Done."
