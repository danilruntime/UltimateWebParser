#!/bin/sh
# Container entry point: make sure the data volume exists, then run the CLI.
set -e

DATA_DIR="${UWP_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR" "$DATA_DIR/profiles" "$DATA_DIR/exports" "$DATA_DIR/artifacts"

# XDG dirs keep Chromium's cache inside the writable volume.
export HOME="${HOME:-/home/uwp}"
export XDG_CACHE_HOME="$DATA_DIR/.cache"
export XDG_CONFIG_HOME="$DATA_DIR/.config"
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"

exec uwp "$@"
