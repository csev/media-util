#!/usr/bin/env bash

# transcribe-file.sh
#
# Usage:
#   ./transcribe-file.sh input.mp4
#
# Outputs (same folder as input):
#   input.txt
#   input.srt
#   input.vtt

set -euo pipefail

INPUT="${1:-}"

if [ -z "$INPUT" ]; then
    echo "Usage: $0 <media-file>"
    exit 1
fi

if [ ! -f "$INPUT" ]; then
    echo "Error: file not found: $INPUT"
    exit 1
fi

MODEL="${WHISPER_MODEL:-$HOME/models/ggml-medium.bin}"

if [ ! -f "$MODEL" ]; then
    echo "Error: model not found: $MODEL"
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "Error: ffmpeg not found"
    exit 1
fi

if ! command -v whisper-cli >/dev/null 2>&1; then
    echo "Error: whisper-cli not found"
    exit 1
fi

DIR="$(dirname "$INPUT")"
FILE="$(basename "$INPUT")"
STEM="${FILE%.*}"

WAV="$DIR/$STEM-whisper.wav"

echo "Processing: $INPUT"
echo "Model: $MODEL"
echo

echo "→ Extracting audio..."
ffmpeg -y -i "$INPUT" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$WAV" >/dev/null 2>&1

echo "→ Running whisper..."
whisper-cli \
    -m "$MODEL" \
    -f "$WAV" \
    -otxt \
    -osrt \
    -ovtt

echo "→ Cleaning up..."
rm -f "$WAV"

echo
echo "Done:"
echo "  $DIR/$STEM.txt"
echo "  $DIR/$STEM.srt"
echo "  $DIR/$STEM.vtt"

