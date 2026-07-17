#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
APP=${1:-"$ROOT/desktop/src-tauri/target/release/bundle/macos/Poppy.app"}

if [ "$(uname -s)" != "Darwin" ]; then
  printf '%s\n' "Skipping macOS code signing on $(uname -s)."
  exit 0
fi

if [ ! -d "$APP" ]; then
  printf 'Poppy.app not found: %s\n' "$APP" >&2
  exit 1
fi

# The local development build is unsigned. A stable designated requirement
# keeps macOS Keychain authorization attached to Poppy across rebuilds, so the
# user does not have to re-enter the login-keychain password every launch.
# This is for local use only; a distributed build should use a Developer ID.
codesign \
  --force \
  --deep \
  --sign - \
  --identifier com.george.poppy \
  --requirements '=designated => identifier "com.george.poppy"' \
  "$APP"
codesign --verify --deep --strict "$APP"
printf 'Signed %s with a stable local Poppy requirement.\n' "$APP"
