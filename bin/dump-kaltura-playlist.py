#!/usr/bin/env python3
"""Dump a public Kaltura/MediaSpace playlist to JSONL.

Parallel to dump-youtube-playlist.sh. Uses the public playlist page (guest KS);
no admin secret required.

Usage (after sourcing media.env):
  dump-kaltura-playlist.py
  dump-kaltura-playlist.py --url 'https://umsiali.mivideo.it.umich.edu/playlist/dedicated/410698932/1_p8621h63'

Uses $KALTURA_PLAYLIST_URL / $KALTURA_PLAYLIST_ID when no --url is given.
Writes to $KALTURA_PLAYLIST_JSONL, else $KALTURA_DIR/kaltura-playlist.jsonl,
else $COURSE_ROOT/kaltura/kaltura-playlist.jsonl. With no output path, prints
JSONL to stdout.

Each line mirrors the YouTube dump shape:
  {"id","title","description","duration","playlist_index"}
plus optional "tags" and "referenceId" when present.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kaltura_common as kc  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dump a public MediaSpace playlist to JSONL "
            "(no Kaltura admin secret required)."
        )
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Public playlist URL (default: $KALTURA_PLAYLIST_URL)",
    )
    parser.add_argument(
        "--url",
        dest="url_flag",
        default=None,
        help="Same as positional playlist URL",
    )
    parser.add_argument(
        "--playlist-id",
        default=None,
        help="Kaltura playlist id (default: $KALTURA_PLAYLIST_ID or from URL)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output JSONL path (default: $KALTURA_PLAYLIST_JSONL / "
            "$KALTURA_DIR/kaltura-playlist.jsonl / "
            "$COURSE_ROOT/kaltura/kaltura-playlist.jsonl)"
        ),
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write JSONL to stdout even when a default output path exists",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    url = args.url_flag or args.url
    output = None if args.stdout else (args.output or kc.default_kaltura_playlist_jsonl())

    resolved_url, playlist_id, entries, source, page_title = kc.fetch_public_playlist(
        url=url,
        playlist_id=args.playlist_id,
    )

    lines: list[str] = []
    for index, entry in enumerate(entries, start=1):
        row = {
            "id": entry["id"],
            "title": entry.get("title"),
            "description": entry.get("description") or "",
            "duration": entry.get("duration"),
            "playlist_index": index,
        }
        tags = entry.get("tags")
        if tags:
            row["tags"] = tags
        ref = entry.get("referenceId")
        if ref:
            row["referenceId"] = ref
        lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

    if not lines:
        print("ERROR: playlist produced no entries", file=sys.stderr)
        return 1

    payload = "\n".join(lines) + "\n"

    if output is None:
        sys.stdout.write(payload)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(output.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(output)
        print(f"Wrote {output} ({len(lines)} entr(y/ies))", file=sys.stderr)

    print(f"playlist url: {resolved_url}", file=sys.stderr)
    print(f"playlist id:  {playlist_id}", file=sys.stderr)
    if page_title:
        print(f"page title:   {page_title}", file=sys.stderr)
    print(f"source:       {source}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
