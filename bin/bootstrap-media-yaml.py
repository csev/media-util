#!/usr/bin/env python3
"""Bootstrap and refresh media.yaml from lessons.json and a media tree.

By default the inventory is scanned from MEDIA_ROOT / --media-root
(.mov / .mp4 / .m4v). Pass --files only to use an explicit list.

Descriptions prefer WHISPER_ROOT/desc (whisper-desc) when present, else the
YouTube playlist description. Titles are composed as
``[<TITLE_PREFIX> nn.mm ]<AI title> (duration)`` from lessons.json +
whisper/desc (``TITLE_PREFIX`` from media.env; empty means no course prefix —
CC4E style). When lessons titles lack ``TOKEN nn.mm``, ``nn`` / ``mm`` are the
ordinals of the media folder and of the file within that folder
(``TITLE_ORDINAL_START`` sets the first folder's ``nn``, default 1). The composed
lessons-based string is stored as ``title``; the
AI wording (when present) is stored separately as ``ai_title``. Course
EXTRA_TAGS / EXTRA_DESCRIPTION from media.env are appended
onto each entry's tags/description. youtube_id prefers a playlist match, then
the lessons.json youtube id for the same media path (even when unlisted / not
on the playlist), and preserves a media.yaml youtube_id that is already on the
playlist. Entry keys in media.yaml are always MEDIA_ROOT-relative
``folder/file`` paths (never a bare filename and never an extra prefix such as
``ca4e-media/folder/file``). When lessons.json uses a longer prefix, matching
is by unique ``folder/file`` suffix. When ``kaltura_id`` is null/empty and a
Kaltura playlist JSONL is present, fill it by matching playlist title to the
media ``folder/file``, filename/stem (Kaltura often uses the upload filename
as the title), or lesson title. Existing non-null ``kaltura_id`` values are
preserved. AI descriptions refresh whenever the desc file exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

# Same-directory helper (works when this file is loaded via importlib too).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_text import sanitize_youtube_tags, sanitize_youtube_text  # noqa: E402


# Defaults are relative to the course working directory, not this util repo.
CWD = Path.cwd()

ENTRY_KEYS = (
    "title",
    "ai_title",
    "youtube_id",
    "kaltura_id",
    "size",
    "md5",
    "duration",
    "duration_text",
    "container_creation",
    "qt_creation",
    "tags",
    "description",
)

PRESERVE_KEYS = ("description", "youtube_id", "kaltura_id", "ai_title")

# Top-level media.yaml keys mirrored from media.env.
GLOBAL_KEYS = (
    "course_root",
    "media_root",
    "whisper_root",
    "youtube_dir",
    "youtube_playlist",
    "kaltura_partner_id",
    "kaltura_service_url",
    "kaltura_category_id",
    "kaltura_playlist_id",
    "course_hint",
    "title_prefix",
    "title_ordinal_start",
    "extra_tags",
    "extra_description",
)

REVIEW_MARKER_RE = re.compile(r"Review:|\(\s*review\s*\)", re.IGNORECASE)
# Numbered course prefixes like "DJ 01.01" (token comes from TITLE_PREFIX).
TRAILING_DURATION_RE = re.compile(r"\s*\((?:\d+:)+\d+\)\s*$")


def title_prefix_token() -> str:
    """Course title prefix token from media.env (e.g. ``DJ``). Empty = none."""
    return (os.environ.get("TITLE_PREFIX") or "").strip()


def title_ordinal_start() -> int:
    """Starting folder ordinal ``nn`` for ``TOKEN nn.mm`` (from media.env).

    ``TITLE_ORDINAL_START`` defaults to ``1`` (DJ 01.01 style). Set to ``0`` for
    courses that want the first media folder numbered ``00``.
    """
    raw = (os.environ.get("TITLE_ORDINAL_START") or "1").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(
            f"Error: TITLE_ORDINAL_START must be an integer, got {raw!r}"
        ) from exc
    if value < 0:
        raise SystemExit(f"Error: TITLE_ORDINAL_START must be >= 0, got {value}")
    return value


def numbered_prefix_re(token: str | None = None) -> re.Pattern[str] | None:
    """Match ``TOKEN nn.mm`` at the start of a title, if TOKEN is set."""
    token = (token if token is not None else title_prefix_token()).strip()
    if not token:
        return None
    return re.compile(rf"^({re.escape(token)}\s+\d+\.\d+)\b", re.IGNORECASE)


def is_review_title(title: str) -> bool:
    """True when a lessons.json title marks a listing as a review.

    Review is a lessons.json-only concept; it is not stored in media.yaml.
    Matches leading ``Review…``, embedded ``Review:``, or ``(review)``.
    """
    text = title.strip()
    if text.lower().startswith("review"):
        return True
    return bool(REVIEW_MARKER_RE.search(text))


def title_without_review_marker(title: str) -> str:
    """Strip Review: / (review) markers for lessons.json conflict comparison."""
    text = title.strip()
    text = re.sub(r"^Review:\s*", "", text, flags=re.IGNORECASE)
    token = title_prefix_token()
    if token:
        text = re.sub(
            rf"^({re.escape(token)}\s+\d+\.\d+)\s+Review:\s*",
            r"\1 ",
            text,
            flags=re.IGNORECASE,
        )
    else:
        # Still recognize legacy "DJ nn.mm Review:" when no prefix is configured.
        text = re.sub(
            r"^(DJ\s+\d+\.\d+)\s+Review:\s*",
            r"\1 ",
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(r"\(\s*review\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def env_or_none(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def apply_course_globals(data: CommentedMap, args: argparse.Namespace) -> None:
    """Write media.env-backed globals onto the YAML root map."""
    data["course_root"] = str(args.course_root)
    data["media_root"] = str(args.media_root)
    data["whisper_root"] = env_or_none("WHISPER_ROOT")
    data["youtube_dir"] = env_or_none("YOUTUBE_DIR")
    data["youtube_playlist"] = env_or_none("YOUTUBE_PLAYLIST")
    data["kaltura_partner_id"] = env_or_none("KALTURA_PARTNER_ID")
    data["kaltura_service_url"] = env_or_none("KALTURA_SERVICE_URL")
    data["kaltura_category_id"] = env_or_none("KALTURA_CATEGORY_ID")
    data["kaltura_playlist_id"] = env_or_none("KALTURA_PLAYLIST_ID")
    data["course_hint"] = env_or_none("COURSE_HINT")
    data["title_prefix"] = title_prefix_token() or None
    data["title_ordinal_start"] = title_ordinal_start() if title_prefix_token() else None
    data["extra_tags"] = env_or_none("EXTRA_TAGS")
    data["extra_description"] = env_or_none("EXTRA_DESCRIPTION")
    # Drop legacy www_root if present from older media.yaml files.
    if "www_root" in data:
        del data["www_root"]


def order_root_map(data: CommentedMap) -> CommentedMap:
    """Keep globals then entries first; preserve any other top-level keys after."""
    expected_prefix = list(GLOBAL_KEYS) + ["entries"]
    root_keys = list(data.keys())
    if root_keys[: len(expected_prefix)] == expected_prefix:
        return data

    ordered = CommentedMap()
    for key in GLOBAL_KEYS:
        ordered[key] = data.get(key)
    ordered["entries"] = data.get("entries") or CommentedMap()
    for key, value in data.items():
        if key in GLOBAL_KEYS or key == "entries":
            continue
        ordered[key] = value
    return ordered


def build_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)

    def represent_none(representer, _data):
        return representer.represent_scalar("tag:yaml.org,2002:null", "null")

    yaml.representer.add_representer(type(None), represent_none)
    return yaml


def default_kaltura_playlist_jsonl() -> Path:
    value = os.environ.get("KALTURA_PLAYLIST_JSONL")
    if value:
        return Path(value)
    kaltura_dir = os.environ.get("KALTURA_DIR")
    if kaltura_dir:
        return Path(kaltura_dir) / "kaltura-playlist.jsonl"
    course_root = os.environ.get("COURSE_ROOT")
    if course_root:
        return Path(course_root) / "kaltura" / "kaltura-playlist.jsonl"
    return CWD / "kaltura" / "kaltura-playlist.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    media_root_default = os.environ.get("MEDIA_ROOT")
    youtube_jsonl_default = os.environ.get("YOUTUBE_PLAYLIST_JSONL")
    if not youtube_jsonl_default:
        youtube_dir = os.environ.get("YOUTUBE_DIR")
        if youtube_dir:
            youtube_jsonl_default = str(Path(youtube_dir) / "youtube-playlist.jsonl")
        else:
            youtube_jsonl_default = str(CWD / "youtube" / "youtube-playlist.jsonl")
    kaltura_jsonl_default = str(default_kaltura_playlist_jsonl())

    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap or refresh media.yaml from lessons and media files. "
            "Run from a course repository (or pass explicit paths)."
        )
    )
    parser.add_argument(
        "--lessons",
        type=Path,
        default=CWD / "lessons.json",
        help=f"Path to lessons.json (default: {CWD / 'lessons.json'})",
    )
    parser.add_argument(
        "--files",
        type=Path,
        default=None,
        help=(
            "Optional explicit media filename list. "
            "Default: scan --media-root for .mov/.mp4/.m4v"
        ),
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=Path(media_root_default) if media_root_default else None,
        required=media_root_default is None,
        help=(
            "Root directory containing media files "
            "(required, or set MEDIA_ROOT)"
        ),
    )
    course_root_default = os.environ.get("COURSE_ROOT") or str(CWD)
    parser.add_argument(
        "--course-root",
        type=Path,
        default=Path(course_root_default),
        help=(
            f"Course repository root "
            f"(default: $COURSE_ROOT or {CWD})"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=CWD / "media.yaml",
        help=f"Output YAML path (default: {CWD / 'media.yaml'})",
    )
    parser.add_argument(
        "--force-title",
        action="store_true",
        help=(
            "Always overwrite title from lessons.json (or stem fallback). "
            "Without this flag, existing titles are preserved for unmatched files."
        ),
    )
    parser.add_argument(
        "--youtube-playlist",
        type=Path,
        default=Path(youtube_jsonl_default),
        help=(
            "yt-dlp JSONL dump of the course playlist "
            f"(default: {youtube_jsonl_default}; "
            "from YOUTUBE_PLAYLIST_JSONL / YOUTUBE_DIR when set)"
        ),
    )
    parser.add_argument(
        "--force-youtube",
        action="store_true",
        help=(
            "Overwrite existing youtube_id using playlist match, else "
            "lessons.json youtube id; also refresh description when no AI "
            "whisper/desc file is present. Without this flag, a media.yaml "
            "youtube_id that is already on the playlist is kept; otherwise "
            "playlist then lessons.json fill the id. AI descriptions always "
            "refresh when present."
        ),
    )
    parser.add_argument(
        "--kaltura-playlist",
        type=Path,
        default=Path(kaltura_jsonl_default),
        help=(
            "JSONL dump from dump-kaltura-playlist.py "
            f"(default: {kaltura_jsonl_default}; "
            "from KALTURA_PLAYLIST_JSONL / KALTURA_DIR when set)"
        ),
    )
    parser.add_argument(
        "--force-kaltura",
        action="store_true",
        help=(
            "Overwrite existing kaltura_id using playlist match, else "
            "lessons.json kaltura_id. Without this flag, only null/empty "
            "kaltura_id values are filled."
        ),
    )
    return parser.parse_args(argv)


def load_lessons_media_map(
    lessons_path: Path,
    relevant: set[str] | None = None,
    *,
    strict: bool = True,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    """Return title/youtube/kaltura maps and conflict dicts.

    Schema (inspected): top-level ``modules`` list; each module has ``items``.
    Items may include ``media``, ``title``, ``youtube``, and ``kaltura_id``.

    Identical reuses are allowed. Conflicting titles/youtube IDs for the same
    media path are collected; when ``strict`` is true they raise SystemExit
    (for paths in ``relevant`` when provided).

    When the same media appears as both a primary and a Review: listing in
    lessons.json, prefer the non-review wording (Review stays lessons-only).
    """
    try:
        text = lessons_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Error: cannot read lessons file {lessons_path}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Error: malformed JSON in {lessons_path}: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc

    if not isinstance(data, dict) or "modules" not in data:
        raise SystemExit(
            f"Error: unexpected lessons schema in {lessons_path}: "
            "expected a top-level object with a 'modules' list"
        )

    modules = data["modules"]
    if not isinstance(modules, list):
        raise SystemExit(
            f"Error: unexpected lessons schema in {lessons_path}: "
            "'modules' must be a list"
        )

    title_map: dict[str, str] = {}
    youtube_map: dict[str, str] = {}
    kaltura_map: dict[str, str] = {}
    title_conflicts: dict[str, set[str]] = {}
    youtube_conflicts: dict[str, set[str]] = {}
    kaltura_conflicts: dict[str, set[str]] = {}

    for module in modules:
        if not isinstance(module, dict):
            continue
        items = module.get("items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            media = item.get("media")
            if not media:
                continue
            if not isinstance(media, str):
                raise SystemExit(
                    f"Error: non-string media path in {lessons_path}: {media!r}"
                )
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                raise SystemExit(
                    f"Error: media {media!r} in {lessons_path} has missing/empty title"
                )
            title = title.strip()
            if media in title_map:
                if title_map[media] != title:
                    same_base = (
                        title_without_review_marker(title_map[media])
                        == title_without_review_marker(title)
                    )
                    if same_base:
                        # Prefer the non-review wording for media.yaml.
                        if is_review_title(title_map[media]) and not is_review_title(
                            title
                        ):
                            title_map[media] = title
                    else:
                        title_conflicts.setdefault(media, {title_map[media]}).add(title)
            else:
                title_map[media] = title

            youtube = item.get("youtube")
            if isinstance(youtube, str) and youtube.strip():
                youtube = youtube.strip()
                if media in youtube_map:
                    if youtube_map[media] != youtube:
                        youtube_conflicts.setdefault(media, {youtube_map[media]}).add(
                            youtube
                        )
                else:
                    youtube_map[media] = youtube

            kaltura = item.get("kaltura_id")
            if isinstance(kaltura, str) and kaltura.strip():
                kaltura = kaltura.strip()
                if media in kaltura_map:
                    if kaltura_map[media] != kaltura:
                        kaltura_conflicts.setdefault(media, {kaltura_map[media]}).add(
                            kaltura
                        )
                else:
                    kaltura_map[media] = kaltura

    if relevant is not None:
        title_conflicts = {
            k: v for k, v in title_conflicts.items() if lessons_path_relevant(k, relevant)
        }
        youtube_conflicts = {
            k: v
            for k, v in youtube_conflicts.items()
            if lessons_path_relevant(k, relevant)
        }
        kaltura_conflicts = {
            k: v
            for k, v in kaltura_conflicts.items()
            if lessons_path_relevant(k, relevant)
        }

    if strict and title_conflicts:
        lines = [
            f"Error: duplicate filename mappings with conflicting titles "
            f"in {lessons_path}:"
        ]
        for media, titles in sorted(title_conflicts.items()):
            lines.append(f"  {media}:")
            for title in sorted(titles):
                lines.append(f"    - {title}")
        raise SystemExit("\n".join(lines))

    if strict and youtube_conflicts:
        lines = [
            f"Error: duplicate filename mappings with conflicting youtube IDs "
            f"in {lessons_path}:"
        ]
        for media, ids in sorted(youtube_conflicts.items()):
            lines.append(f"  {media}:")
            for youtube_id in sorted(ids):
                lines.append(f"    - {youtube_id}")
        raise SystemExit("\n".join(lines))

    if strict and kaltura_conflicts:
        lines = [
            f"Error: duplicate filename mappings with conflicting kaltura IDs "
            f"in {lessons_path}:"
        ]
        for media, ids in sorted(kaltura_conflicts.items()):
            lines.append(f"  {media}:")
            for kaltura_id in sorted(ids):
                lines.append(f"    - {kaltura_id}")
        raise SystemExit("\n".join(lines))

    return (
        title_map,
        youtube_map,
        kaltura_map,
        title_conflicts,
        youtube_conflicts,
        kaltura_conflicts,
    )


def folder_file_key(path: str) -> str:
    """Return the canonical ``folder/file`` (or bare file) form of a media path.

    media.yaml keys and MEDIA_ROOT inventory use this shape — never an extra
    leading prefix such as ``ca4e-media/folder/file``.
    """
    parts = [part for part in Path(path).as_posix().strip("/").split("/") if part]
    if not parts:
        return path
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[0]


def lessons_path_relevant(lessons_path: str, relevant: set[str]) -> bool:
    """True if a lessons.json media key corresponds to a MEDIA_ROOT relative path.

    Matches exact path or unique shared ``folder/file``
    (e.g. lessons ``ca4e-media/01-Origins/foo.m4v`` vs root ``01-Origins/foo.m4v``).
    """
    if lessons_path in relevant:
        return True
    key = folder_file_key(lessons_path)
    if key in relevant:
        return True
    return any(folder_file_key(rel) == key for rel in relevant)


def build_lessons_folder_file_index(lessons_map: dict[str, Any]) -> dict[str, str]:
    """Map ``folder/file`` -> lessons media key when that key is unique."""
    groups: dict[str, list[str]] = {}
    for key in lessons_map:
        groups.setdefault(folder_file_key(key), []).append(key)
    return {ff: keys[0] for ff, keys in groups.items() if len(keys) == 1}


def match_lessons_media_key(
    rel_path: str,
    lessons_map: dict[str, Any],
    folder_file_index: dict[str, str] | None = None,
) -> str | None:
    """Return the lessons.json media key for a MEDIA_ROOT ``folder/file`` path.

    Order:
      1. Exact path match
      2. Unique ``folder/file`` match (strips a longer lessons prefix)
    """
    if rel_path in lessons_map:
        return rel_path
    index = folder_file_index
    if index is None:
        index = build_lessons_folder_file_index(lessons_map)
    return index.get(folder_file_key(rel_path))


def lookup_lessons_media(
    rel_path: str,
    lessons_map: dict[str, Any],
    folder_file_index: dict[str, str] | None = None,
) -> Any | None:
    """Look up a lessons.json value for a MEDIA_ROOT ``folder/file`` path."""
    key = match_lessons_media_key(rel_path, lessons_map, folder_file_index)
    if key is None:
        return None
    return lessons_map[key]


def normalize_title(title: str) -> str:
    """Normalize titles for comparison (drop course prefix and trailing times)."""
    text = title.strip()
    token = title_prefix_token()
    if token:
        text = re.sub(
            rf"^{re.escape(token)}\s+\d+\.\d+\s+",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(rf"^{re.escape(token)}\s+", "", text, flags=re.IGNORECASE)
    else:
        # Also drop legacy bare / numbered DJ when no prefix is configured.
        text = re.sub(r"^DJ\s+\d+\.\d+\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^DJ\s+", "", text, flags=re.IGNORECASE)
    while True:
        match = re.search(r"\s*\((\d{1,2}:\d{2}|\d+\.\d{2})\)\s*$", text)
        if not match:
            break
        text = text[: match.start()].rstrip()
    text = text.replace("'", "").replace('"', "")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def titles_compatible(lesson_title: str, youtube_title: str) -> bool:
    left = normalize_title(lesson_title)
    right = normalize_title(youtube_title)
    if not left or not right:
        return False
    if left == right:
        return True
    # Allow wording drift (e.g. "Django Data Models" vs "Introduction to Django Models")
    if left in right or right in left:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= min(3, len(left_tokens), len(right_tokens))


def load_youtube_playlist(playlist_path: Path) -> list[dict[str, Any]]:
    if not playlist_path.exists():
        warnings.warn(
            f"YouTube playlist file not found: {playlist_path}; "
            "youtube_id/description will not be filled from YouTube",
            UserWarning,
            stacklevel=2,
        )
        return []

    entries: list[dict[str, Any]] = []
    try:
        lines = playlist_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(
            f"Error: cannot read YouTube playlist {playlist_path}: {exc}"
        ) from exc

    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"Error: malformed JSONL in {playlist_path} line {lineno}: {exc.msg}"
            ) from exc
        if not isinstance(obj, dict) or not obj.get("id"):
            warnings.warn(
                f"Skipping playlist line {lineno}: missing id",
                UserWarning,
                stacklevel=2,
            )
            continue
        entries.append(obj)
    return entries


def index_youtube_playlist(
    playlist: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Index playlist entries by id, normalized title, and optional basename.

    Essential playlist JSONL fields: id, title, description, duration,
    playlist_index. A filename/_filename field is optional; when present, the
    basename (folder may be missing) is also indexed for matching.
    """
    by_id: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    by_basename: dict[str, dict[str, Any]] = {}

    for entry in playlist:
        youtube_id = str(entry["id"])
        by_id[youtube_id] = entry

        title = entry.get("title") or entry.get("fulltitle") or ""
        if isinstance(title, str) and title.strip():
            by_title.setdefault(normalize_title(title), entry)

        for key in ("filename", "_filename"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            # Folder may be absent in YouTube/yt-dlp filenames.
            basename = Path(value).name
            by_basename.setdefault(basename, entry)
            by_basename.setdefault(Path(basename).stem, entry)

    return by_id, by_title, by_basename


def match_youtube_entry(
    rel_name: str,
    *,
    lesson_title: str | None,
    lesson_youtube_id: str | None,
    by_id: dict[str, dict[str, Any]],
    by_title: dict[str, dict[str, Any]],
    by_basename: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Match a media file to a playlist entry via id, title, then basename."""
    if lesson_youtube_id and lesson_youtube_id in by_id:
        entry = by_id[lesson_youtube_id]
        yt_title = entry.get("title") or ""
        if lesson_title and isinstance(yt_title, str) and yt_title.strip():
            if not titles_compatible(lesson_title, yt_title):
                warnings.warn(
                    f"YouTube title differs for {rel_name!r}: "
                    f"lesson={lesson_title!r} youtube={yt_title!r} "
                    f"(keeping youtube id {lesson_youtube_id})",
                    UserWarning,
                    stacklevel=2,
                )
        return entry

    if lesson_title:
        normalized = normalize_title(lesson_title)
        if normalized in by_title:
            return by_title[normalized]
        for yt_norm, entry in by_title.items():
            if normalized and (normalized in yt_norm or yt_norm in normalized):
                return entry

    basename = Path(rel_name).name
    stem = Path(rel_name).stem
    for key in (basename, stem):
        if key in by_basename:
            return by_basename[key]

    return None


def normalize_youtube_id(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_kaltura_id(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def load_kaltura_playlist(playlist_path: Path) -> list[dict[str, Any]]:
    """Load dump-kaltura-playlist.py JSONL; missing file → empty (no error)."""
    if not playlist_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    try:
        lines = playlist_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(
            f"Error: cannot read Kaltura playlist {playlist_path}: {exc}"
        ) from exc

    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"Error: malformed JSONL in {playlist_path} line {lineno}: {exc.msg}"
            ) from exc
        if not isinstance(obj, dict) or not obj.get("id"):
            warnings.warn(
                f"Skipping Kaltura playlist line {lineno}: missing id",
                UserWarning,
                stacklevel=2,
            )
            continue
        entries.append(obj)
    return entries


def _index_kaltura_name(index: dict[str, dict[str, Any]], name: str, entry: dict[str, Any]) -> None:
    text = name.strip()
    if not text:
        return
    index.setdefault(text, entry)
    index.setdefault(text.lower(), entry)
    path = Path(text)
    index.setdefault(path.name, entry)
    index.setdefault(path.name.lower(), entry)
    index.setdefault(path.stem, entry)
    index.setdefault(path.stem.lower(), entry)
    ff = folder_file_key(text)
    index.setdefault(ff, entry)
    index.setdefault(ff.lower(), entry)


def index_kaltura_playlist(
    playlist: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Index Kaltura playlist by id and by title/filename/folder-file keys."""
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}

    for entry in playlist:
        kaltura_id = str(entry["id"])
        by_id[kaltura_id] = entry
        title = entry.get("title") or entry.get("name") or ""
        if isinstance(title, str):
            _index_kaltura_name(by_name, title, entry)
            # Also index normalized lesson-style titles when present.
            normalized = normalize_title(title)
            if normalized:
                by_name.setdefault(normalized, entry)

    return by_id, by_name


def match_kaltura_entry(
    rel_name: str,
    *,
    lesson_title: str | None,
    lesson_kaltura_id: str | None,
    by_id: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Match media to a Kaltura playlist entry via id, path, filename, or title."""
    if lesson_kaltura_id and lesson_kaltura_id in by_id:
        return by_id[lesson_kaltura_id]

    candidates = [
        rel_name,
        folder_file_key(rel_name),
        Path(rel_name).name,
        Path(rel_name).stem,
    ]
    for key in candidates:
        if not key:
            continue
        if key in by_name:
            return by_name[key]
        lower = key.lower()
        if lower in by_name:
            return by_name[lower]

    if lesson_title:
        normalized = normalize_title(lesson_title)
        if normalized and normalized in by_name:
            return by_name[normalized]

    return None


def resolve_kaltura_id(
    rel_name: str,
    *,
    existing_kaltura_id: Any,
    lesson_kaltura_id: str | None,
    lesson_title: str | None,
    by_id: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    force: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    """Choose kaltura_id for a media path.

    Priority:
      1. Existing media.yaml id (kept when set, unless ``force``)
      2. Playlist match via id / folder/file / filename / stem / title
      3. lessons.json kaltura_id
    """
    existing = normalize_kaltura_id(existing_kaltura_id)
    lesson_id = normalize_kaltura_id(lesson_kaltura_id)

    if existing and not force:
        return existing, by_id.get(existing)

    lookup_id = existing if (existing and existing in by_id) else lesson_id
    entry = match_kaltura_entry(
        rel_name,
        lesson_title=lesson_title,
        lesson_kaltura_id=lookup_id,
        by_id=by_id,
        by_name=by_name,
    )
    playlist_id = normalize_kaltura_id(entry.get("id") if entry else None)

    if force:
        return playlist_id or lesson_id or existing, entry

    if playlist_id:
        return playlist_id, entry
    if lesson_id:
        return lesson_id, entry
    return existing, entry


def resolve_youtube_id(
    rel_name: str,
    *,
    existing_youtube_id: Any,
    lesson_youtube_id: str | None,
    lesson_title: str | None,
    by_id: dict[str, dict[str, Any]],
    by_title: dict[str, dict[str, Any]],
    by_basename: dict[str, dict[str, Any]],
    force: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    """Choose youtube_id for a media path.

    Priority:
      1. Playlist match — via media.yaml id already on the playlist, lessons.json
         id on the playlist, title, or basename
      2. lessons.json youtube id for this exact media path (even if unlisted /
         absent from the playlist, e.g. copyright extras)
      3. Existing media.yaml youtube_id

    When media.yaml already stores an id that is on the playlist, lessons.json
    does not override it unless ``force`` is set.
    """
    existing = normalize_youtube_id(existing_youtube_id)
    lesson_id = normalize_youtube_id(lesson_youtube_id)

    if existing and existing in by_id:
        lookup_id = existing
    elif lesson_id and lesson_id in by_id:
        lookup_id = lesson_id
    else:
        lookup_id = lesson_id

    yt_entry = match_youtube_entry(
        rel_name,
        lesson_title=lesson_title,
        lesson_youtube_id=lookup_id,
        by_id=by_id,
        by_title=by_title,
        by_basename=by_basename,
    )
    playlist_id = normalize_youtube_id(yt_entry.get("id") if yt_entry else None)

    if force:
        chosen = playlist_id or lesson_id or existing
        return chosen, yt_entry

    # Already-on-playlist id in media.yaml wins over lessons.json.
    if existing and existing in by_id:
        return existing, yt_entry or by_id.get(existing)

    if playlist_id:
        return playlist_id, yt_entry

    if lesson_id:
        return lesson_id, yt_entry

    return existing, yt_entry


def empty_value(value: Any) -> bool:
    return value is None or value == ""


def choose_field(existing: Any, incoming: Any, *, force: bool) -> Any:
    if force or empty_value(existing):
        return incoming if not empty_value(incoming) else existing
    return existing


def as_literal_description(value: Any) -> Any:
    """Store descriptions as YAML literal block scalars (|)."""
    if value is None:
        return None
    if isinstance(value, LiteralScalarString):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip("\n")
    if not text:
        return None
    # Literal scalars look cleaner with a trailing newline.
    if not text.endswith("\n"):
        text = text + "\n"
    return LiteralScalarString(text)


def resolve_whisper_root(args: argparse.Namespace) -> Path | None:
    value = env_or_none("WHISPER_ROOT")
    if value:
        return Path(value)
    course = getattr(args, "course_root", None)
    if course is not None:
        candidate = Path(course) / "whisper"
        if candidate.is_dir():
            return candidate
    return None


def normalize_tags(value: Any) -> str | None:
    """Normalize tags to a comma-separated string, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        tags = [t.strip() for t in value.split(",") if t.strip()]
        text = ", ".join(tags) if tags else None
    elif isinstance(value, list):
        tags = [str(t).strip() for t in value if str(t).strip()]
        text = ", ".join(tags) if tags else None
    else:
        return None
    if text is None:
        return None
    cleaned = sanitize_youtube_tags(text)
    return cleaned or None


def merge_extra_tags(tags: str | None, extra_tags: str | None) -> str | None:
    """Append course EXTRA_TAGS onto an entry tag string (deduped)."""
    extras = normalize_tags(extra_tags)
    if not extras:
        return tags
    base_parts = [t.strip() for t in (tags or "").split(",") if t.strip()]
    seen = {t.casefold() for t in base_parts}
    for tag in extras.split(","):
        tag = tag.strip()
        if not tag:
            continue
        if tag.casefold() in seen:
            continue
        base_parts.append(tag)
        seen.add(tag.casefold())
    return ", ".join(base_parts) if base_parts else None


def merge_extra_description(
    description: str | None, extra_description: str | None
) -> str | None:
    """Append course EXTRA_DESCRIPTION onto an entry description if missing."""
    if not extra_description or not str(extra_description).strip():
        return description
    extra = sanitize_youtube_text(str(extra_description).strip())
    if not extra:
        return description
    body = (description or "").rstrip()
    if body and extra.casefold() in body.casefold():
        return description
    if body:
        return f"{body}\n\n{extra}"
    return extra


def load_ai_metadata(
    whisper_root: Path | None, rel_media: str
) -> tuple[str | None, str | None, str | None]:
    """Read whisper-desc output: title / tags / description (blank-line separated).

    Returns (title, tags, description). Tags are a comma-separated string.
    Any field may be None if missing/unusable.
    """
    if whisper_root is None:
        return None, None, None
    stem = Path(rel_media).with_suffix("").as_posix()
    path = whisper_root / "desc" / f"{stem}.txt"
    if not path.is_file():
        return None, None, None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None, None, None
    if not text:
        return None, None, None
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) < 3:
        return None, None, None

    ai_title = parts[0].split("\n", 1)[0].strip() or None
    tags = normalize_tags(parts[1])
    description = "\n\n".join(parts[2:]).strip()
    if ai_title:
        ai_title = sanitize_youtube_text(ai_title)
    if description:
        description = sanitize_youtube_text(description)
    return ai_title, tags, (description or None)


def format_title_duration(seconds: int) -> str:
    """Format seconds for titles: m:ss or h:mm:ss (no leading zero on minutes)."""
    if seconds < 0:
        seconds = 0
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def extract_title_prefix(title: str) -> str | None:
    """Return numbered prefix (e.g. ``DJ 01.01``) from a lessons title, if any."""
    token = title_prefix_token()
    pattern = numbered_prefix_re(token)
    if pattern is None:
        return None
    match = pattern.match(title.strip())
    return match.group(1) if match else None


def build_media_ordinal_prefixes(media_files: list[str]) -> dict[str, str]:
    """Map each MEDIA_ROOT ``folder/file`` path to ``TOKEN nn.mm``.

    ``nn`` is the folder ordinal (sorted order of unique parent directories in
    ``media_files``), starting at ``TITLE_ORDINAL_START`` (default 1). ``mm`` is
    the 1-based ordinal of the file within that folder (inventory order).
    No-op when ``TITLE_PREFIX`` is empty.
    """
    token = title_prefix_token()
    if not token:
        return {}

    folder_start = title_ordinal_start()

    folders: list[str] = []
    by_folder: dict[str, list[str]] = {}
    for rel in media_files:
        folder = Path(rel).parent.as_posix()
        if folder == ".":
            folder = ""
        if folder not in by_folder:
            folders.append(folder)
            by_folder[folder] = []
        by_folder[folder].append(rel)

    prefixes: dict[str, str] = {}
    for offset, folder in enumerate(folders):
        nn = folder_start + offset
        for mm, rel in enumerate(by_folder[folder], start=1):
            prefixes[rel] = f"{token} {nn:02d}.{mm:02d}"
    return prefixes


def clean_title_body(text: str) -> str:
    """Strip course prefix, Review marker, and trailing (duration) from a title body."""
    body = text.strip()
    token = title_prefix_token()
    pattern = numbered_prefix_re(token)
    if pattern is not None:
        body = pattern.sub("", body).strip()
        body = re.sub(rf"^{re.escape(token)}\b\s*", "", body, flags=re.IGNORECASE).strip()
    else:
        # Drop leftover DJ prefixes when TITLE_PREFIX is empty (e.g. CC4E).
        body = re.sub(r"^DJ\s+\d+\.\d+\s+", "", body, flags=re.IGNORECASE).strip()
        body = re.sub(r"^DJ\b\s*", "", body, flags=re.IGNORECASE).strip()
    body = re.sub(r"^Review:\s*", "", body, flags=re.IGNORECASE).strip()
    body = re.sub(r"\(\s*review\s*\)", "", body, flags=re.IGNORECASE).strip()
    body = TRAILING_DURATION_RE.sub("", body).strip()
    body = re.sub(r"\s+", " ", body).strip()
    return body


def compose_media_title(
    lesson_title: str,
    ai_title: str | None,
    duration_seconds: int,
    *,
    numbered_prefix: str | None = None,
    lessons_title_for_prefix: str | None = None,
) -> str:
    """Build ``[<TITLE_PREFIX> [nn.mm] ]<AI title> (duration)``.

    When ``TITLE_PREFIX`` is set (e.g. ``CA`` / ``DJ``), prefer a numbered
    prefix already present on the lessons.json title
    (``lessons_title_for_prefix``), else an ordinal ``TOKEN nn.mm`` from media
    folder/file order (``TITLE_ORDINAL_START``), else the bare token. When
    ``TITLE_PREFIX`` is empty, omit any course prefix (CC4E style).

    ``lesson_title`` supplies the title body (may be an existing media.yaml
    title or stem fallback); it is not used for numbered-prefix extraction so
    a stale ``TOKEN nn.mm`` on an existing entry cannot override ordinals.
    """
    token = title_prefix_token()
    prefix_source = lessons_title_for_prefix if lessons_title_for_prefix else ""
    prefix = extract_title_prefix(prefix_source) if token else None
    if token and not prefix:
        prefix = numbered_prefix or token
    body = clean_title_body(ai_title) if ai_title else ""
    if not body:
        body = clean_title_body(lesson_title)
    if not body:
        body = "Untitled"
    duration = format_title_duration(duration_seconds)
    if prefix:
        return sanitize_youtube_text(f"{prefix} {body} ({duration})")
    return sanitize_youtube_text(f"{body} ({duration})")


def load_media_files(files_path: Path) -> list[str]:
    try:
        lines = files_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"Error: cannot read file list {files_path}: {exc}") from exc

    result: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        if name in seen:
            raise SystemExit(f"Error: duplicate media filename in {files_path}: {name}")
        seen.add(name)
        result.append(name)
    return result


MEDIA_SUFFIXES = {".mov", ".mp4", ".m4v"}
# Top-level directory under MEDIA_ROOT that holds retired masters.
# Scans never descend into it (compare / bootstrap / whisper inventory).
MEDIA_ARCHIVE_DIR = "archive"


def scan_media_root(media_root: Path) -> list[str]:
    """Return sorted MEDIA_ROOT-relative ``folder/file`` media paths.

    Skips the top-level ``archive/`` directory entirely. These relative paths
    are the canonical media.yaml entry keys.
    """
    if not media_root.is_dir():
        raise SystemExit(f"Error: media root is not a directory: {media_root}")

    result: list[str] = []
    media_root = media_root.resolve()
    for dirpath, dirnames, filenames in os.walk(media_root, topdown=True):
        # Never descend into MEDIA_ROOT/archive/
        if Path(dirpath) == media_root and MEDIA_ARCHIVE_DIR in dirnames:
            dirnames.remove(MEDIA_ARCHIVE_DIR)
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in MEDIA_SUFFIXES:
                continue
            result.append(path.relative_to(media_root).as_posix())

    result.sort(key=lambda name: name.encode("utf-8"))
    if not result:
        raise SystemExit(
            f"Error: no .mov/.mp4/.m4v files found under {media_root}"
        )
    return result


def canonicalize_media_rel(rel: str, media_root: Path) -> str:
    """Normalize a media path to the MEDIA_ROOT-relative ``folder/file`` key.

    Keeps the path as-is when it already resolves under MEDIA_ROOT. If a longer
    prefix was supplied (``ca4e-media/01-Origins/foo.m4v``) and ``folder/file``
    exists, returns that. Never collapses to a bare filename when a folder is
    present on disk.
    """
    rel_posix = Path(rel).as_posix().lstrip("./")
    if (media_root / rel_posix).is_file():
        return rel_posix
    ff = folder_file_key(rel_posix)
    if ff != rel_posix and (media_root / ff).is_file():
        return ff
    return rel_posix


def resolve_media_files(media_root: Path, files_path: Path | None) -> list[str]:
    if files_path is not None:
        raw = load_media_files(files_path)
    else:
        raw = scan_media_root(media_root)
    # Preserve scan order; canonicalize and dedupe.
    seen: set[str] = set()
    result: list[str] = []
    for name in raw:
        key = canonicalize_media_rel(name, media_root)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result

def title_from_stem(rel_path: str) -> str:
    stem = Path(rel_path).stem
    return stem.replace("_", " ").replace("-", " ").strip()


def resolve_title(
    rel_path: str,
    title_map: dict[str, str],
    existing_title: str | None,
    *,
    force_title: bool,
    is_new: bool,
    folder_file_index: dict[str, str] | None = None,
) -> str:
    """Return the title to store for this media file.

    Matched lessons titles are always applied (requirement: update title on rerun).
    Unmatched files warn and use a stem-derived title for new entries, or when
    ``--force-title`` is set; otherwise an existing title is preserved.

    Matching accepts an exact media path or a unique ``folder/file`` when
    lessons.json stores a longer prefixed path.
    """
    lesson_title = lookup_lessons_media(rel_path, title_map, folder_file_index)
    if isinstance(lesson_title, str) and lesson_title.strip():
        return lesson_title

    if existing_title and not force_title and not is_new:
        warnings.warn(
            f"No lesson title for {rel_path!r}; keeping existing title",
            UserWarning,
            stacklevel=2,
        )
        return existing_title

    stem_title = title_from_stem(rel_path)
    warnings.warn(
        f"No lesson title for {rel_path!r}; using filename stem {stem_title!r}",
        UserWarning,
        stacklevel=2,
    )
    return stem_title


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise SystemExit(
            "Error: ffprobe not found on PATH. "
            "Install ffmpeg (which provides ffprobe) and try again."
        )
    return path


def probe_duration(ffprobe: str, path: Path) -> int:
    return probe_media_meta(ffprobe, path)["duration"]


def normalize_creation_timestamp(value: Any) -> str | None:
    """Normalize ffprobe creation timestamps for media.yaml storage."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Drop useless fractional seconds: 2025-08-28T17:48:55.000000Z -> …55Z
    text = re.sub(r"\.0+Z$", "Z", text)
    text = re.sub(r"\.0+([+-]\d{2}:?\d{2})$", r"\1", text)
    return text


def probe_media_meta(ffprobe: str, path: Path) -> dict[str, Any]:
    """Return duration (int seconds) plus container/QT creation timestamps."""
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise SystemExit(f"Error: failed to run ffprobe on {path}: {exc}") from exc

    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise SystemExit(
            f"Error: ffprobe failed for {path} "
            f"(exit {completed.returncode}): {err or 'no details'}"
        )

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Error: ffprobe returned invalid JSON for {path}: {exc}"
        ) from exc

    fmt = payload.get("format") if isinstance(payload, dict) else None
    if not isinstance(fmt, dict):
        raise SystemExit(f"Error: ffprobe missing format block for {path}")

    raw_duration = fmt.get("duration")
    try:
        duration = round(float(raw_duration))
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"Error: ffprobe returned non-numeric duration for {path}: {raw_duration!r}"
        ) from exc

    tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
    # Tag names vary by demuxer; look up case-insensitively.
    lower_tags = {
        str(key).lower(): value for key, value in tags.items() if isinstance(key, str)
    }
    container = normalize_creation_timestamp(lower_tags.get("creation_time"))
    qt = normalize_creation_timestamp(
        lower_tags.get("com.apple.quicktime.creationdate")
    )

    return {
        "duration": duration,
        "container_creation": container,
        "qt_creation": qt,
    }


def format_duration_text(seconds: int) -> str:
    """Format seconds as mm:ss, or hh:mm:ss when >= 1 hour."""
    if seconds < 0:
        seconds = 0
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def load_existing(output_path: Path, yaml: YAML) -> CommentedMap:
    if not output_path.exists():
        data = CommentedMap()
        for key in GLOBAL_KEYS:
            data[key] = None
        data["entries"] = CommentedMap()
        return data

    try:
        with output_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.load(handle)
    except Exception as exc:  # noqa: BLE001 - surface YAML parse errors clearly
        raise SystemExit(f"Error: cannot parse existing YAML {output_path}: {exc}") from exc

    if loaded is None:
        data = CommentedMap()
        for key in GLOBAL_KEYS:
            data[key] = None
        data["entries"] = CommentedMap()
        return data

    if not isinstance(loaded, dict):
        raise SystemExit(
            f"Error: existing {output_path} must be a mapping with media_root and entries"
        )

    if not isinstance(loaded, CommentedMap):
        data = CommentedMap()
        for key, value in loaded.items():
            data[key] = value
        loaded = data

    entries = loaded.get("entries")
    if entries is None:
        loaded["entries"] = CommentedMap()
    elif not isinstance(entries, dict):
        raise SystemExit(f"Error: 'entries' in {output_path} must be a mapping")
    elif not isinstance(entries, CommentedMap):
        converted = CommentedMap()
        for key, value in entries.items():
            converted[key] = value
        loaded["entries"] = converted

    return loaded


def ensure_entry_map(entry: Any) -> CommentedMap:
    if entry is None:
        return CommentedMap()
    if isinstance(entry, CommentedMap):
        return entry
    if isinstance(entry, dict):
        converted = CommentedMap()
        for key, value in entry.items():
            converted[key] = value
        return converted
    raise SystemExit(f"Error: entry must be a mapping, got {type(entry).__name__}")


def ordered_entry(
    existing: CommentedMap,
    *,
    title: str,
    ai_title: Any = None,
    size: int,
    md5: str,
    duration: int,
    container_creation: Any = None,
    qt_creation: Any = None,
    youtube_id: Any = None,
    kaltura_id: Any = None,
    description: Any = None,
    tags: Any = None,
    force_youtube: bool = False,
    force_description: bool = False,
    force_tags: bool = False,
    force_ai_title: bool = False,
) -> CommentedMap:
    """Update an entry in place so ruamel comments/formatting are preserved.

    ``youtube_id`` and ``kaltura_id`` are expected to already be resolved by the
    caller (playlist / lessons.json / existing).

    ``title`` is the course-facing title (typically from lessons.json).
    ``ai_title`` records the AI-generated title for later comparison; it is
    preserved on rerun unless a new AI title is supplied (or ``force_ai_title``).
    """
    preserved = {key: existing.get(key, None) for key in PRESERVE_KEYS}
    preserved["youtube_id"] = youtube_id
    preserved["kaltura_id"] = kaltura_id
    preserved["ai_title"] = choose_field(
        preserved["ai_title"], ai_title, force=force_ai_title
    )
    preserved["description"] = as_literal_description(
        choose_field(
            preserved["description"],
            description,
            force=force_description or force_youtube,
        )
    )

    existing_tags = normalize_tags(existing.get("tags"))
    incoming_tags = normalize_tags(tags)
    chosen_tags = choose_field(existing_tags, incoming_tags, force=force_tags)

    # Drop legacy keys that must not appear in media.yaml.
    existing.pop("review", None)

    # Ensure known keys exist in the required order by rebuilding only when needed.
    known = [key for key in existing.keys() if key in ENTRY_KEYS]
    needs_reorder = known != list(ENTRY_KEYS)
    duration_text = format_duration_text(duration)
    container_creation = normalize_creation_timestamp(container_creation)
    qt_creation = normalize_creation_timestamp(qt_creation)
    if needs_reorder or not existing:
        extras = [
            (key, value)
            for key, value in existing.items()
            if key not in ENTRY_KEYS and key != "review"
        ]
        existing.clear()
        existing["title"] = title
        existing["ai_title"] = preserved["ai_title"]
        existing["youtube_id"] = preserved["youtube_id"]
        existing["kaltura_id"] = preserved["kaltura_id"]
        existing["size"] = size
        existing["md5"] = md5
        existing["duration"] = duration
        existing["duration_text"] = duration_text
        existing["container_creation"] = container_creation
        existing["qt_creation"] = qt_creation
        existing["tags"] = chosen_tags
        for key, value in extras:
            existing[key] = value
        existing["description"] = preserved["description"]
        return existing

    existing["title"] = title
    existing["ai_title"] = preserved["ai_title"]
    existing["youtube_id"] = preserved["youtube_id"]
    existing["kaltura_id"] = preserved["kaltura_id"]
    existing["description"] = preserved["description"]
    existing["size"] = size
    existing["md5"] = md5
    existing["duration"] = duration
    existing["duration_text"] = duration_text
    existing["container_creation"] = container_creation
    existing["qt_creation"] = qt_creation
    existing["tags"] = chosen_tags
    return existing


def rebuild_entries(
    old_entries: CommentedMap,
    media_files: list[str],
    updated: dict[str, CommentedMap],
) -> tuple[CommentedMap, list[str]]:
    """Return entries in media inventory order, then any orphaned keys."""
    media_set = set(media_files)
    orphans = [key for key in old_entries.keys() if key not in media_set]

    # Strip legacy review flags from orphans (Review is lessons.json-only).
    for key in orphans:
        entry = old_entries.get(key)
        if isinstance(entry, dict):
            entry.pop("review", None)

    # If order already matches and there are no inserts/moves, update in place.
    expected = list(media_files) + orphans
    if list(old_entries.keys()) == expected:
        for name in media_files:
            old_entries[name] = updated[name]
        return old_entries, orphans

    new_entries = CommentedMap()
    for name in media_files:
        new_entries[name] = updated[name]
    for key in orphans:
        new_entries[key] = old_entries[key]
    return new_entries, orphans


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ffprobe = require_ffprobe()
    media_root: Path = args.media_root

    if not media_root.is_dir():
        raise SystemExit(f"Error: media root is not a directory: {media_root}")

    media_files = resolve_media_files(media_root, args.files)
    inventory_label = str(args.files) if args.files else str(media_root)
    (
        title_map,
        lesson_youtube_map,
        lesson_kaltura_map,
        _,
        _,
        _,
    ) = load_lessons_media_map(args.lessons, relevant=set(media_files))
    title_folder_file_index = build_lessons_folder_file_index(title_map)
    youtube_folder_file_index = build_lessons_folder_file_index(lesson_youtube_map)
    kaltura_folder_file_index = build_lessons_folder_file_index(lesson_kaltura_map)

    playlist = load_youtube_playlist(args.youtube_playlist)
    by_id, by_title, by_basename = index_youtube_playlist(playlist)
    kaltura_playlist = load_kaltura_playlist(args.kaltura_playlist)
    kaltura_by_id, kaltura_by_name = index_kaltura_playlist(kaltura_playlist)
    ordinal_prefixes = build_media_ordinal_prefixes(media_files)
    whisper_root = resolve_whisper_root(args)

    yaml = build_yaml()
    data = load_existing(args.output, yaml)
    apply_course_globals(data, args)

    old_entries = data["entries"]
    updated: dict[str, CommentedMap] = {}
    youtube_matched = 0
    youtube_from_lessons: list[str] = []
    youtube_unmatched: list[str] = []
    kaltura_from_playlist: list[str] = []
    kaltura_from_lessons: list[str] = []
    kaltura_kept: list[str] = []
    kaltura_unmatched: list[str] = []
    ai_desc_count = 0
    yt_desc_count = 0
    ai_tags_count = 0
    ai_title_count = 0

    for rel_name in media_files:
        # Inventory keys are MEDIA_ROOT-relative (folder/file). Do not key by a
        # bare filename or by a longer lessons.json prefix.
        media_path = media_root / rel_name
        if not media_path.is_file():
            raise SystemExit(f"Error: missing media file: {media_path}")

        existing = ensure_entry_map(old_entries.get(rel_name))
        existing_title = existing.get("title")
        if not isinstance(existing_title, str):
            existing_title = None

        is_new = rel_name not in old_entries
        lesson_title = resolve_title(
            rel_name,
            title_map,
            existing_title,
            force_title=args.force_title,
            is_new=is_new,
            folder_file_index=title_folder_file_index,
        )

        lesson_title_for_yt = (
            lookup_lessons_media(rel_name, title_map, title_folder_file_index)
            or lesson_title
        )
        lessons_title_for_prefix = lookup_lessons_media(
            rel_name, title_map, title_folder_file_index
        )
        if not isinstance(lessons_title_for_prefix, str):
            lessons_title_for_prefix = None
        lesson_youtube_id = lookup_lessons_media(
            rel_name, lesson_youtube_map, youtube_folder_file_index
        )
        youtube_id, yt_entry = resolve_youtube_id(
            rel_name,
            existing_youtube_id=existing.get("youtube_id"),
            lesson_youtube_id=lesson_youtube_id,
            lesson_title=lesson_title_for_yt,
            by_id=by_id,
            by_title=by_title,
            by_basename=by_basename,
            force=args.force_youtube,
        )
        youtube_description = None
        if yt_entry is not None:
            youtube_matched += 1
            youtube_description = yt_entry.get("description")
            if isinstance(youtube_description, str):
                youtube_description = youtube_description.strip() or None
            else:
                youtube_description = None
        elif youtube_id:
            youtube_from_lessons.append(rel_name)
        elif playlist:
            youtube_unmatched.append(rel_name)
            warnings.warn(
                f"No YouTube playlist or lessons.json youtube id for {rel_name!r}",
                UserWarning,
                stacklevel=2,
            )

        lesson_kaltura_id = lookup_lessons_media(
            rel_name, lesson_kaltura_map, kaltura_folder_file_index
        )
        existing_kaltura = normalize_kaltura_id(existing.get("kaltura_id"))
        kaltura_id, kaltura_entry = resolve_kaltura_id(
            rel_name,
            existing_kaltura_id=existing_kaltura,
            lesson_kaltura_id=lesson_kaltura_id,
            lesson_title=lesson_title_for_yt,
            by_id=kaltura_by_id,
            by_name=kaltura_by_name,
            force=args.force_kaltura,
        )
        if kaltura_id and existing_kaltura and kaltura_id == existing_kaltura and not args.force_kaltura:
            kaltura_kept.append(rel_name)
        elif kaltura_entry is not None and (
            not existing_kaltura or args.force_kaltura
        ):
            kaltura_from_playlist.append(rel_name)
        elif kaltura_id and not existing_kaltura:
            kaltura_from_lessons.append(rel_name)
        elif kaltura_playlist and not kaltura_id:
            kaltura_unmatched.append(rel_name)

        ai_title, ai_tags, ai_description = load_ai_metadata(whisper_root, rel_name)
        if ai_title:
            ai_title_count += 1
        if ai_description:
            description = ai_description
            force_description = True
            ai_desc_count += 1
        else:
            description = (
                sanitize_youtube_text(youtube_description)
                if youtube_description
                else None
            )
            force_description = False
            if description:
                yt_desc_count += 1

        if ai_tags:
            tags = ai_tags
            force_tags = True
            ai_tags_count += 1
        else:
            tags = None
            force_tags = False

        extra_tags = env_or_none("EXTRA_TAGS")
        extra_description = env_or_none("EXTRA_DESCRIPTION")
        merged_tags = merge_extra_tags(tags, extra_tags)
        if extra_tags and merged_tags != tags:
            tags = merged_tags
            force_tags = True
        elif merged_tags:
            tags = merged_tags

        merged_description = merge_extra_description(description, extra_description)
        if extra_description and merged_description != description:
            description = merged_description
            force_description = True

        meta = probe_media_meta(ffprobe, media_path)
        duration = meta["duration"]
        numbered_prefix = ordinal_prefixes.get(rel_name)
        # Course-facing title comes from lessons.json (plus optional TITLE_PREFIX).
        title = compose_media_title(
            lesson_title,
            None,
            duration,
            numbered_prefix=numbered_prefix,
            lessons_title_for_prefix=lessons_title_for_prefix,
        )
        # Keep AI wording separately for later comparison / editing.
        composed_ai_title = None
        force_ai_title = False
        if ai_title:
            composed_ai_title = compose_media_title(
                lesson_title,
                ai_title,
                duration,
                numbered_prefix=numbered_prefix,
                lessons_title_for_prefix=lessons_title_for_prefix,
            )
            force_ai_title = True

        updated[rel_name] = ordered_entry(
            existing,
            title=title,
            ai_title=composed_ai_title,
            size=media_path.stat().st_size,
            md5=file_md5(media_path),
            duration=duration,
            container_creation=meta.get("container_creation"),
            qt_creation=meta.get("qt_creation"),
            youtube_id=youtube_id,
            kaltura_id=kaltura_id,
            description=description,
            tags=tags,
            force_youtube=args.force_youtube,
            force_description=force_description,
            force_tags=force_tags,
            force_ai_title=force_ai_title,
        )

    new_entries, orphans = rebuild_entries(old_entries, media_files, updated)
    data["entries"] = new_entries
    data = order_root_map(data)

    try:
        with args.output.open("w", encoding="utf-8") as handle:
            yaml.dump(data, handle)
    except OSError as exc:
        raise SystemExit(f"Error: cannot write {args.output}: {exc}") from exc

    print(f"Wrote {len(media_files)} media entries to {args.output}")
    print(
        f"Descriptions: {ai_desc_count} from whisper/desc, "
        f"{yt_desc_count} from YouTube playlist"
        + (
            f" (whisper_root={whisper_root})"
            if whisper_root is not None
            else " (no WHISPER_ROOT)"
        )
    )
    print(f"Titles: {ai_title_count} from whisper/desc, "
          f"{len(media_files) - ai_title_count} from lessons.json fallback")
    print(f"Tags: {ai_tags_count} from whisper/desc")
    if playlist or youtube_from_lessons or youtube_unmatched:
        print(
            f"YouTube ids: {youtube_matched} from playlist"
            + (
                f" ({args.youtube_playlist})"
                if playlist
                else ""
            )
            + f", {len(youtube_from_lessons)} from lessons.json only"
            + f", {len(youtube_unmatched)} unmatched"
        )
        if youtube_from_lessons:
            print(
                f"Filled from lessons.json (not in playlist) "
                f"({len(youtube_from_lessons)}):"
            )
            for name in youtube_from_lessons:
                print(f"  {name}")
        if youtube_unmatched:
            print(f"Unmatched media files ({len(youtube_unmatched)}):")
            for name in youtube_unmatched:
                print(f"  {name}")
    if kaltura_playlist or kaltura_from_playlist or kaltura_from_lessons or kaltura_unmatched:
        print(
            f"Kaltura ids: {len(kaltura_from_playlist)} filled from playlist"
            + (f" ({args.kaltura_playlist})" if kaltura_playlist else "")
            + f", {len(kaltura_from_lessons)} from lessons.json only"
            + f", {len(kaltura_kept)} kept existing"
            + f", {len(kaltura_unmatched)} unmatched"
        )
        if kaltura_unmatched:
            print(f"Unmatched Kaltura media files ({len(kaltura_unmatched)}):")
            for name in kaltura_unmatched:
                print(f"  {name}")
    elif args.kaltura_playlist and not args.kaltura_playlist.exists():
        print(
            f"Kaltura ids: no playlist dump at {args.kaltura_playlist} "
            "(run dump-kaltura-playlist.py)"
        )
    if orphans:
        print(f"Orphaned YAML entries ({len(orphans)}) not in {inventory_label}:")
        for name in orphans:
            print(f"  {name}")
    else:
        print("No orphaned YAML entries.")
    return 0


if __name__ == "__main__":
    def _showwarning(message, category, filename, lineno, file=None, line=None):
        sys.stderr.write(f"Warning: {message}\n")

    warnings.showwarning = _showwarning  # type: ignore[assignment]
    raise SystemExit(main())
