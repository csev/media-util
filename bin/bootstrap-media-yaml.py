#!/usr/bin/env python3
"""Bootstrap and refresh media.yaml from lessons.json and a media tree.

By default the inventory is scanned from MEDIA_ROOT / --media-root
(.mov / .mp4 / .m4v). Pass --files only to use an explicit list.

Descriptions prefer WHISPER_ROOT/desc (whisper-desc) when present, else the
YouTube playlist description. Titles are composed as
``DJ nn.mm <AI title> (duration)`` from lessons.json + whisper/desc.
Course EXTRA_TAGS / EXTRA_DESCRIPTION from media.env are appended onto each
entry's tags/description. Preserves manually edited youtube_id / kaltura_id
on rerun via ruamel.yaml (AI descriptions refresh whenever the desc file exists).
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
    "youtube_id",
    "kaltura_id",
    "size",
    "md5",
    "duration",
    "duration_text",
    "tags",
    "description",
)

PRESERVE_KEYS = ("description", "youtube_id", "kaltura_id")

# Top-level media.yaml keys mirrored from media.env.
GLOBAL_KEYS = (
    "course_root",
    "media_root",
    "whisper_root",
    "youtube_dir",
    "youtube_playlist",
    "course_hint",
    "extra_tags",
    "extra_description",
)

REVIEW_MARKER_RE = re.compile(r"Review:|\(\s*review\s*\)", re.IGNORECASE)
DJ_PREFIX_RE = re.compile(r"^(DJ\s+\d+\.\d+)\b", re.IGNORECASE)
TRAILING_DURATION_RE = re.compile(r"\s*\((?:\d+:)+\d+\)\s*$")


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
    data["course_hint"] = env_or_none("COURSE_HINT")
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    media_root_default = os.environ.get("MEDIA_ROOT")
    youtube_jsonl_default = os.environ.get("YOUTUBE_PLAYLIST_JSONL")
    if not youtube_jsonl_default:
        youtube_dir = os.environ.get("YOUTUBE_DIR")
        if youtube_dir:
            youtube_jsonl_default = str(Path(youtube_dir) / "youtube-playlist.jsonl")
        else:
            youtube_jsonl_default = str(CWD / "youtube" / "youtube-playlist.jsonl")

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
            "Overwrite existing youtube_id from the playlist, and description "
            "when no AI whisper/desc file is present. "
            "Without this flag, only null/empty youtube_id values are filled; "
            "AI descriptions always refresh when present."
        ),
    )
    return parser.parse_args(argv)


def load_lessons_media_map(
    lessons_path: Path,
    relevant: set[str] | None = None,
    *,
    strict: bool = True,
) -> tuple[dict[str, str], dict[str, str], dict[str, set[str]], dict[str, set[str]]]:
    """Return (title_map, youtube_id_map, title_conflicts, youtube_conflicts).

    Schema (inspected): top-level ``modules`` list; each module has ``items``.
    Items may include ``media``, ``title``, and ``youtube``.

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
    title_conflicts: dict[str, set[str]] = {}
    youtube_conflicts: dict[str, set[str]] = {}

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

    if relevant is not None:
        title_conflicts = {k: v for k, v in title_conflicts.items() if k in relevant}
        youtube_conflicts = {k: v for k, v in youtube_conflicts.items() if k in relevant}

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

    return title_map, youtube_map, title_conflicts, youtube_conflicts


def normalize_title(title: str) -> str:
    """Normalize titles for comparison (drop DJ nn.mm prefix and trailing times)."""
    text = title.strip()
    text = re.sub(r"^DJ\s+\d+\.\d+\s+", "", text, flags=re.IGNORECASE)
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


def extract_dj_prefix(title: str) -> str | None:
    match = DJ_PREFIX_RE.match(title.strip())
    return match.group(1) if match else None


def clean_title_body(text: str) -> str:
    """Strip DJ prefix, Review marker, and trailing (duration) from a title body."""
    body = text.strip()
    body = DJ_PREFIX_RE.sub("", body).strip()
    body = re.sub(r"^Review:\s*", "", body, flags=re.IGNORECASE).strip()
    body = re.sub(r"\(\s*review\s*\)", "", body, flags=re.IGNORECASE).strip()
    body = TRAILING_DURATION_RE.sub("", body).strip()
    body = re.sub(r"\s+", " ", body).strip()
    return body


def compose_media_title(
    lesson_title: str,
    ai_title: str | None,
    duration_seconds: int,
) -> str:
    """Build ``DJ nn.mm <AI title> (duration)`` (no Review: — lessons.json only)."""
    prefix = extract_dj_prefix(lesson_title)
    if not prefix:
        prefix = "DJ"
    body = clean_title_body(ai_title) if ai_title else ""
    if not body:
        body = clean_title_body(lesson_title)
    if not body:
        body = "Untitled"
    duration = format_title_duration(duration_seconds)
    return sanitize_youtube_text(f"{prefix} {body} ({duration})")


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


def scan_media_root(media_root: Path) -> list[str]:
    """Return sorted relative paths of media files under media_root."""
    if not media_root.is_dir():
        raise SystemExit(f"Error: media root is not a directory: {media_root}")

    result: list[str] = []
    for path in media_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        result.append(path.relative_to(media_root).as_posix())

    result.sort(key=lambda name: name.encode("utf-8"))
    if not result:
        raise SystemExit(
            f"Error: no .mov/.mp4/.m4v files found under {media_root}"
        )
    return result


def resolve_media_files(media_root: Path, files_path: Path | None) -> list[str]:
    if files_path is not None:
        return load_media_files(files_path)
    return scan_media_root(media_root)

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
) -> str:
    """Return the title to store for this media file.

    Matched lessons titles are always applied (requirement: update title on rerun).
    Unmatched files warn and use a stem-derived title for new entries, or when
    ``--force-title`` is set; otherwise an existing title is preserved.
    """
    if rel_path in title_map:
        return title_map[rel_path]

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
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
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

    raw = completed.stdout.strip()
    try:
        return round(float(raw))
    except ValueError as exc:
        raise SystemExit(
            f"Error: ffprobe returned non-numeric duration for {path}: {raw!r}"
        ) from exc


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
    size: int,
    md5: str,
    duration: int,
    youtube_id: Any = None,
    description: Any = None,
    tags: Any = None,
    force_youtube: bool = False,
    force_description: bool = False,
    force_tags: bool = False,
) -> CommentedMap:
    """Update an entry in place so ruamel comments/formatting are preserved."""
    preserved = {key: existing.get(key, None) for key in PRESERVE_KEYS}
    preserved["youtube_id"] = choose_field(
        preserved["youtube_id"], youtube_id, force=force_youtube
    )
    preserved["description"] = as_literal_description(
        choose_field(
            preserved["description"],
            description,
            force=force_description or force_youtube,
        )
    )
    # Always preserve manually set kaltura_id.
    preserved["kaltura_id"] = existing.get("kaltura_id", None)

    existing_tags = normalize_tags(existing.get("tags"))
    incoming_tags = normalize_tags(tags)
    chosen_tags = choose_field(existing_tags, incoming_tags, force=force_tags)

    # Drop legacy keys that must not appear in media.yaml.
    existing.pop("review", None)

    # Ensure known keys exist in the required order by rebuilding only when needed.
    known = [key for key in existing.keys() if key in ENTRY_KEYS]
    needs_reorder = known != list(ENTRY_KEYS)
    duration_text = format_duration_text(duration)
    if needs_reorder or not existing:
        extras = [
            (key, value)
            for key, value in existing.items()
            if key not in ENTRY_KEYS and key != "review"
        ]
        existing.clear()
        existing["title"] = title
        existing["youtube_id"] = preserved["youtube_id"]
        existing["kaltura_id"] = preserved["kaltura_id"]
        existing["size"] = size
        existing["md5"] = md5
        existing["duration"] = duration
        existing["duration_text"] = duration_text
        existing["tags"] = chosen_tags
        for key, value in extras:
            existing[key] = value
        existing["description"] = preserved["description"]
        return existing

    existing["title"] = title
    existing["youtube_id"] = preserved["youtube_id"]
    existing["kaltura_id"] = preserved["kaltura_id"]
    existing["description"] = preserved["description"]
    existing["size"] = size
    existing["md5"] = md5
    existing["duration"] = duration
    existing["duration_text"] = duration_text
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
    title_map, lesson_youtube_map, _, _ = load_lessons_media_map(
        args.lessons, relevant=set(media_files)
    )

    playlist = load_youtube_playlist(args.youtube_playlist)
    by_id, by_title, by_basename = index_youtube_playlist(playlist)
    whisper_root = resolve_whisper_root(args)

    yaml = build_yaml()
    data = load_existing(args.output, yaml)
    apply_course_globals(data, args)

    old_entries = data["entries"]
    updated: dict[str, CommentedMap] = {}
    youtube_matched = 0
    youtube_unmatched: list[str] = []
    ai_desc_count = 0
    yt_desc_count = 0
    ai_tags_count = 0
    ai_title_count = 0

    for rel_name in media_files:
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
        )

        yt_entry = match_youtube_entry(
            rel_name,
            lesson_title=title_map.get(rel_name) or lesson_title,
            lesson_youtube_id=lesson_youtube_map.get(rel_name),
            by_id=by_id,
            by_title=by_title,
            by_basename=by_basename,
        )
        youtube_id = None
        youtube_description = None
        if yt_entry is not None:
            youtube_matched += 1
            youtube_id = yt_entry.get("id")
            youtube_description = yt_entry.get("description")
            if isinstance(youtube_description, str):
                youtube_description = youtube_description.strip() or None
            else:
                youtube_description = None
        elif playlist:
            youtube_unmatched.append(rel_name)
            warnings.warn(
                f"No YouTube playlist match for {rel_name!r}",
                UserWarning,
                stacklevel=2,
            )

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

        duration = probe_duration(ffprobe, media_path)
        title = compose_media_title(lesson_title, ai_title, duration)

        updated[rel_name] = ordered_entry(
            existing,
            title=title,
            size=media_path.stat().st_size,
            md5=file_md5(media_path),
            duration=duration,
            youtube_id=youtube_id,
            description=description,
            tags=tags,
            force_youtube=args.force_youtube,
            force_description=force_description,
            force_tags=force_tags,
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
    if playlist:
        print(
            f"YouTube matches: {youtube_matched}/{len(media_files)} "
            f"from {args.youtube_playlist}"
        )
        if youtube_unmatched:
            print(f"Unmatched media files ({len(youtube_unmatched)}):")
            for name in youtube_unmatched:
                print(f"  {name}")
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
