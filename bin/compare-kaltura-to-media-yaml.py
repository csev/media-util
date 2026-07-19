#!/usr/bin/env python3
"""Compare a public Kaltura/MediaSpace playlist to media.yaml (no admin secret).

Flow:
  1. Fetch the public playlist HTML ($KALTURA_PLAYLIST_URL)
  2. Pull a guest KS embedded in the page (when present)
  3. Call playlist.execute for ordered entries (id, name)
  4. Fall back to scraping playlistContent ids if execute is unavailable

Compares against media.yaml:
  - kaltura_id membership
  - title (media.yaml title vs Kaltura name)
  - duration (seconds; ±2s tolerance)
  - order of shared ids (media.yaml entry order vs playlist order)

Example:
  source media.env
  compare-kaltura-to-media-yaml.py
  compare-kaltura-to-media-yaml.py --url 'https://umsiali.mivideo.it.umich.edu/playlist/dedicated/410698932/1_6058dcqq'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402

USER_AGENT = "media-util-compare-kaltura-to-media-yaml/1.0"
KALTURA_API = "https://www.kaltura.com/api_v3/index.php"
PLAYLIST_CONTENT_RE = re.compile(
    r'playlistContent["\']?\s*[:=]\s*["\']([^"\']+)["\']'
)
KS_RE = re.compile(r'"ks"\s*:\s*"([^"]+)"')
ENTRY_ID_RE = re.compile(r"^1_[a-zA-Z0-9]+$")
TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
PLAYLIST_ID_FROM_URL_RE = re.compile(r"/(1_[a-zA-Z0-9]+)(?:/(1_[a-zA-Z0-9]+))?/?$")
# media.yaml duration is integer seconds from ffprobe; Kaltura duration is seconds.
DURATION_TOLERANCE_SEC = 2


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def default_playlist_url() -> str:
    for name in ("KALTURA_PLAYLIST_URL", "KALTURA_TAB"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    fail(
        "set KALTURA_PLAYLIST_URL in media.env (or pass --url). "
        "Example: https://umsiali.mivideo.it.umich.edu/playlist/dedicated/410698932/1_6058dcqq"
    )


def default_playlist_id() -> str | None:
    value = os.environ.get("KALTURA_PLAYLIST_ID")
    if value and value.strip():
        return value.strip()
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
    # If two trailing entry-shaped ids, first is playlist, second is entry.
    if m.group(2):
        return m.group(1)
    return m.group(1)


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
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


def kaltura_api(ks: str, service: str, action: str, **params: Any) -> Any:
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
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_playlist_entries(
    html: str, playlist_id: str
) -> tuple[list[dict[str, Any]], str]:
    """Return (entries, source_label).

    Each entry: {id, name, duration} in playlist order.
    """
    ks = scrape_guest_ks(html)
    if ks:
        try:
            result = kaltura_api(ks, "playlist", "execute", id=playlist_id)
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
                    name = obj.get("name")
                    entries.append(
                        {
                            "id": eid,
                            "name": name.strip()
                            if isinstance(name, str) and name.strip()
                            else None,
                            "duration": parse_duration_seconds(obj.get("duration")),
                        }
                    )
                if entries:
                    return entries, "playlist.execute (guest KS from page)"
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            print(f"WARNING: playlist.execute error: {exc}", file=sys.stderr)

    ids = scrape_playlist_content_ids(html)
    if not ids:
        fail(
            "could not load playlist entries "
            "(no guest KS execute result and no playlistContent in HTML)"
        )
    return (
        [{"id": eid, "name": None, "duration": None} for eid in ids],
        "playlistContent HTML scrape (ids only)",
    )


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


def normalize_title(text: str) -> str:
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def yaml_rows(entries: dict[str, Any]) -> list[dict[str, Any]]:
    """media.yaml entries with kaltura_id, in file order."""
    rows: list[dict[str, Any]] = []
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kaltura_id")
        if not (isinstance(kid, str) and kid.strip()):
            continue
        title = entry.get("title")
        rows.append(
            {
                "rel": str(rel),
                "kaltura_id": kid.strip(),
                "title": title.strip()
                if isinstance(title, str) and title.strip()
                else None,
                "duration": parse_duration_seconds(entry.get("duration")),
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare public MediaSpace playlist (ids, titles, duration, order) "
            "to media.yaml without an admin API secret."
        )
    )
    parser.add_argument(
        "--media-yaml",
        type=Path,
        default=None,
        help="Path to media.yaml (default: $MEDIA_YAML / $COURSE_ROOT/media.yaml)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "Public playlist URL (default: $KALTURA_PLAYLIST_URL). "
            "Trailing /{entry_id} is stripped automatically."
        ),
    )
    parser.add_argument(
        "--playlist-id",
        default=None,
        help="Kaltura playlist id (default: $KALTURA_PLAYLIST_ID or from URL)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    media_yaml = args.media_yaml or common.default_media_yaml()
    url = normalize_playlist_url(args.url or default_playlist_url())
    playlist_id = (
        args.playlist_id
        or default_playlist_id()
        or playlist_id_from_url(url)
    )
    if not playlist_id:
        fail("could not determine playlist id (set KALTURA_PLAYLIST_ID or pass --playlist-id)")

    data = common.load_media_yaml(media_yaml)
    entries = data["entries"]
    yrows = yaml_rows(entries)
    yaml_by_id: dict[str, dict[str, Any]] = {}
    for row in yrows:
        yaml_by_id.setdefault(row["kaltura_id"], row)

    empty_yaml = []
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kaltura_id")
        if not (isinstance(kid, str) and kid.strip()):
            empty_yaml.append(str(rel))

    print(f"media.yaml:   {media_yaml} ({len(entries)} entries, {len(yrows)} with kaltura_id)")
    print(f"playlist url: {url}")
    print(f"playlist id:  {playlist_id}")
    print("fetching…")
    html = fetch_html(url)
    page_title = scrape_page_title(html)
    if page_title:
        print(f"page title:   {page_title}")

    pl_entries, source = load_playlist_entries(html, playlist_id)
    print(f"source:       {source}")
    print(f"playlist:     {len(pl_entries)} entr(y/ies)")

    pl_ids = [e["id"] for e in pl_entries]
    pl_set = set(pl_ids)
    yaml_set = set(yaml_by_id.keys())
    pl_by_id = {e["id"]: e for e in pl_entries}

    missing_in_yaml = [
        f"{i + 1:02d}  {eid}"
        + (f"  {pl_by_id[eid]['name']}" if pl_by_id[eid].get("name") else "")
        for i, eid in enumerate(pl_ids)
        if eid not in yaml_set
    ]
    missing_in_playlist = [
        f"{row['rel']}  kaltura_id={row['kaltura_id']}"
        for row in yrows
        if row["kaltura_id"] not in pl_set
    ]

    title_mismatch: list[str] = []
    duration_mismatch: list[str] = []
    for eid in pl_ids:
        if eid not in yaml_by_id:
            continue
        y = yaml_by_id[eid]
        p = pl_by_id[eid]
        y_title = y.get("title")
        p_title = p.get("name")
        if y_title and p_title and normalize_title(y_title) != normalize_title(p_title):
            title_mismatch.append(
                f"{y['rel']}\n"
                f"    yaml:     {y_title}\n"
                f"    kaltura:  {p_title}"
            )
        elif y_title and not p_title:
            title_mismatch.append(
                f"{y['rel']}\n"
                f"    yaml:     {y_title}\n"
                f"    kaltura:  (no name from scrape)"
            )

        y_dur = y.get("duration")
        p_dur = p.get("duration")
        if y_dur is not None and p_dur is not None:
            if abs(y_dur - p_dur) > DURATION_TOLERANCE_SEC:
                duration_mismatch.append(
                    f"{y['rel']}  kaltura_id={eid}  "
                    f"yaml={y_dur}s  kaltura={p_dur}s  "
                    f"delta={p_dur - y_dur:+d}s"
                )
        elif y_dur is not None and p_dur is None:
            duration_mismatch.append(
                f"{y['rel']}  kaltura_id={eid}  "
                f"yaml={y_dur}s  kaltura=(no duration from scrape)"
            )

    # Order: shared ids in media.yaml file order vs playlist order
    yaml_shared = [row["kaltura_id"] for row in yrows if row["kaltura_id"] in pl_set]
    pl_shared = [eid for eid in pl_ids if eid in yaml_set]
    order_mismatch: list[str] = []
    if yaml_shared != pl_shared:
        # Show first divergence and a short window
        limit = min(len(yaml_shared), len(pl_shared))
        first = None
        for i in range(limit):
            if yaml_shared[i] != pl_shared[i]:
                first = i
                break
        if first is None:
            order_mismatch.append(
                f"shared prefix matches but lengths differ: "
                f"yaml_shared={len(yaml_shared)} playlist_shared={len(pl_shared)}"
            )
        else:
            order_mismatch.append(f"first divergence at shared index {first}:")
            start = max(0, first - 1)
            end = min(limit, first + 4)
            for i in range(start, end):
                yid = yaml_shared[i]
                pid = pl_shared[i]
                yrel = yaml_by_id[yid]["rel"]
                pname = pl_by_id[pid].get("name") or ""
                mark = "  " if yid == pid else "!!"
                order_mismatch.append(
                    f"  {mark} [{i}] yaml={yid} ({yrel})"
                )
                order_mismatch.append(
                    f"       pl  ={pid}"
                    + (f" ({pname})" if pname else "")
                )
        # Also list full position maps for ids that appear in both but different index
        y_pos = {eid: i for i, eid in enumerate(yaml_shared)}
        p_pos = {eid: i for i, eid in enumerate(pl_shared)}
        moved = []
        for eid in yaml_shared:
            if y_pos[eid] != p_pos[eid]:
                moved.append(
                    f"  {eid}  yaml_pos={y_pos[eid]}  playlist_pos={p_pos[eid]}  "
                    f"{yaml_by_id[eid]['rel']}"
                )
        if moved:
            order_mismatch.append(f"ids with different shared positions ({len(moved)}):")
            order_mismatch.extend(moved[:25])
            if len(moved) > 25:
                order_mismatch.append(f"  … +{len(moved) - 25} more")

    matched = len(yaml_shared)
    print(f"matched ids:  {matched}")

    problems = 0
    problems += common.section(
        "In playlist but missing from media.yaml kaltura_id", missing_in_yaml
    )
    problems += common.section(
        "In media.yaml but not in playlist", missing_in_playlist
    )
    problems += common.section(
        "media.yaml entries with null/empty kaltura_id", empty_yaml
    )
    problems += common.section("Title mismatch (media.yaml vs Kaltura name)", title_mismatch)
    problems += common.section(
        f"Duration mismatch (tolerance ±{DURATION_TOLERANCE_SEC}s)",
        duration_mismatch,
    )
    problems += common.section(
        "Playlist order differs from media.yaml order (shared ids)",
        order_mismatch,
    )

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
