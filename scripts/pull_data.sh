#!/usr/bin/env bash
# Pull the latest rankings produced by the GitHub Actions refresh.
#
# The workflow publishes to a single-commit `screener-data` branch rather than
# into normal history, so `git pull` on your working branch will not bring it.
# This fetches that branch and drops the files into data/.
#
#     ./scripts/pull_data.sh
#     python -m screener serve
#
# Your local database is overwritten. Anything recorded only locally — notably
# decisions in the journal — is in that file, so this refuses to clobber one
# that is newer than the published copy unless you pass --force.

set -euo pipefail

BRANCH="${SCREENER_DATA_BRANCH:-screener-data}"
DB_PATH="data/screener.sqlite"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! git rev-parse --git-dir > /dev/null 2>&1; then
  echo "Not inside a git repository." >&2
  exit 1
fi

echo "Fetching origin/$BRANCH ..."
if ! git fetch --depth 1 origin "$BRANCH" 2>/dev/null; then
  cat >&2 <<EOF
Could not fetch '$BRANCH'.

The branch only exists once the refresh workflow has run successfully at
least once. Check the Actions tab, or trigger "Refresh rankings" manually.
EOF
  exit 1
fi

mkdir -p data

if [ -f "$DB_PATH" ] && [ "$FORCE" -eq 0 ]; then
  LOCAL_EPOCH=$(date -r "$DB_PATH" +%s 2>/dev/null || stat -f %m "$DB_PATH")
  REMOTE_EPOCH=$(git log -1 --format=%ct "origin/$BRANCH")
  if [ "$LOCAL_EPOCH" -gt "$REMOTE_EPOCH" ]; then
    cat >&2 <<EOF
Your local database is newer than the published one.

  local:     $(date -d "@$LOCAL_EPOCH" 2>/dev/null || date -r "$LOCAL_EPOCH")
  published: $(date -d "@$REMOTE_EPOCH" 2>/dev/null || date -r "$REMOTE_EPOCH")

Overwriting would discard anything recorded since, including journal
decisions. Re-run with --force if that is what you want.
EOF
    exit 1
  fi
fi

git show "origin/$BRANCH:data/screener.sqlite" > "$DB_PATH"
git show "origin/$BRANCH:data/latest-ranking.md" > data/latest-ranking.md 2>/dev/null || true
git show "origin/$BRANCH:data/latest-ranking.json" > data/latest-ranking.json 2>/dev/null || true

echo "Updated $DB_PATH ($(du -h "$DB_PATH" | cut -f1))"
echo "Published $(git log -1 --format='%cr' "origin/$BRANCH")."
echo
echo "Run 'python -m screener serve' to view, or read data/latest-ranking.md."
