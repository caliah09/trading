#!/bin/sh
# Fix volume ownership, then drop privileges before starting the app.
#
# WHY THIS EXISTS
# ---------------
# The image chowns /data at build time, but a platform that mounts a volume
# there (Railway, Kubernetes, Fly) replaces that directory with a fresh
# root-owned one. The build-time ownership is then irrelevant, and an
# unprivileged process cannot create the SQLite file — which surfaces as the
# distinctly unhelpful "sqlite3.OperationalError: unable to open database file".
#
# So the container starts as root, fixes ownership of the mounted directory,
# and only then becomes the unprivileged user for the actual application
# process. That keeps the non-root property for everything that handles
# requests, without requiring the platform to run the whole container as root.

set -e

APP_USER=screener
DB_PATH="${SCREENER_DB:-/data/screener.sqlite}"
DATA_DIR="$(dirname "$DB_PATH")"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    # SQLite needs write access to the directory as well as the file, for its
    # journal and WAL sidecars.
    chown -R "$APP_USER:$APP_USER" "$DATA_DIR" 2>/dev/null || {
        echo "entrypoint: could not take ownership of $DATA_DIR" >&2
    }

    # Prefer setpriv (util-linux, present in Debian slim); gosu if the image
    # has it. Falling back to root is deliberate: a dashboard that runs with
    # more privilege than intended is better than one that will not start, and
    # the warning says plainly which happened.
    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups -- "$@"
    elif command -v gosu >/dev/null 2>&1; then
        exec gosu "$APP_USER" "$@"
    else
        echo "entrypoint: no setpriv or gosu available; running as root." >&2
        exec "$@"
    fi
fi

# Already unprivileged — the platform dropped us here, so just run.
exec "$@"
