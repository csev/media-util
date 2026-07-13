#!/usr/bin/env python3
"""Compare lessons.json media references against media.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report inconsistencies between lessons.json and media.yaml."
    )
    parser.add_argument(
        "--media-yaml",
        type=Path,
        default=None,
        help="Path to media.yaml (default: $MEDIA_YAML / $COURSE_ROOT/media.yaml)",
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

    media_yaml = args.media_yaml or common.default_media_yaml()
    lessons_path = args.lessons or common.default_lessons()

    data = common.load_media_yaml(media_yaml)
    entries = data["entries"]
    yaml_files = set(entries.keys())

    title_map, youtube_map, title_conflicts, youtube_conflicts = (
        bootstrap.load_lessons_media_map(lessons_path, strict=False)
    )
    lesson_files = set(title_map.keys())

    print(f"lessons.json: {lessons_path} ({len(lesson_files)} media refs)")
    print(f"media.yaml:   {media_yaml} ({len(yaml_files)} entries)")

    only_lessons = sorted(lesson_files - yaml_files)
    only_yaml = sorted(yaml_files - lesson_files)

    conflict_title_lines = []
    for media, titles in sorted(title_conflicts.items()):
        conflict_title_lines.append(media)
        for title in sorted(titles):
            conflict_title_lines.append(f"    - {title}")

    conflict_youtube_lines = []
    for media, ids in sorted(youtube_conflicts.items()):
        conflict_youtube_lines.append(media)
        for youtube_id in sorted(ids):
            conflict_youtube_lines.append(f"    - {youtube_id}")

    title_mismatch: list[str] = []
    youtube_mismatch: list[str] = []
    yaml_missing_youtube: list[str] = []

    for rel in sorted(lesson_files & yaml_files):
        entry = entries.get(rel) or {}
        if not isinstance(entry, dict):
            continue

        lesson_title = title_map[rel]
        yaml_title = entry.get("title")
        if isinstance(yaml_title, str) and yaml_title.strip():
            if not bootstrap.titles_compatible(lesson_title, yaml_title):
                title_mismatch.append(
                    f"{rel}\n    lessons: {lesson_title}\n    yaml:    {yaml_title}"
                )
        else:
            title_mismatch.append(
                f"{rel}\n    lessons: {lesson_title}\n    yaml:    (missing title)"
            )

        lesson_yt = youtube_map.get(rel)
        yaml_yt = entry.get("youtube_id")
        yaml_yt = yaml_yt.strip() if isinstance(yaml_yt, str) and yaml_yt.strip() else None
        if lesson_yt and yaml_yt and lesson_yt != yaml_yt:
            youtube_mismatch.append(
                f"{rel}  lessons={lesson_yt}  yaml={yaml_yt}"
            )
        elif lesson_yt and not yaml_yt:
            yaml_missing_youtube.append(f"{rel}  lessons youtube={lesson_yt}")

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
        "In lessons.json but missing from media.yaml", only_lessons
    )
    problems += common.section(
        "In media.yaml but not referenced by lessons.json", only_yaml
    )
    problems += common.section("Title mismatches (lessons vs media.yaml)", title_mismatch)
    problems += common.section(
        "YouTube id mismatches (lessons vs media.yaml)", youtube_mismatch
    )
    problems += common.section(
        "lessons.json has youtube id but media.yaml does not", yaml_missing_youtube
    )

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
