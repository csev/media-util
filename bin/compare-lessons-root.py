#!/usr/bin/env python3
"""Compare lessons.json media references against MEDIA_ROOT (no media.yaml)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report inconsistencies between lessons.json and MEDIA_ROOT. "
            "Does not read or modify media.yaml."
        )
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=None,
        help="Media binary tree (default: $MEDIA_ROOT)",
    )
    parser.add_argument(
        "--lessons",
        type=Path,
        default=None,
        help="Path to lessons.json (default: $LESSONS_JSON / $COURSE_ROOT/lessons.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bootstrap = common.load_bootstrap()

    media_root = args.media_root or common.default_media_root()
    lessons_path = args.lessons or common.default_lessons()

    root_files = set(bootstrap.scan_media_root(media_root))
    (
        title_map,
        youtube_map,
        _kaltura_map,
        title_conflicts,
        youtube_conflicts,
        _kaltura_conflicts,
    ) = bootstrap.load_lessons_media_map(lessons_path, strict=False)
    lesson_files = set(title_map.keys())
    title_folder_file_index = bootstrap.build_lessons_folder_file_index(title_map)

    # Pair MEDIA_ROOT folder/file paths to lessons.json media keys (exact or
    # unique folder/file — lessons may use a longer prefix like ca4e-media/...).
    root_to_lesson: dict[str, str] = {}
    for rel in root_files:
        key = bootstrap.match_lessons_media_key(rel, title_map, title_folder_file_index)
        if key is not None:
            root_to_lesson[rel] = key
    matched_lesson_keys = set(root_to_lesson.values())
    matched_root = set(root_to_lesson.keys())

    print(f"lessons.json: {lessons_path} ({len(lesson_files)} media refs)")
    print(f"MEDIA_ROOT:   {media_root} ({len(root_files)} media files)")

    only_lessons = sorted(lesson_files - matched_lesson_keys)
    only_root = sorted(root_files - matched_root)

    conflict_title_lines: list[str] = []
    for media, titles in sorted(title_conflicts.items()):
        conflict_title_lines.append(media)
        for title in sorted(titles):
            conflict_title_lines.append(f"    - {title}")

    conflict_youtube_lines: list[str] = []
    for media, ids in sorted(youtube_conflicts.items()):
        conflict_youtube_lines.append(media)
        for youtube_id in sorted(ids):
            conflict_youtube_lines.append(f"    - {youtube_id}")

    # Lessons that point at a path present on disk, but with no youtube id.
    missing_youtube = sorted(
        root_to_lesson[rel]
        for rel in sorted(matched_root)
        if root_to_lesson[rel] not in youtube_map
    )

    problems = 0
    problems += common.section(
        "Conflicting titles for same media path inside lessons.json",
        conflict_title_lines,
    )
    problems += common.section(
        "Conflicting youtube ids for same media path inside lessons.json",
        conflict_youtube_lines,
    )
    problems += common.section(
        "In lessons.json but missing from MEDIA_ROOT", only_lessons
    )
    problems += common.section(
        "In MEDIA_ROOT but not referenced by lessons.json", only_root
    )
    problems += common.section(
        "In both, but lessons.json has no youtube id", missing_youtube
    )

    print(f"\nIn both: {len(matched_root)} (exact or unique folder/file)")
    print(f"lessons.json youtube ids: {len(youtube_map)}")

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
