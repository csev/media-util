#!/usr/bin/env python3
"""Compare a public Kaltura/MediaSpace playlist to media.yaml (no admin secret).

Flow:
  1. Fetch the public playlist (see dump-kaltura-playlist.py / kaltura_common)
  2. Compare against media.yaml:
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
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402
import kaltura_common as kc  # noqa: E402

# media.yaml duration is integer seconds from ffprobe; Kaltura duration is seconds.
DURATION_TOLERANCE_SEC = 2


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
                "duration": kc.parse_duration_seconds(entry.get("duration")),
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
    print("fetching…")
    url, playlist_id, pl_entries, source, page_title = kc.fetch_public_playlist(
        url=args.url,
        playlist_id=args.playlist_id,
    )
    print(f"playlist url: {url}")
    print(f"playlist id:  {playlist_id}")
    if page_title:
        print(f"page title:   {page_title}")
    print(f"source:       {source}")
    print(f"playlist:     {len(pl_entries)} entr(y/ies)")

    # Adapt dump-shaped entries (title) to the prior name field used below.
    for entry in pl_entries:
        entry["name"] = entry.get("title")

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
