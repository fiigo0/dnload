#!/usr/bin/env bash
#
# Dnlod — First Launch helper
#
# Why this exists:
#   macOS quarantines anything downloaded from the internet and refuses to run
#   apps that aren't notarized by Apple ("Dnlod is damaged" or "unidentified
#   developer"). Dnlod is safe — it's just not paying Apple's $99/year for
#   notarization. This script removes the quarantine flag and opens the app.
#
# What it does:
#   1. Finds Dnlod.app (in /Applications, ~/Downloads, ~/Desktop, or next to
#      this script).
#   2. Runs:  xattr -dr com.apple.quarantine <path-to-Dnlod.app>
#   3. Launches the app.
#
# You only need to run this ONCE.

set -e

clear
echo "════════════════════════════════════════"
echo "   Dnlod — First Launch"
echo "════════════════════════════════════════"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CANDIDATES=(
  "${SCRIPT_DIR}/Dnlod.app"
  "/Applications/Dnlod.app"
  "${HOME}/Applications/Dnlod.app"
  "${HOME}/Downloads/Dnlod.app"
  "${HOME}/Desktop/Dnlod.app"
)

APP=""
for candidate in "${CANDIDATES[@]}"; do
  if [[ -d "${candidate}" ]]; then
    APP="${candidate}"
    break
  fi
done

if [[ -z "${APP}" ]]; then
  echo "❌  Could not find Dnlod.app."
  echo
  echo "    Move Dnlod.app to one of these locations and try again:"
  for c in "${CANDIDATES[@]}"; do
    echo "      • ${c}"
  done
  echo
  read -n 1 -s -r -p "Press any key to close…"
  exit 1
fi

echo "✓  Found Dnlod.app at:"
echo "   ${APP}"
echo
echo "→  Removing macOS quarantine flag…"
xattr -dr com.apple.quarantine "${APP}" 2>/dev/null || true
echo "✓  Done."
echo
echo "→  Launching Dnlod…"
open "${APP}"
echo "✓  Dnlod should now open."
echo
echo "You can delete this script — you won't need it again."
echo
read -n 1 -s -r -p "Press any key to close this window…"
