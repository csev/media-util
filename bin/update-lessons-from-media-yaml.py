#!/usr/bin/env python3
"""Copy media.yaml titles and kaltura_id into lessons.json video entries.

Review is a lessons.json-only concept:
  - If the existing title is a review (starts with ``Review`` or contains
    ``Review:`` / ``(review)``), the new title becomes
    ``Review: <media.yaml title>`` and ``"review": true`` is set.
  - Non-review media entries get the media.yaml title as-is (no review key).
  - Non-media entries whose title already starts with ``Review`` get
    ``"review": true`` only.

``kaltura_id`` is copied from media.yaml onto items that share the same
``media`` path when media.yaml has a non-empty value. Existing lessons
``kaltura_id`` values are left alone when media.yaml is null/empty.

Usage:
  source media.env
  update-lessons-from-media-yaml.py
  update-lessons-from-media-yaml.py --dry-run
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


def normalize_kaltura_id(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def rebuild_item(
    item: dict[str, Any],
    title: str,
    *,
    review: bool,
    kaltura_id: str | None = None,
    set_kaltura: bool = False,
) -> dict[str, Any]:
    """Return a new dict with title/review/kaltura_id updated in a stable order."""
    out: dict[str, Any] = {}
    for key, value in item.items():
        if key == "review":
            continue
        if key == "kaltura_id":
            # Drop old value; re-insert below in the preferred spot when updating.
            if set_kaltura:
                continue
            out[key] = value
            continue
        if key == "title":
            out["title"] = title
            if review:
                out["review"] = True
            continue
        if key == "media" and set_kaltura and kaltura_id and "kaltura_id" not in out:
            out["kaltura_id"] = kaltura_id
        out[key] = value
        if key == "youtube" and set_kaltura and kaltura_id:
            out["kaltura_id"] = kaltura_id

    if "title" not in out:
        out["title"] = title
        if review:
            out["review"] = True

    if set_kaltura and kaltura_id and "kaltura_id" not in out:
        out["kaltura_id"] = kaltura_id

    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Update lessons.json titles and kaltura_id from media.yaml "
            "(Review stays in lessons)."
        )
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
    media_kaltura: dict[str, str] = {}
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if isinstance(title, str) and title.strip():
            media_titles[str(rel)] = title.strip()
        kid = normalize_kaltura_id(entry.get("kaltura_id"))
        if kid:
            media_kaltura[str(rel)] = kid

    updated = 0
    review_flagged = 0
    kaltura_updated = 0
    missing_media = 0
    samples: list[str] = []

    def walk_inplace(obj: Any) -> None:
        nonlocal updated, review_flagged, kaltura_updated, missing_media
        if isinstance(obj, dict):
            title = obj.get("title")
            media = obj.get("media")
            if isinstance(title, str) and isinstance(media, str):
                media_title = media_titles.get(media)
                wanted_kaltura = media_kaltura.get(media)
                current_kaltura = normalize_kaltura_id(obj.get("kaltura_id"))

                if media_title is None and media not in media_kaltura:
                    missing_media += 1
                else:
                    was_review = is_review_title(title)
                    if media_title is not None:
                        new_title = compose_lesson_title(
                            media_title, was_review=was_review
                        )
                    else:
                        new_title = title
                    review = starts_with_review(new_title)
                    needs_review_key = review and obj.get("review") is not True
                    drop_review = ("review" in obj) and not review
                    title_changed = new_title != title or needs_review_key or drop_review

                    set_kaltura = (
                        wanted_kaltura is not None
                        and wanted_kaltura != current_kaltura
                    )

                    if title_changed or set_kaltura:
                        rebuilt = rebuild_item(
                            obj,
                            new_title,
                            review=review,
                            kaltura_id=wanted_kaltura,
                            set_kaltura=set_kaltura,
                        )
                        obj.clear()
                        obj.update(rebuilt)
                        updated += 1
                        if review and (title_changed or needs_review_key):
                            review_flagged += 1
                        if set_kaltura:
                            kaltura_updated += 1
                        if len(samples) < 8:
                            bits = []
                            if title_changed:
                                bits.append(f"title {title!r} -> {new_title!r}")
                            if set_kaltura:
                                bits.append(
                                    f"kaltura_id {current_kaltura!r} -> {wanted_kaltura!r}"
                                )
                            samples.append(f"  {media}: " + "; ".join(bits))
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
    print(
        f"media.yaml:   {media_yaml_path} "
        f"({len(media_titles)} titles, {len(media_kaltura)} kaltura_id)"
    )
    print(f"entries updated: {updated}")
    print(f"review:true set: {review_flagged}")
    print(f"kaltura_id set/updated: {kaltura_updated}")
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
