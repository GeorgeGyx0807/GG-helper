#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=$(rustc --print host-tuple)
BUILD_ROOT=${TMPDIR:-/tmp}/poppy-sidecar-build
DEST="$ROOT/desktop/src-tauri/binaries/poppy-gateway-$TARGET"
OCR_HELPER="$BUILD_ROOT/poppy-ocr"
SEMANTIC_HELPER="$BUILD_ROOT/poppy-semantic"

mkdir -p "$ROOT/desktop/src-tauri/binaries" "$BUILD_ROOT/dist" "$BUILD_ROOT/work" "$BUILD_ROOT/spec"
PYTHON_BIN=${POPPY_PYTHON:-"$ROOT/.venv/bin/python"}
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi
CLANG_MODULE_CACHE_PATH="$BUILD_ROOT/clang-module-cache" \
  clang -O2 -fobjc-arc -framework Foundation -framework AppKit -framework Vision \
  "$ROOT/scripts/poppy_ocr.m" -o "$OCR_HELPER"
CLANG_MODULE_CACHE_PATH="$BUILD_ROOT/clang-module-cache" \
  clang -O2 -fobjc-arc -fblocks -framework Foundation -framework NaturalLanguage \
  "$ROOT/scripts/poppy_semantic.m" -o "$SEMANTIC_HELPER"
PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-config" "$PYTHON_BIN" -m PyInstaller \
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
  --collect-all markitdown \
  --collect-all pdfplumber \
  --collect-all pypdfium2 \
  --collect-all openpyxl \
  --collect-all xlrd \
  --collect-all lark_channel \
  --collect-all watchdog \
  --collect-all fastembed \
  --collect-all lancedb \
  --collect-all pyarrow \
  --collect-all onnxruntime \
  --collect-all tokenizers \
  --collect-all huggingface_hub \
  --add-binary "$OCR_HELPER:." \
  --add-binary "$SEMANTIC_HELPER:." \
  "$ROOT/scripts/poppy_gateway_sidecar.py"
cp "$BUILD_ROOT/dist/poppy-gateway" "$DEST"
chmod 755 "$DEST"
printf '%s\n' "$DEST"
