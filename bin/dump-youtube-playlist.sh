#!/usr/bin/env bash
# Dump a YouTube playlist to JSONL for bootstrap-media-yaml.py
#
# Usage (after sourcing media.env):
#   dump-youtube-playlist.sh
#   dump-youtube-playlist.sh 'https://www.youtube.com/playlist?list=...'
#
# Uses $YOUTUBE_PLAYLIST for the URL when no argument is given.
# Writes to $YOUTUBE_DIR/youtube-playlist.jsonl (or $YOUTUBE_PLAYLIST_JSONL).
# With neither output path set, prints JSONL to stdout.

set -euo pipefail

PLAYLIST_URL="${1:-${YOUTUBE_PLAYLIST:-}}"
OUTPUT_PATH="${YOUTUBE_PLAYLIST_JSONL:-}"

if [ -z "$OUTPUT_PATH" ] && [ -n "${YOUTUBE_DIR:-}" ]; then
    OUTPUT_PATH="$YOUTUBE_DIR/youtube-playlist.jsonl"
fi

if [ -z "$PLAYLIST_URL" ]; then
    echo "Usage: $(basename "$0") [playlist-url]" >&2
    echo "Set YOUTUBE_PLAYLIST in media.env, or pass the playlist URL." >&2
    exit 1
fi

if ! command -v yt-dlp >/dev/null 2>&1; then
    echo "Error: yt-dlp not found" >&2
    exit 1
fi

dump_playlist() {
    yt-dlp \
        --skip-download \
        --ignore-errors \
        --print '{"id":%(id)j,"title":%(title)j,"description":%(description)j,"duration":%(duration)j,"playlist_index":%(playlist_index)j}' \
        "$PLAYLIST_URL"
}

if [ -n "$OUTPUT_PATH" ]; then
    mkdir -p "$(dirname "$OUTPUT_PATH")"
    tmp_path="$OUTPUT_PATH.tmp.$$"
    dump_playlist > "$tmp_path"
    mv -f "$tmp_path" "$OUTPUT_PATH"
    echo "Wrote $OUTPUT_PATH"
else
    dump_playlist
fi
