#!/usr/bin/env python3
"""Shared Kaltura helpers for media-util upload / compare / smoke-test tools.

Env (source course media.env):

  KALTURA_PARTNER_ID       Partner id (required for live API calls)
  KALTURA_ADMIN_SECRET     Admin API secret (or file below)
  KALTURA_SECRET_FILE      Default: ~/.ssh/kaltura_admin_secret
  KALTURA_SERVICE_URL      Default: https://www.kaltura.com
  KALTURA_USER_ID          Session user id (default: media-util)
  KALTURA_CATEGORY_ID      Optional category to attach uploads to
  KALTURA_PLAYLIST_ID      Optional static playlist to append uploads to
  KALTURA_PLAYLIST_URL     Public MediaSpace playlist URL (dump / compare)
  KALTURA_DIR              Course folder for kaltura-playlist.jsonl
  KALTURA_PLAYLIST_JSONL   Optional override path for playlist dump

Reference id for each lecture is the media.yaml relative path
(e.g. lesson-01-welcome/01-DJ-00-00-Welcome-2024-01-09.m4v).

Public MediaSpace playlist helpers below do **not** need an admin secret:
they scrape a guest KS from the playlist HTML and call playlist.execute.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SERVICE_URL = "https://www.kaltura.com"
DEFAULT_SECRET_FILE = Path.home() / ".ssh" / "kaltura_admin_secret"
DEFAULT_USER_ID = "media-util"
KALTURA_API = "https://www.kaltura.com/api_v3/index.php"
PUBLIC_USER_AGENT = "media-util-kaltura-playlist/1.0"

PLAYLIST_CONTENT_RE = re.compile(
    r'playlistContent["\']?\s*[:=]\s*["\']([^"\']+)["\']'
)
KS_RE = re.compile(r'"ks"\s*:\s*"([^"]+)"')
ENTRY_ID_RE = re.compile(r"^1_[a-zA-Z0-9]+$")
TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
PLAYLIST_ID_FROM_URL_RE = re.compile(
    r"/(1_[a-zA-Z0-9]+)(?:/(1_[a-zA-Z0-9]+))?/?$"
)


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def env_str(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def env_path(*names: str) -> Path | None:
    value = env_str(*names)
    return Path(value) if value else None


def default_media_yaml() -> Path:
    path = env_path("MEDIA_YAML")
    if path:
        return path
    course = env_path("COURSE_ROOT")
    if course:
        return course / "media.yaml"
    return Path.cwd() / "media.yaml"


def default_media_root() -> Path:
    path = env_path("MEDIA_ROOT")
    if path:
        return path
    fail("set MEDIA_ROOT (source media.env) or pass --media-root")


def require_kaltura_client():
    try:
        from KalturaClient import KalturaClient, KalturaConfiguration
        from KalturaClient.Plugins.Core import (
            KalturaCategoryEntry,
            KalturaFilterPager,
            KalturaMediaEntry,
            KalturaMediaEntryFilter,
            KalturaMediaType,
            KalturaSessionType,
            KalturaUploadToken,
            KalturaUploadedFileTokenResource,
        )
    except ImportError as exc:
        fail(
            "KalturaApiClient (and lxml) required. "
            "Install with: pip3 install -r requirements.txt "
            f"({exc})"
        )
    return {
        "KalturaClient": KalturaClient,
        "KalturaConfiguration": KalturaConfiguration,
        "KalturaCategoryEntry": KalturaCategoryEntry,
        "KalturaFilterPager": KalturaFilterPager,
        "KalturaMediaEntry": KalturaMediaEntry,
        "KalturaMediaEntryFilter": KalturaMediaEntryFilter,
        "KalturaMediaType": KalturaMediaType,
        "KalturaSessionType": KalturaSessionType,
        "KalturaUploadToken": KalturaUploadToken,
        "KalturaUploadedFileTokenResource": KalturaUploadedFileTokenResource,
    }


class KalturaConfig:
    def __init__(
        self,
        *,
        partner_id: int,
        admin_secret: str,
        service_url: str,
        user_id: str,
        category_id: str | None,
        playlist_id: str | None,
    ) -> None:
        self.partner_id = partner_id
        self.admin_secret = admin_secret
        self.service_url = service_url
        self.user_id = user_id
        self.category_id = category_id
        self.playlist_id = playlist_id


def load_admin_secret(secret_file: Path | None = None) -> str | None:
    env_secret = env_str("KALTURA_ADMIN_SECRET")
    if env_secret:
        return env_secret
    path = secret_file or env_path("KALTURA_SECRET_FILE") or DEFAULT_SECRET_FILE
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    return None


def load_kaltura_config(
    *,
    require_secret: bool = True,
    secret_file: Path | None = None,
) -> KalturaConfig:
    partner_raw = env_str("KALTURA_PARTNER_ID")
    if not partner_raw:
        fail("set KALTURA_PARTNER_ID in media.env")
    try:
        partner_id = int(partner_raw)
    except ValueError:
        fail(f"KALTURA_PARTNER_ID must be an integer, got {partner_raw!r}")

    admin_secret = load_admin_secret(secret_file)
    if require_secret and not admin_secret:
        path = secret_file or env_path("KALTURA_SECRET_FILE") or DEFAULT_SECRET_FILE
        fail(
            "set KALTURA_ADMIN_SECRET or put the admin secret in "
            f"{path} (one line, no quotes)"
        )

    return KalturaConfig(
        partner_id=partner_id,
        admin_secret=admin_secret or "",
        service_url=env_str("KALTURA_SERVICE_URL", default=DEFAULT_SERVICE_URL)
        or DEFAULT_SERVICE_URL,
        user_id=env_str("KALTURA_USER_ID", default=DEFAULT_USER_ID) or DEFAULT_USER_ID,
        category_id=env_str("KALTURA_CATEGORY_ID"),
        playlist_id=env_str("KALTURA_PLAYLIST_ID"),
    )


def build_client(cfg: KalturaConfig):
    """Return an authenticated KalturaClient (admin KS)."""
    api = require_kaltura_client()
    conf = api["KalturaConfiguration"](cfg.partner_id)
    conf.serviceUrl = cfg.service_url.rstrip("/")
    client = api["KalturaClient"](conf)
    ks = client.session.start(
        cfg.admin_secret,
        cfg.user_id,
        api["KalturaSessionType"].ADMIN,
        cfg.partner_id,
        86400,
        "disableentitlement",
    )
    client.setKs(ks)
    return client


def reference_id_for(rel_path: str) -> str:
    """Stable Kaltura referenceId: media.yaml relative path."""
    return rel_path.strip().replace("\\", "/")


def find_entry_by_reference(client, reference_id: str) -> Any | None:
    api = require_kaltura_client()
    filt = api["KalturaMediaEntryFilter"]()
    filt.referenceIdEqual = reference_id
    pager = api["KalturaFilterPager"]()
    pager.pageSize = 2
    pager.pageIndex = 1
    result = client.media.list(filt, pager)
    objects = getattr(result, "objects", None) or []
    if not objects:
        return None
    return objects[0]


def find_entry_by_id(client, entry_id: str) -> Any | None:
    try:
        return client.media.get(entry_id)
    except Exception:
        return None


def list_media_by_category(client, category_id: str) -> list[Any]:
    """Return all media entries in a category (paginated)."""
    api = require_kaltura_client()
    filt = api["KalturaMediaEntryFilter"]()
    filt.categoriesIdsMatchAnd = str(category_id)
    pager = api["KalturaFilterPager"]()
    pager.pageSize = 500
    page = 1
    out: list[Any] = []
    while True:
        pager.pageIndex = page
        result = client.media.list(filt, pager)
        objects = getattr(result, "objects", None) or []
        out.extend(objects)
        total = int(getattr(result, "totalCount", 0) or 0)
        if not objects or len(out) >= total:
            break
        page += 1
    return out


def tags_to_kaltura(tags: Any) -> str:
    if tags is None:
        return ""
    if isinstance(tags, list):
        parts = [str(t).strip() for t in tags if str(t).strip()]
        return ", ".join(parts)
    text = str(tags).strip()
    return text


def upload_file_chunked(client, file_path: Path, upload_token_id: str) -> None:
    """Upload a local file to an existing uploadToken.

    Uses a single request. Lecture masters are large but Kaltura accepts the
    full file path; retry the whole entry on failure.
    """
    client.uploadToken.upload(upload_token_id, str(file_path))


def create_and_upload_entry(
    client,
    *,
    cfg: KalturaConfig,
    file_path: Path,
    title: str,
    description: str,
    tags: str,
    reference_id: str,
) -> Any:
    """Create a media entry, upload the file, optionally categorize / playlist."""
    api = require_kaltura_client()

    existing = find_entry_by_reference(client, reference_id)
    if existing is not None:
        fail(
            f"Kaltura already has referenceId={reference_id!r} "
            f"as entry {getattr(existing, 'id', '?')}; "
            "set kaltura_id in media.yaml or delete/reuse that entry"
        )

    entry = api["KalturaMediaEntry"]()
    entry.mediaType = api["KalturaMediaType"].VIDEO
    entry.name = title
    entry.description = description or ""
    entry.tags = tags or ""
    entry.referenceId = reference_id
    entry = client.media.add(entry)

    token = client.uploadToken.add(api["KalturaUploadToken"]())
    print(f"  uploading {file_path.name} ({file_path.stat().st_size} bytes)…")
    upload_file_chunked(client, file_path, token.id)

    resource = api["KalturaUploadedFileTokenResource"]()
    resource.token = token.id
    entry = client.media.addContent(entry.id, resource)

    if cfg.category_id:
        cat = api["KalturaCategoryEntry"]()
        cat.entryId = entry.id
        cat.categoryId = int(cfg.category_id)
        try:
            client.categoryEntry.add(cat)
        except Exception as exc:
            print(f"  WARNING: category attach failed: {exc}", file=sys.stderr)

    if cfg.playlist_id:
        try:
            _append_playlist_entry(client, cfg.playlist_id, entry.id)
        except Exception as exc:
            print(f"  WARNING: playlist append failed: {exc}", file=sys.stderr)

    return entry


def _append_playlist_entry(client, playlist_id: str, entry_id: str) -> None:
    playlist = client.playlist.get(playlist_id)
    content = (getattr(playlist, "playlistContent", None) or "").strip()
    ids = [part for part in content.split(",") if part.strip()]
    if entry_id in ids:
        return
    ids.append(entry_id)
    playlist.playlistContent = ",".join(ids)
    client.playlist.update(playlist_id, playlist)


def update_entry_metadata(
    client,
    entry_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: str | None = None,
    reference_id: str | None = None,
) -> Any:
    api = require_kaltura_client()
    media_entry = api["KalturaMediaEntry"]()
    if title is not None:
        media_entry.name = title
    if description is not None:
        media_entry.description = description
    if tags is not None:
        media_entry.tags = tags
    if reference_id is not None:
        media_entry.referenceId = reference_id
    return client.media.update(entry_id, media_entry)


# ---------------------------------------------------------------------------
# Public MediaSpace playlist (no admin secret)
# ---------------------------------------------------------------------------


def default_playlist_url() -> str:
    for name in ("KALTURA_PLAYLIST_URL", "KALTURA_TAB"):
        value = env_str(name)
        if value:
            return value
    fail(
        "set KALTURA_PLAYLIST_URL in media.env (or pass --url). "
        "Example: https://umsiali.mivideo.it.umich.edu/playlist/dedicated/410698932/1_6058dcqq"
    )


def default_playlist_id() -> str | None:
    return env_str("KALTURA_PLAYLIST_ID")


def default_kaltura_playlist_jsonl() -> Path | None:
    """Return dump path when env/course layout implies one; else None (stdout)."""
    path = env_path("KALTURA_PLAYLIST_JSONL")
    if path:
        return path
    kaltura_dir = env_path("KALTURA_DIR")
    if kaltura_dir:
        return kaltura_dir / "kaltura-playlist.jsonl"
    course = env_path("COURSE_ROOT")
    if course:
        return course / "kaltura" / "kaltura-playlist.jsonl"
    return None


def normalize_playlist_url(url: str) -> str:
    """Strip a trailing /{entry_id} if present so we fetch the playlist root."""
    url = url.strip().rstrip("/")
    url = url.replace("/{id}", "").rstrip("/")
    parts = url.split("/")
    if parts and ENTRY_ID_RE.match(parts[-1] or ""):
        if len(parts) >= 2 and ENTRY_ID_RE.match(parts[-2] or ""):
            url = "/".join(parts[:-1])
    return url


def playlist_id_from_url(url: str) -> str | None:
    """Best-effort playlist id from .../dedicated/<n>/<playlistId>[/<entryId>]."""
    m = PLAYLIST_ID_FROM_URL_RE.search(url.rstrip("/"))
    if not m:
        return None
    return m.group(1)


def fetch_playlist_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": PUBLIC_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        fail(f"HTTP {exc.code} fetching {url}")
    except urllib.error.URLError as exc:
        fail(f"failed to fetch {url}: {exc.reason}")


def scrape_page_title(html: str) -> str | None:
    tm = TITLE_RE.search(html)
    if not tm:
        return None
    return re.sub(r"\s+", " ", tm.group(1)).strip() or None


def scrape_guest_ks(html: str) -> str | None:
    matches = KS_RE.findall(html)
    return matches[0] if matches else None


def scrape_playlist_content_ids(html: str) -> list[str]:
    match = PLAYLIST_CONTENT_RE.search(html)
    if not match:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for part in match.group(1).split(","):
        eid = part.strip()
        if not eid or not ENTRY_ID_RE.match(eid) or eid in seen:
            continue
        seen.add(eid)
        ids.append(eid)
    return ids


def kaltura_api_public(ks: str, service: str, action: str, **params: Any) -> Any:
    data: dict[str, Any] = {
        "format": "1",
        "service": service,
        "action": action,
        "ks": ks,
    }
    data.update(params)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        KALTURA_API,
        data=body,
        method="POST",
        headers={
            "User-Agent": PUBLIC_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_duration_seconds(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(round(value)) if value >= 0 else None
    if isinstance(value, str) and value.strip():
        try:
            return int(round(float(value.strip())))
        except ValueError:
            return None
    return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def load_public_playlist_entries(
    html: str, playlist_id: str
) -> tuple[list[dict[str, Any]], str]:
    """Return (entries, source_label) for a public MediaSpace playlist.

    Each entry includes: id, title, description, duration, tags, referenceId.
    """
    ks = scrape_guest_ks(html)
    if ks:
        try:
            result = kaltura_api_public(ks, "playlist", "execute", id=playlist_id)
            if isinstance(result, dict) and result.get("code"):
                print(
                    f"WARNING: playlist.execute failed: {result.get('code')} "
                    f"{result.get('message')}",
                    file=sys.stderr,
                )
            elif isinstance(result, list) and result:
                entries: list[dict[str, Any]] = []
                for obj in result:
                    if not isinstance(obj, dict):
                        continue
                    eid = obj.get("id")
                    if not isinstance(eid, str) or not ENTRY_ID_RE.match(eid):
                        continue
                    entries.append(
                        {
                            "id": eid,
                            "title": _optional_str(obj.get("name")),
                            "description": _optional_str(obj.get("description")) or "",
                            "duration": parse_duration_seconds(obj.get("duration")),
                            "tags": _optional_str(obj.get("tags")),
                            "referenceId": _optional_str(obj.get("referenceId")),
                        }
                    )
                if entries:
                    return entries, "playlist.execute (guest KS from page)"
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
        ) as exc:
            print(f"WARNING: playlist.execute error: {exc}", file=sys.stderr)

    ids = scrape_playlist_content_ids(html)
    if not ids:
        fail(
            "could not load playlist entries "
            "(no guest KS execute result and no playlistContent in HTML)"
        )
    return (
        [
            {
                "id": eid,
                "title": None,
                "description": "",
                "duration": None,
                "tags": None,
                "referenceId": None,
            }
            for eid in ids
        ],
        "playlistContent HTML scrape (ids only)",
    )


def fetch_public_playlist(
    *,
    url: str | None = None,
    playlist_id: str | None = None,
) -> tuple[str, str, list[dict[str, Any]], str, str | None]:
    """Fetch a public playlist.

    Returns (url, playlist_id, entries, source_label, page_title).
    """
    resolved_url = normalize_playlist_url(url or default_playlist_url())
    resolved_id = (
        playlist_id
        or default_playlist_id()
        or playlist_id_from_url(resolved_url)
    )
    if not resolved_id:
        fail(
            "could not determine playlist id "
            "(set KALTURA_PLAYLIST_ID or pass --playlist-id)"
        )
    html = fetch_playlist_html(resolved_url)
    page_title = scrape_page_title(html)
    entries, source = load_public_playlist_entries(html, resolved_id)
    return resolved_url, resolved_id, entries, source, page_title
