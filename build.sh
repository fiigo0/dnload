#!/usr/bin/env bash
# Build Dnlod.app — generates icon, bundles ffmpeg + dylibs, runs py2app.
# Usage: ./build.sh
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

# ---------- pretty helpers ----------
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*" >&2; }
step()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }

# ---------- prerequisites ----------
step "Checking prerequisites"

if [[ ! -d ".venv" ]]; then
  red "No .venv/ found. Create it first:"
  red "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! command -v dylibbundler >/dev/null 2>&1; then
  red "dylibbundler not found. Install with: brew install dylibbundler"
  exit 1
fi

FFMPEG_SRC="$(command -v ffmpeg || true)"
if [[ -z "${FFMPEG_SRC}" ]]; then
  red "ffmpeg not found on PATH. Install with: brew install ffmpeg"
  exit 1
fi
green "✓ dylibbundler at $(command -v dylibbundler)"
green "✓ ffmpeg     at ${FFMPEG_SRC}"
green "✓ python     $(python --version)"

if ! python -c "import py2app" >/dev/null 2>&1; then
  step "Installing py2app into venv"
  pip install --quiet py2app
fi

# ---------- clean previous artifacts ----------
step "Cleaning previous build artifacts"
# Finder sometimes regenerates .DS_Store mid-delete -> retry a few times.
for dir in build dist build_resources; do
  for attempt in 1 2 3; do
    rm -rf "${dir}" 2>/dev/null && break
    sleep 1
  done
  if [[ -e "${dir}" ]]; then
    red "Could not remove ${dir}/ — close any Finder windows showing it and retry."
    exit 1
  fi
done
rm -f Dnlod.icns
green "✓ Removed build/ dist/ build_resources/ Dnlod.icns"

# ---------- icon ----------
step "Generating Dnlod.icns"
python build_icon.py

# ---------- stage ffmpeg + dylibs ----------
step "Staging ffmpeg and bundling dylibs"
mkdir -p build_resources/ffmpeg/lib
cp "${FFMPEG_SRC}" build_resources/ffmpeg/ffmpeg
chmod +x build_resources/ffmpeg/ffmpeg
dylibbundler -of -b -cd \
  -x build_resources/ffmpeg/ffmpeg \
  -d build_resources/ffmpeg/lib \
  -p '@executable_path/lib/' >/dev/null
LIBS_COUNT=$(find build_resources/ffmpeg/lib -name '*.dylib' | wc -l | tr -d ' ')
green "✓ Bundled ffmpeg + ${LIBS_COUNT} dylibs"

# Quick standalone sanity check
if ! ./build_resources/ffmpeg/ffmpeg -version >/dev/null 2>&1; then
  red "Bundled ffmpeg failed its standalone sanity check."
  exit 1
fi
green "✓ Bundled ffmpeg runs standalone"

# ---------- py2app ----------
step "Running py2app"
python setup.py py2app >/dev/null

if [[ ! -d "dist/Dnlod.app" ]]; then
  red "Build finished but dist/Dnlod.app is missing."
  exit 1
fi

# ---------- summary ----------
APP_SIZE=$(du -sh dist/Dnlod.app | awk '{print $1}')
step "Done"
bold "  dist/Dnlod.app   (${APP_SIZE})"
echo "  Bundle id : com.alexdiaz.dnlod"
echo "  Open with : open dist/Dnlod.app"
echo "  Install   : mv dist/Dnlod.app /Applications/"
