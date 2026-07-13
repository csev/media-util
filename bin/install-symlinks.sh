#!/usr/bin/env bash
# Install ~/bin wrappers that point at this media-util/bin directory.

set -euo pipefail

SOURCE="$0"
while [ -L "$SOURCE" ]; do
    TARGET="$(readlink "$SOURCE")"
    case "$TARGET" in
        /*) SOURCE="$TARGET" ;;
        *)  SOURCE="$(dirname "$SOURCE")/$TARGET" ;;
    esac
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd -P)"
BIN_DIR="${BIN_DIR:-$HOME/bin}"

mkdir -p "$BIN_DIR"

link_one() {
    name="$1"
    src="$SCRIPT_DIR/$name"
    dst="$BIN_DIR/$name"

    if [ ! -e "$src" ]; then
        echo "SKIP missing: $src" >&2
        return 1
    fi

    ln -sfn "$src" "$dst"
    echo "LINK $dst -> $src"
}

link_one whisper-desc
link_one whisper-cleanup.py
link_one whisper-folder.sh
link_one whisper-media.sh
link_one whisper-one.sh
link_one bootstrap-media-yaml.py
link_one dump-youtube-playlist.sh

echo
echo "Installed into $BIN_DIR"
echo "Ensure $BIN_DIR is on your PATH."
