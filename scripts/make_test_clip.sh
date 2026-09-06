#!/usr/bin/env bash
# Generates a short synthetic "talk" video (color background + spoken-word audio
# via espeak-ng) so the pipeline can be smoke-tested without real session footage.
# Usage: bash scripts/make_test_clip.sh [output_path]
set -euo pipefail

OUT="${1:-/tmp/test_session.mp4}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

TEXT="Welcome everyone to this evening's session. Tonight I want to speak about
the nature of stillness. When the mind becomes quiet, we begin to see things
as they really are, not as we wish them to be. There is a story about a lake.
When the water is disturbed, you cannot see the bottom, no matter how clear
the water actually is. But when the water settles, even a single leaf at the
bottom becomes visible. Your mind is exactly like that lake. The practice is
not to force the water still, but to simply stop stirring it. Let us sit
together in silence for a moment and notice what remains when we stop trying
so hard. That noticing, that simple awareness, is the beginning of everything
we are here to explore together. Thank you for being here tonight."

command -v espeak-ng >/dev/null || { echo "espeak-ng not found - install it or supply your own test video."; exit 1; }

echo "Synthesizing speech..."
espeak-ng -s 150 -v en "$TEXT" -w "$WORKDIR/speech.wav"

DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WORKDIR/speech.wav")

echo "Rendering ${DURATION}s test video to $OUT ..."
ffmpeg -y \
  -f lavfi -i "color=c=0x3a5a40:s=1280x720:d=${DURATION}" \
  -i "$WORKDIR/speech.wav" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac \
  -shortest "$OUT"

echo "Done: $OUT"
