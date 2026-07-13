#!/usr/bin/env python3
"""Copy media.yaml titles into lessons.json video entries.

Review is a lessons.json-only concept:
  - If the existing title is a review (starts with ``Review`` or contains
    ``Review:`` / ``(review)``), the new title becomes
    ``Review: <media.yaml title>`` and ``"review": true`` is set.
  - Non-review media entries get the media.yaml title as-is (no review key).
  - Non-media entries whose title already starts with ``Review`` get
    ``"review": true`` only.

Usage:
  source media.env
  sync-lessons-titles-from-media.py
  sync-lessons-titles-from-media.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

REVIEW_MARKER_RE = re.compile(r"Review:|\(\s*review\s*\)", re.IGNORECASE)


def env_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value.strip())
    return None


def default_lessons() -> Path:
    path = env_path("LESSONS_JSON")
    if path:
        return path
    course = env_path("COURSE_ROOT")
    if course:
        return course / "lessons.json"
    return Path.cwd() / "lessons.json"


def default_media_yaml() -> Path:
    path = env_path("MEDIA_YAML")
    if path:
        return path
    course = env_path("COURSE_ROOT")
    if course:
        return course / "media.yaml"
    return Path.cwd() / "media.yaml"


def is_review_title(title: str) -> bool:
    text = title.strip()
    if text.lower().startswith("review"):
        return True
    return bool(REVIEW_MARKER_RE.search(text))


def starts_with_review(title: str) -> bool:
    return title.strip().lower().startswith("review")


def compose_lesson_title(media_title: str, *, was_review: bool) -> str:
    body = media_title.strip()
    if starts_with_review(body):
        return body
    if was_review:
        return f"Review: {body}"
    return body


def rebuild_item(item: dict[str, Any], title: str, *, review: bool) -> dict[str, Any]:
    """Return a new dict with title updated and review key placed after title."""
    out: dict[str, Any] = {}
    for key, value in item.items():
        if key == "review":
            continue
        if key == "title":
            out["title"] = title
            if review:
                out["review"] = True
            continue
        out[key] = value
    if "title" not in out:
        out["title"] = title
        if review:
            out["review"] = True
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update lessons.json titles from media.yaml (Review stays in lessons)."
    )
    parser.add_argument(
        "--lessons",
        type=Path,
        default=None,
        help="Path to lessons.json (default: $LESSONS_JSON / $COURSE_ROOT/lessons.json)",
    )
    parser.add_argument(
        "--media-yaml",
        type=Path,
        default=None,
        help="Path to media.yaml (default: $MEDIA_YAML / $COURSE_ROOT/media.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without writing lessons.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lessons_path = args.lessons or default_lessons()
    media_yaml_path = args.media_yaml or default_media_yaml()

    if not lessons_path.is_file():
        print(f"ERROR: lessons.json not found: {lessons_path}", file=sys.stderr)
        return 1
    if not media_yaml_path.is_file():
        print(f"ERROR: media.yaml not found: {media_yaml_path}", file=sys.stderr)
        return 1

    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    yaml = YAML(typ="safe")
    media_data = yaml.load(media_yaml_path.read_text(encoding="utf-8"))
    entries = (media_data or {}).get("entries") or {}
    if not isinstance(entries, dict):
        print(
            f"ERROR: media.yaml entries must be a mapping: {media_yaml_path}",
            file=sys.stderr,
        )
        return 1

    media_titles: dict[str, str] = {}
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if isinstance(title, str) and title.strip():
            media_titles[str(rel)] = title.strip()

    updated = 0
    review_flagged = 0
    missing_media = 0
    samples: list[str] = []

    def walk_inplace(obj: Any) -> None:
        nonlocal updated, review_flagged, missing_media
        if isinstance(obj, dict):
            title = obj.get("title")
            media = obj.get("media")
            if isinstance(title, str) and isinstance(media, str):
                media_title = media_titles.get(media)
                if media_title is None:
                    missing_media += 1
                else:
                    was_review = is_review_title(title)
                    new_title = compose_lesson_title(media_title, was_review=was_review)
                    review = starts_with_review(new_title)
                    needs_review_key = review and obj.get("review") is not True
                    drop_review = ("review" in obj) and not review
                    if new_title != title or needs_review_key or drop_review:
                        rebuilt = rebuild_item(obj, new_title, review=review)
                        obj.clear()
                        obj.update(rebuilt)
                        updated += 1
                        if review:
                            review_flagged += 1
                        if len(samples) < 8:
                            samples.append(
                                f"  {title!r}\n    -> {new_title!r} review={review}"
                            )
            elif isinstance(title, str) and starts_with_review(title):
                if obj.get("review") is not True:
                    rebuilt = rebuild_item(obj, title.strip(), review=True)
                    obj.clear()
                    obj.update(rebuilt)
                    updated += 1
                    review_flagged += 1
                    if len(samples) < 8:
                        samples.append(f"  (flag only) {title!r} -> review=true")

            for value in list(obj.values()):
                walk_inplace(value)
        elif isinstance(obj, list):
            for item in obj:
                walk_inplace(item)

    walk_inplace(lessons)

    print(f"lessons.json: {lessons_path}")
    print(f"media.yaml:   {media_yaml_path} ({len(media_titles)} titles)")
    print(f"entries updated: {updated}")
    print(f"review:true set: {review_flagged}")
    if missing_media:
        print(f"WARNING: media paths missing from media.yaml: {missing_media}")
    if samples:
        print("samples:")
        for line in samples:
            print(line)

    if args.dry_run:
        print("dry-run only; lessons.json not written")
        return 0

    text = json.dumps(lessons, indent=4, ensure_ascii=False) + "\n"
    lessons_path.write_text(text, encoding="utf-8")
    print(f"Wrote {lessons_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
