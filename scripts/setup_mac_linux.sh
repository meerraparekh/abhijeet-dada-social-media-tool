#!/usr/bin/env bash
# One-time setup on Mac or Linux. Run from the repo root: bash scripts/setup_mac_linux.sh
set -euo pipefail

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg not found."
  if command -v brew >/dev/null; then
    echo "Installing with Homebrew..."
    brew install ffmpeg
  else
    echo "Install Homebrew (https://brew.sh) then run: brew install ffmpeg"
    echo "(On Linux: sudo apt-get install ffmpeg, or your distro's equivalent.)"
    exit 1
  fi
fi

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo
echo "Setup done. Next:"
echo "  1. export ANTHROPIC_API_KEY=sk-ant-...   (from console.anthropic.com)"
echo "  2. bash scripts/run.sh"
echo "  3. Open http://localhost:8000 in your browser"
