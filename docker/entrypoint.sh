#!/bin/sh
# Container entry point.
#
# The container starts as root for exactly one reason: a fresh bind mount
# (``./data``) or a brand-new named volume is owned by root, and the service
# runs as uid 1000. Repairing the ownership here is the difference between a
# working ``docker compose up`` and a crash loop on first start. Privileges are
# dropped before the CLI runs, so nothing below that point is root.
set -e
# Everything the container writes (profiles with live cookies, the database and
# its WAL, screenshots, VNC password files) is private to uid 1000. The database
# and the data directory are chmod-ed by the app as well; the umask covers files
# SQLite creates later, such as -wal/-shm.
umask 077

DATA_DIR="${UWP_DATA_DIR:-/data}"
UWP_UID="${UWP_UID:-1000}"
UWP_GID="${UWP_GID:-1000}"

prepare_data_dir() {
    for path in "$DATA_DIR" "$DATA_DIR/profiles" "$DATA_DIR/exports" \
                "$DATA_DIR/artifacts" "$DATA_DIR/.cache" "$DATA_DIR/.config"; do
        mkdir -p "$path"
    done
}

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    prepare_data_dir
    # Only walk the tree when a top-level entry is not ours: the healthcheck
    # calls this script every minute and a recursive chown must not run then.
    needs_chown=0
    for path in "$DATA_DIR" "$DATA_DIR/profiles" "$DATA_DIR/exports" \
                "$DATA_DIR/artifacts"; do
        if [ "$(stat -c '%u' "$path")" != "$UWP_UID" ]; then
            needs_chown=1
        fi
    done
    if [ "$needs_chown" = "1" ]; then
        chown -R "$UWP_UID:$UWP_GID" "$DATA_DIR" || {
            echo "uwp: cannot make $DATA_DIR writable by uid $UWP_UID" >&2
            echo "uwp: on the host run: mkdir -p data && sudo chown -R $UWP_UID:$UWP_GID data" >&2
            exit 2
        }
    fi
    exec setpriv --reuid="$UWP_UID" --regid="$UWP_GID" --init-groups /entrypoint.sh "$@"
fi

prepare_data_dir 2>/dev/null || {
    echo "uwp: cannot create directories in $DATA_DIR (uid $(id -u))" >&2
    echo "uwp: on the host run: mkdir -p data && sudo chown -R $UWP_UID:$UWP_GID data" >&2
    exit 2
}

# The directories may exist and still be unwritable (a host `chmod 555`, or a
# volume owned by a different uid). Probe before the CLI does: otherwise the
# first symptom is sqlite's "unable to open database file" traceback.
probe="$DATA_DIR/.uwp-write-test"
if ! ( : > "$probe" ) 2>/dev/null; then
    echo "uwp: $DATA_DIR is not writable by uid $(id -u)" >&2
    echo "uwp: on the host run: sudo chown -R $UWP_UID:$UWP_GID data" >&2
    exit 2
fi
rm -f "$probe"

# XDG dirs keep Chromium's cache inside the writable volume.
# ``HOME`` is already ``/root`` in the base image, so ``${HOME:-...}`` would
# keep it and Chromium (now uid 1000) could not create ``~/.local``.
if [ -n "${UWP_HOME:-}" ]; then
    HOME="$UWP_HOME"
else
    HOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"
    HOME="${HOME:-/home/uwp}"
fi
export HOME
export XDG_CACHE_HOME="$DATA_DIR/.cache"
export XDG_CONFIG_HOME="$DATA_DIR/.config"
mkdir -p "$HOME/.local/share" 2>/dev/null || {
    HOME="$DATA_DIR/home"
    export HOME
    mkdir -p "$HOME/.local/share"
}

exec uwp "$@"
