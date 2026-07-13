#!/usr/bin/env bash
# Dump a YouTube playlist to JSONL for bootstrap-media-yaml.py
#
# Usage:
#   dump-youtube-playlist.sh 'https://www.youtube.com/playlist?list=...' > media/youtube-playlist.jsonl

set -euo pipefail

PLAYLIST_URL="${1:-}"

if [ -z "$PLAYLIST_URL" ]; then
    echo "Usage: $(basename "$0") <playlist-url>" >&2
    exit 1
fi

if ! command -v yt-dlp >/dev/null 2>&1; then
    echo "Error: yt-dlp not found" >&2
    exit 1
fi

yt-dlp \
    --skip-download \
    --ignore-errors \
    --print '{"id":%(id)j,"title":%(title)j,"description":%(description)j,"duration":%(duration)j,"playlist_index":%(playlist_index)j}' \
    "$PLAYLIST_URL"
