#!/usr/bin/env python3
"""Update YouTube video titles/descriptions/tags from media.yaml.

Requires OAuth (YouTube Data API v3). Dry-run by default; pass --apply to write.

Credentials (first run opens a browser for consent):
  $YOUTUBE_CLIENT_SECRETS  or  ~/.ssh/youtube_client_secret.json
  token saved to            $YOUTUBE_TOKEN  or  $YOUTUBE_DIR/youtube-oauth-token.json

Example:
  source media.env
  update-youtube-from-media-yaml.py              # preview diffs
  update-youtube-from-media-yaml.py --apply      # push changes
  update-youtube-from-media-yaml.py --apply --limit 1
  update-youtube-from-media-yaml.py --only-playlist   # skip unlisted / non-playlist
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_text import sanitize_youtube_tags, sanitize_youtube_text  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESC_MAX = 5000
# YouTube enforces a total character budget across all tags.
YOUTUBE_TAGS_MAX_CHARS = 500


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def env_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value.strip())
    return None


def default_media_yaml() -> Path:
    path = env_path("MEDIA_YAML")
    if path:
        return path
    course = env_path("COURSE_ROOT")
    if course:
        return course / "media.yaml"
    return Path.cwd() / "media.yaml"


def default_youtube_dir() -> Path:
    path = env_path("YOUTUBE_DIR")
    if path:
        return path
    course = env_path("COURSE_ROOT")
    if course:
        return course / "youtube"
    return Path.cwd() / "youtube"


def default_client_secrets() -> Path:
    path = env_path("YOUTUBE_CLIENT_SECRETS")
    if path:
        return path
    return Path.home() / ".ssh" / "youtube_client_secret.json"


def default_token_path() -> Path:
    path = env_path("YOUTUBE_TOKEN")
    if path:
        return path
    return default_youtube_dir() / "youtube-oauth-token.json"


def default_youtube_playlist_jsonl() -> Path:
    path = env_path("YOUTUBE_PLAYLIST_JSONL")
    if path:
        return path
    return default_youtube_dir() / "youtube-playlist.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Update YouTube titles, descriptions, and tags from media.yaml "
            "(dry-run unless --apply)."
        )
    )
    parser.add_argument(
        "--media-yaml",
        type=Path,
        default=None,
        help="Path to media.yaml (default: $MEDIA_YAML / $COURSE_ROOT/media.yaml)",
    )
    parser.add_argument(
        "--client-secrets",
        type=Path,
        default=None,
        help=(
            "OAuth client secrets JSON "
            "(default: $YOUTUBE_CLIENT_SECRETS or ~/.ssh/youtube_client_secret.json)"
        ),
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=None,
        help=(
            "OAuth token cache path "
            "(default: $YOUTUBE_TOKEN or $YOUTUBE_DIR/youtube-oauth-token.json)"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update YouTube (default is dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N videos that would change (0 = all)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Only this youtube_id (repeatable) or media path substring",
    )
    parser.add_argument(
        "--only-playlist",
        action="store_true",
        help=(
            "Only update videos whose youtube_id appears in the course "
            "playlist JSONL (skips unlisted / off-playlist ids such as "
            "copyright extras). Combine with --only for a further filter."
        ),
    )
    parser.add_argument(
        "--youtube-playlist",
        type=Path,
        default=None,
        help=(
            "Playlist JSONL for --only-playlist "
            "(default: $YOUTUBE_PLAYLIST_JSONL or "
            "$YOUTUBE_DIR/youtube-playlist.jsonl)"
        ),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Seconds to sleep between API updates (default: 0.2)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Update even when title/description/tags already match",
    )
    return parser.parse_args()


def load_media_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        fail(f"media.yaml not found: {path}")
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        fail(f"cannot read {path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        fail(f"{path} must contain an 'entries' mapping")

    rows: list[dict[str, Any]] = []
    for rel, entry in data["entries"].items():
        if not isinstance(entry, dict):
            continue
        youtube_id = entry.get("youtube_id")
        if not isinstance(youtube_id, str) or not youtube_id.strip():
            continue
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        description = entry.get("description")
        if description is None:
            description = ""
        elif not isinstance(description, str):
            description = str(description)
        tags = entry.get("tags")
        rows.append(
            {
                "media": rel,
                "youtube_id": youtube_id.strip(),
                "title": title.strip(),
                "description": description.strip("\n"),
                "tags": tags,
            }
        )
    return rows


def filter_rows(rows: list[dict[str, Any]], only: list[str]) -> list[dict[str, Any]]:
    if not only:
        return rows
    needles = [n.strip() for n in only if n.strip()]
    if not needles:
        return rows
    out = []
    for row in rows:
        blob = f"{row['youtube_id']} {row['media']}"
        if any(n in blob for n in needles):
            out.append(row)
    return out


def load_playlist_ids(path: Path) -> set[str]:
    """Return youtube ids from a yt-dlp playlist JSONL dump."""
    if not path.is_file():
        fail(f"playlist JSONL not found: {path}")
    ids: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot read {path}: {exc}")
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"malformed JSONL in {path}:{lineno}: {exc}")
        if not isinstance(entry, dict):
            continue
        youtube_id = entry.get("id")
        if isinstance(youtube_id, str) and youtube_id.strip():
            ids.add(youtube_id.strip())
    if not ids:
        fail(f"no youtube ids found in playlist JSONL: {path}")
    return ids


def filter_playlist_rows(
    rows: list[dict[str, Any]], playlist_ids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows into (on playlist, not on playlist)."""
    on_playlist: list[dict[str, Any]] = []
    off_playlist: list[dict[str, Any]] = []
    for row in rows:
        if row["youtube_id"] in playlist_ids:
            on_playlist.append(row)
        else:
            off_playlist.append(row)
    return on_playlist, off_playlist


def tags_to_list(value: Any) -> list[str]:
    """Convert media.yaml tags (comma string or list) to a sanitized list."""
    if value is None:
        return []
    if isinstance(value, str):
        raw = [p.strip() for p in value.split(",")]
    elif isinstance(value, list):
        raw = [str(p).strip() for p in value]
    else:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not item:
            continue
        cleaned = sanitize_youtube_tags(item)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(cleaned)
    return tags


def fit_youtube_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Keep tags within YouTube's total character budget. Returns (tags, notes)."""
    notes: list[str] = []
    if not tags:
        return [], notes
    kept: list[str] = []
    # YouTube counts commas between tags toward the limit.
    used = 0
    for tag in tags:
        extra = len(tag) + (1 if kept else 0)
        if used + extra > YOUTUBE_TAGS_MAX_CHARS:
            notes.append(
                f"tags truncated to {len(kept)}/{len(tags)} "
                f"(YouTube {YOUTUBE_TAGS_MAX_CHARS}-char limit)"
            )
            break
        kept.append(tag)
        used += extra
    return kept, notes


def tags_equal(left: list[str], right: list[str]) -> bool:
    """True when both sides have the same tags ignoring order and case."""
    return {t.casefold() for t in left} == {t.casefold() for t in right}


def normalize_description(text: str) -> str:
    """Normalize description text for reliable equality checks."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    # Drop a single trailing blank line YouTube sometimes round-trips differently.
    while lines and lines[-1] == "":
        lines.pop()
    while lines and lines[0] == "":
        lines.pop(0)
    return "\n".join(lines)


def prepare_for_youtube(
    title: str, description: str, tags: Any
) -> tuple[str, str, list[str], list[str]]:
    notes: list[str] = []
    cleaned_title = sanitize_youtube_text(title).strip()
    cleaned_desc = normalize_description(sanitize_youtube_text(description))
    cleaned_tags = tags_to_list(tags)
    if cleaned_title != title.strip() or cleaned_desc != normalize_description(description):
        notes.append("stripped HTML/angle-brackets for YouTube")
    title, description = cleaned_title, cleaned_desc
    if len(title) > YOUTUBE_TITLE_MAX:
        notes.append(f"title truncated {len(title)} -> {YOUTUBE_TITLE_MAX}")
        title = title[: YOUTUBE_TITLE_MAX - 1].rstrip() + "…"
    if len(description) > YOUTUBE_DESC_MAX:
        notes.append(f"description truncated {len(description)} -> {YOUTUBE_DESC_MAX}")
        description = description[: YOUTUBE_DESC_MAX - 1].rstrip() + "…"
        description = normalize_description(description)
    fitted, tag_notes = fit_youtube_tags(cleaned_tags)
    notes.extend(tag_notes)
    return title, description, fitted, notes


def build_youtube_service(client_secrets: Path, token_path: Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        fail(
            "missing Google API packages. Install with:\n"
            "  pip3 install -r /Users/csev/htdocs/media-util/requirements.txt\n"
            f"({exc})"
        )

    if not client_secrets.is_file():
        fail(
            f"OAuth client secrets not found: {client_secrets}\n"
            "Download a Desktop OAuth client JSON from Google Cloud Console "
            "(YouTube Data API v3 enabled) and save it there, or set "
            "YOUTUBE_CLIENT_SECRETS."
        )

    creds = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"Saved OAuth token: {token_path}")

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def fetch_snippet(youtube, video_id: str) -> dict[str, Any]:
    snippets = fetch_snippets(youtube, [video_id])
    if video_id not in snippets:
        fail(f"YouTube video not found or not accessible: {video_id}")
    return snippets[video_id]


def fetch_snippets(youtube, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batch-fetch snippets (up to 50 ids per API call) to save list quota."""
    out: dict[str, dict[str, Any]] = {}
    chunk_size = 50
    for i in range(0, len(video_ids), chunk_size):
        chunk = video_ids[i : i + chunk_size]
        if not chunk:
            continue
        response = (
            youtube.videos()
            .list(part="snippet", id=",".join(chunk))
            .execute()
        )
        for item in response.get("items") or []:
            vid = item.get("id")
            snippet = item.get("snippet")
            if isinstance(vid, str) and isinstance(snippet, dict):
                out[vid] = snippet
    return out


def is_quota_exceeded(exc: BaseException) -> bool:
    """True when YouTube Data API reports daily quota exhausted."""
    text = str(exc).lower()
    if "quotaexceeded" in text or ("exceeded your" in text and "quota" in text):
        return True
    content = getattr(exc, "content", None)
    if content:
        blob = (
            content.decode("utf-8", errors="replace").lower()
            if isinstance(content, bytes)
            else str(content).lower()
        )
        if "quotaexceeded" in blob or ("quota" in blob and "exceeded" in blob):
            return True
    resp = getattr(exc, "resp", None)
    if resp is not None and getattr(resp, "status", None) == 403 and "quota" in text:
        return True
    return False


def update_snippet(
    youtube,
    video_id: str,
    *,
    title: str,
    description: str,
    tags: list[str] | None,
    snippet: dict[str, Any],
) -> None:
    body = {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": snippet.get("categoryId") or "27",
        },
    }
    if tags is not None:
        body["snippet"]["tags"] = tags
    elif "tags" in snippet and isinstance(snippet["tags"], list):
        # No media.yaml tags — keep existing YouTube tags (sanitized).
        body["snippet"]["tags"] = [
            sanitize_youtube_tags(str(tag))
            for tag in snippet["tags"]
            if str(tag).strip()
        ]
    if "defaultLanguage" in snippet:
        body["snippet"]["defaultLanguage"] = snippet["defaultLanguage"]
    if "defaultAudioLanguage" in snippet:
        body["snippet"]["defaultAudioLanguage"] = snippet["defaultAudioLanguage"]

    youtube.videos().update(part="snippet", body=body).execute()


def main() -> int:
    args = parse_args()
    media_yaml = args.media_yaml or default_media_yaml()
    client_secrets = args.client_secrets or default_client_secrets()
    token_path = args.token or default_token_path()

    rows = filter_rows(load_media_entries(media_yaml), args.only)
    print(f"media.yaml: {media_yaml}")
    print(f"entries with youtube_id: {len(rows)}")

    if args.only_playlist:
        playlist_path = args.youtube_playlist or default_youtube_playlist_jsonl()
        playlist_ids = load_playlist_ids(playlist_path)
        rows, skipped_off = filter_playlist_rows(rows, playlist_ids)
        print(f"youtube-playlist: {playlist_path} ({len(playlist_ids)} videos)")
        print(f"on playlist: {len(rows)}")
        if skipped_off:
            print(f"skipped (not on playlist): {len(skipped_off)}")
            for row in skipped_off:
                print(f"  {row['youtube_id']}  {row['media']}")

    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    if not rows:
        print("Nothing to do.")
        return 0

    youtube = build_youtube_service(client_secrets, token_path)

    would_change = 0
    updated = 0
    skipped = 0
    errors = 0
    stopped_for_quota = False

    video_ids = [row["youtube_id"] for row in rows]
    try:
        snippets_by_id = fetch_snippets(youtube, video_ids)
        print(
            f"Fetched snippets: {len(snippets_by_id)}/{len(video_ids)} "
            f"({(len(video_ids) + 49) // 50} list request(s))"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR fetching YouTube snippets: {exc}", file=sys.stderr)
        if is_quota_exceeded(exc):
            print(
                "STOP: YouTube API quota exceeded; remaining videos not processed.",
                file=sys.stderr,
            )
            print("stopped:           YouTube API quota exceeded")
            return 1
        return 1

    for row in rows:
        if args.limit and would_change >= args.limit:
            break

        video_id = row["youtube_id"]
        wanted_title, wanted_desc, wanted_tags, notes = prepare_for_youtube(
            row["title"], row["description"], row.get("tags")
        )
        # Only push tags when media.yaml has some; otherwise preserve YouTube tags.
        push_tags = wanted_tags if wanted_tags else None

        snippet = snippets_by_id.get(video_id)
        if snippet is None:
            print(
                f"ERROR fetch {video_id} ({row['media']}): "
                "video not found or not accessible",
                file=sys.stderr,
            )
            errors += 1
            continue

        current_title = (snippet.get("title") or "").strip()
        current_desc = normalize_description(snippet.get("description") or "")
        current_tags = tags_to_list(snippet.get("tags") or [])
        title_same = current_title == wanted_title
        desc_same = current_desc == wanted_desc
        tags_same = True if push_tags is None else tags_equal(current_tags, push_tags)

        if title_same and desc_same and tags_same and not args.force:
            skipped += 1
            continue

        would_change += 1
        print()
        print(f"{video_id}  {row['media']}")
        if notes:
            for note in notes:
                print(f"  NOTE: {note}")
        if not title_same or args.force:
            print(f"  title:")
            print(f"    was: {current_title}")
            print(f"    now: {wanted_title}")
        if not desc_same or args.force:
            was_preview = current_desc.replace("\n", " / ")[:120]
            now_preview = wanted_desc.replace("\n", " / ")[:120]
            print(f"  description:")
            print(f"    was: {was_preview}{'…' if len(current_desc) > 120 else ''}")
            print(f"    now: {now_preview}{'…' if len(wanted_desc) > 120 else ''}")
        if push_tags is not None and (not tags_same or args.force):
            print(f"  tags:")
            print(f"    was: {', '.join(current_tags) or '(none)'}")
            print(f"    now: {', '.join(push_tags)}")

        if not args.apply:
            continue

        try:
            update_snippet(
                youtube,
                video_id,
                title=wanted_title,
                description=wanted_desc,
                tags=push_tags,
                snippet=snippet,
            )
            print("  UPDATED")
            updated += 1
            if args.sleep > 0:
                time.sleep(args.sleep)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR update: {exc}", file=sys.stderr)
            errors += 1
            if is_quota_exceeded(exc):
                stopped_for_quota = True
                print(
                    "STOP: YouTube API quota exceeded; remaining videos not processed.",
                    file=sys.stderr,
                )
                break

    print()
    print(f"unchanged/skipped: {skipped}")
    print(f"would change:      {would_change}")
    if args.apply:
        print(f"updated:           {updated}")
    else:
        print("dry-run only; re-run with --apply to push changes")
    if stopped_for_quota:
        print("stopped:           YouTube API quota exceeded")
    if errors:
        print(f"errors:            {errors}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
