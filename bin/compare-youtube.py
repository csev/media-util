#!/usr/bin/env python3
"""Compare youtube-playlist.jsonl against media.yaml YouTube fields."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report inconsistencies between youtube-playlist.jsonl and media.yaml."
    )
    parser.add_argument(
        "--media-yaml",
        type=Path,
        default=None,
        help="Path to media.yaml (default: $MEDIA_YAML / $COURSE_ROOT/media.yaml)",
    )
    parser.add_argument(
        "--youtube-playlist",
        type=Path,
        default=None,
        help=(
            "Playlist JSONL (default: $YOUTUBE_PLAYLIST_JSONL / "
            "$YOUTUBE_DIR/youtube-playlist.jsonl)"
        ),
    )
    parser.add_argument(
        "--lessons",
        type=Path,
        default=None,
        help="lessons.json used for title/id matching hints (default: course lessons.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bootstrap = common.load_bootstrap()

    media_yaml = args.media_yaml or common.default_media_yaml()
    playlist_path = args.youtube_playlist or common.default_youtube_jsonl()
    lessons_path = args.lessons or common.default_lessons()

    data = common.load_media_yaml(media_yaml)
    entries = data["entries"]
    playlist = bootstrap.load_youtube_playlist(playlist_path)
    if not playlist:
        raise SystemExit(f"Error: empty or missing playlist: {playlist_path}")

    by_id, by_title, by_basename = bootstrap.index_youtube_playlist(playlist)
    title_map, lesson_youtube_map, _, _ = bootstrap.load_lessons_media_map(
        lessons_path, relevant=set(entries.keys())
    )

    print(f"media.yaml:           {media_yaml} ({len(entries)} entries)")
    print(f"youtube-playlist:     {playlist_path} ({len(playlist)} videos)")
    print(f"lessons.json (hints): {lessons_path}")

    yaml_ids: dict[str, list[str]] = {}
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        youtube_id = entry.get("youtube_id")
        if isinstance(youtube_id, str) and youtube_id.strip():
            yaml_ids.setdefault(youtube_id.strip(), []).append(rel)

    playlist_ids = set(by_id.keys())

    id_not_in_playlist = []
    for youtube_id, rels in sorted(yaml_ids.items()):
        if youtube_id not in playlist_ids:
            for rel in rels:
                id_not_in_playlist.append(f"{rel}  youtube_id={youtube_id}")

    unused_playlist = []
    for youtube_id, entry in sorted(by_id.items(), key=lambda item: item[1].get("playlist_index") or 0):
        if youtube_id not in yaml_ids:
            title = entry.get("title") or ""
            unused_playlist.append(f"{youtube_id}  {title}")

    missing_id = []
    matchable = []
    id_mismatch = []
    for rel, entry in sorted(entries.items()):
        if not isinstance(entry, dict):
            continue
        current = entry.get("youtube_id")
        current_id = current.strip() if isinstance(current, str) and current.strip() else None
        lesson_title = title_map.get(rel) or entry.get("title")
        if not isinstance(lesson_title, str):
            lesson_title = None
        matched = bootstrap.match_youtube_entry(
            rel,
            lesson_title=lesson_title,
            lesson_youtube_id=lesson_youtube_map.get(rel),
            by_id=by_id,
            by_title=by_title,
            by_basename=by_basename,
        )
        if current_id is None:
            missing_id.append(rel)
            if matched is not None:
                matchable.append(
                    f"{rel}  could match {matched.get('id')} ({matched.get('title')})"
                )
        elif matched is not None and str(matched.get("id")) != current_id:
            id_mismatch.append(
                f"{rel}  yaml={current_id}  matched={matched.get('id')} "
                f"({matched.get('title')})"
            )

    problems = 0
    problems += common.section(
        "media.yaml youtube_id not in playlist", id_not_in_playlist
    )
    problems += common.section(
        "Playlist videos not referenced by media.yaml", unused_playlist
    )
    problems += common.section("media.yaml entries with null/empty youtube_id", missing_id)
    problems += common.section(
        "media.yaml entries with null youtube_id that look matchable", matchable
    )
    problems += common.section(
        "media.yaml youtube_id differs from best playlist match", id_mismatch
    )

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
