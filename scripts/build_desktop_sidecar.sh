#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=$(rustc --print host-tuple)
BUILD_ROOT=${TMPDIR:-/tmp}/pico-sidecar-build
DEST="$ROOT/desktop/src-tauri/binaries/poppy-gateway-$TARGET"

mkdir -p "$ROOT/desktop/src-tauri/binaries" "$BUILD_ROOT/dist" "$BUILD_ROOT/work" "$BUILD_ROOT/spec"
uv run python -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name poppy-gateway \
  --distpath "$BUILD_ROOT/dist" \
  --workpath "$BUILD_ROOT/work" \
  --specpath "$BUILD_ROOT/spec" \
  --paths "$ROOT" \
  --collect-all uvicorn \
  --collect-all fastapi \
  "$ROOT/scripts/pico_gateway_sidecar.py"
cp "$BUILD_ROOT/dist/poppy-gateway" "$DEST"
chmod 755 "$DEST"
printf '%s\n' "$DEST"
