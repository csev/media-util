#!/usr/bin/env python3
"""Compare lessons.json YouTube ids/titles against youtube-playlist.jsonl."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402


def lesson_title_for_compare(title: str) -> str:
    """Drop catalog-only prefixes that often differ from YouTube wording."""
    text = title.strip()
    text = re.sub(r"^Review:\s*", "", text, flags=re.IGNORECASE)
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report mismatches between lessons.json and youtube-playlist.jsonl. "
            "Does not read or modify media.yaml."
        )
    )
    parser.add_argument(
        "--lessons",
        type=Path,
        default=None,
        help="Path to lessons.json (default: $LESSONS_JSON / $COURSE_ROOT/lessons.json)",
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
    return parser.parse_args()


YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?[^\"'\s]*v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
YOUTUBE_FIELD_RE = re.compile(r"^youtube", re.IGNORECASE)


def extract_youtube_id(value: str) -> str | None:
    """Return an 11-char YouTube id from a bare id or URL."""
    text = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    match = YOUTUBE_ID_RE.search(text)
    return match.group(1) if match else None


def collect_reference_youtube_ids(lessons_path: Path) -> dict[str, list[str]]:
    """Map youtube ids found outside media videos (e.g. reference hrefs)."""
    data = json.loads(lessons_path.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}

    def add(youtube_id: str, where: str) -> None:
        found.setdefault(youtube_id, []).append(where)

    for module in data.get("modules") or []:
        if not isinstance(module, dict):
            continue
        module_label = module.get("anchor") or module.get("title") or "?"
        for item in module.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type") or "item"
            title = item.get("title") or ""

            # Skip primary video+media youtube fields; those are tracked via media.
            has_media = isinstance(item.get("media"), str) and bool(item["media"].strip())
            for key, value in item.items():
                if not isinstance(value, str) or not value.strip():
                    continue
                if has_media and key == "youtube":
                    continue
                youtube_id = None
                if YOUTUBE_FIELD_RE.match(key):
                    youtube_id = extract_youtube_id(value)
                elif key in {"href", "url", "launch"}:
                    youtube_id = extract_youtube_id(value)
                if youtube_id:
                    add(youtube_id, f"{module_label}/{item_type}: {title or key}")
    return found


def load_lesson_videos(lessons_path: Path) -> list[dict[str, Any]]:
    """Return video items that have a media path."""
    try:
        data = json.loads(lessons_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Error: cannot read {lessons_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Error: malformed JSON in {lessons_path}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        raise SystemExit(
            f"Error: unexpected lessons schema in {lessons_path}: "
            "expected top-level object with modules list"
        )

    videos: list[dict[str, Any]] = []
    for module in data["modules"]:
        if not isinstance(module, dict):
            continue
        module_title = module.get("title") or module.get("anchor") or ""
        items = module.get("items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            media = item.get("media")
            if not isinstance(media, str) or not media.strip():
                continue
            media = media.strip().lstrip("./")
            if media.startswith("dj4e-media/"):
                media = media[len("dj4e-media/") :]
            title = item.get("title")
            title = title.strip() if isinstance(title, str) else ""
            youtube = item.get("youtube")
            youtube = youtube.strip() if isinstance(youtube, str) else ""
            videos.append(
                {
                    "module": module_title,
                    "media": media,
                    "title": title,
                    "youtube": youtube or None,
                }
            )
    return videos


def main() -> int:
    args = parse_args()
    bootstrap = common.load_bootstrap()

    lessons_path = args.lessons or common.default_lessons()
    playlist_path = args.youtube_playlist or common.default_youtube_jsonl()

    videos = load_lesson_videos(lessons_path)
    reference_ids = collect_reference_youtube_ids(lessons_path)
    playlist = bootstrap.load_youtube_playlist(playlist_path)
    if not playlist:
        raise SystemExit(f"Error: empty or missing playlist: {playlist_path}")

    by_id, by_title, _by_basename = bootstrap.index_youtube_playlist(playlist)

    # Prefer the first occurrence of each media path (lessons can repeat videos).
    unique_videos: dict[str, dict[str, Any]] = {}
    for video in videos:
        media = video["media"]
        if media not in unique_videos:
            unique_videos[media] = video
        elif not unique_videos[media]["youtube"] and video["youtube"]:
            unique_videos[media] = video

    print(
        f"lessons.json:     {lessons_path} "
        f"({len(unique_videos)} unique media / {len(videos)} item refs)"
    )
    print(
        f"                (+{len(reference_ids)} youtube id(s) from references/other fields)"
    )
    print(f"youtube-playlist: {playlist_path} ({len(playlist)} videos)")

    lesson_ids: dict[str, list[str]] = {}
    missing_youtube: list[str] = []
    id_not_in_playlist: list[str] = []
    title_mismatch: list[str] = []
    matchable_missing: list[str] = []
    id_vs_title_match: list[str] = []

    for media, video in sorted(unique_videos.items()):
        title = video["title"]
        compare_title = lesson_title_for_compare(title) if title else ""
        youtube_id = video["youtube"]
        label = f"{media}"
        if title:
            label = f"{media}  lessons_title={title}"

        if not youtube_id:
            missing_youtube.append(label)
            if compare_title:
                matched = None
                normalized = bootstrap.normalize_title(compare_title)
                if normalized in by_title:
                    matched = by_title[normalized]
                else:
                    for yt_norm, entry in by_title.items():
                        if normalized and (
                            normalized in yt_norm or yt_norm in normalized
                        ):
                            matched = entry
                            break
                if matched is not None:
                    matchable_missing.append(
                        f"{media}  could match {matched.get('id')} "
                        f"({matched.get('title')})"
                    )
            continue

        lesson_ids.setdefault(youtube_id, []).append(media)

        if youtube_id not in by_id:
            id_not_in_playlist.append(f"{media}  youtube={youtube_id}")
            continue

        yt_entry = by_id[youtube_id]
        yt_title = yt_entry.get("title") or ""
        if compare_title and isinstance(yt_title, str) and yt_title.strip():
            if not bootstrap.titles_compatible(compare_title, yt_title):
                title_mismatch.append(
                    f"{media}\n"
                    f"    lessons:  {title}\n"
                    f"    youtube:  {yt_title}\n"
                    f"    id:       {youtube_id}"
                )

        # Lesson id present, but a different playlist video matches the title better.
        if compare_title:
            normalized = bootstrap.normalize_title(compare_title)
            title_hit = by_title.get(normalized)
            if title_hit is None:
                for yt_norm, entry in by_title.items():
                    if normalized and (normalized in yt_norm or yt_norm in normalized):
                        title_hit = entry
                        break
            if title_hit is not None and str(title_hit.get("id")) != youtube_id:
                id_vs_title_match.append(
                    f"{media}  lessons_youtube={youtube_id}  "
                    f"title_match={title_hit.get('id')} ({title_hit.get('title')})"
                )

    referenced_ids = set(lesson_ids) | set(reference_ids)

    unused_playlist: list[str] = []
    for youtube_id, entry in sorted(
        by_id.items(), key=lambda item: item[1].get("playlist_index") or 0
    ):
        if youtube_id not in referenced_ids:
            unused_playlist.append(f"{youtube_id}  {entry.get('title') or ''}")

    shared_id_conflicts: list[str] = []
    for youtube_id, medias in sorted(lesson_ids.items()):
        unique = sorted(set(medias))
        if len(unique) > 1:
            shared_id_conflicts.append(f"{youtube_id}")
            for media in unique:
                shared_id_conflicts.append(f"    - {media}")

    problems = 0
    problems += common.section(
        "lessons.json youtube id not in playlist", id_not_in_playlist
    )
    problems += common.section(
        "lessons.json title incompatible with playlist title for same youtube id",
        title_mismatch,
    )
    problems += common.section(
        "lessons.json youtube id differs from best playlist title match",
        id_vs_title_match,
    )
    problems += common.section(
        "Same youtube id used by multiple lessons.json media paths",
        shared_id_conflicts,
    )
    problems += common.section(
        "lessons.json media videos with no youtube id", missing_youtube
    )
    problems += common.section(
        "lessons.json media with no youtube id but title looks matchable",
        matchable_missing,
    )
    problems += common.section(
        "Playlist videos not referenced by lessons.json (video youtube or reference href)",
        unused_playlist,
    )

    print(f"\nlessons.json media videos with youtube id: {len(lesson_ids)}")
    print(f"lessons.json non-media youtube references: {len(reference_ids)}")
    print(f"playlist videos referenced by lessons: {len(referenced_ids & set(by_id))}")

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
