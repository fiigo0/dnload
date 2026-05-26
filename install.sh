#!/usr/bin/env bash
#
# Dnlod installer.
#
# Usage (paste into Terminal):
#   curl -fsSL https://raw.githubusercontent.com/fiigo0/dnload/main/install.sh | bash
#
# Why this exists:
#   macOS Gatekeeper blocks anything you download with a browser unless it's
#   notarized by Apple (which requires a paid Apple Developer account). Dnlod
#   isn't notarized. But files downloaded via `curl` don't get the quarantine
#   flag, so this installer fetches and unpacks the .app without ever tripping
#   Gatekeeper. No "Apple could not verify…" dialog.

set -euo pipefail

REPO="fiigo0/dnload"
ASSET_PATTERN="Dnlod-v.*-macos-arm64\\.tar\\.xz"

# ---------- helpers ----------
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*" >&2; }
step()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }

# ---------- preflight ----------
if [[ "$(uname)" != "Darwin" ]]; then
  red "Dnlod is macOS-only. You're on $(uname)."
  exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
  red "Dnlod currently supports Apple Silicon (arm64) only. You're on $ARCH."
  red "Intel-Mac support would need a separate build with x86_64 ffmpeg bundled."
  exit 1
fi

# ---------- resolve latest release asset ----------
step "Looking up latest Dnlod release"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
ASSET_URL="$(
  curl -fsSL "${API_URL}" \
    | grep -Eo "\"browser_download_url\"[[:space:]]*:[[:space:]]*\"[^\"]*${ASSET_PATTERN}\"" \
    | head -1 \
    | sed -E 's/.*"(https[^"]+)"/\1/'
)"

if [[ -z "${ASSET_URL}" ]]; then
  red "Could not find a matching release asset at ${API_URL}"
  red "Check https://github.com/${REPO}/releases manually."
  exit 1
fi

VERSION="$(basename "${ASSET_URL}" | sed -E 's/Dnlod-(v[^-]+)-.*/\1/')"
green "✓ Found Dnlod ${VERSION}"

# ---------- download ----------
step "Downloading"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
ARCHIVE="${TMP}/dnlod.tar.xz"
curl -fL --progress-bar "${ASSET_URL}" -o "${ARCHIVE}"

# ---------- extract ----------
step "Extracting"
tar -C "${TMP}" -xJf "${ARCHIVE}"
APP_SRC="$(/usr/bin/find "${TMP}" -maxdepth 3 -name 'Dnlod.app' -type d -print -quit)"
if [[ -z "${APP_SRC}" ]]; then
  red "Dnlod.app not found inside the archive."
  exit 1
fi

# Belt + suspenders: curl shouldn't quarantine, but strip just in case.
xattr -dr com.apple.quarantine "${APP_SRC}" 2>/dev/null || true

# ---------- install ----------
step "Installing"
DEST="/Applications/Dnlod.app"
if [[ ! -w "/Applications" ]]; then
  DEST="${HOME}/Applications/Dnlod.app"
  mkdir -p "${HOME}/Applications"
  bold "  /Applications isn't writable — installing into ~/Applications instead."
fi

if [[ -e "${DEST}" ]]; then
  bold "  Removing existing ${DEST}"
  rm -rf "${DEST}"
fi
mv "${APP_SRC}" "${DEST}"
green "✓ Installed to ${DEST}"

# ---------- launch ----------
step "Launching Dnlod"
open "${DEST}"

cat <<EOF

────────────────────────────────────────
   Dnlod ${VERSION} is installed.
   Location: ${DEST}
────────────────────────────────────────

EOF
