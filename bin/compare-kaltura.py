#!/usr/bin/env python3
"""Compare media.yaml kaltura_id / referenceId values against Kaltura.

Reports:
  - media.yaml entries missing kaltura_id
  - yaml kaltura_id not found in Kaltura
  - Kaltura entries (in category, if set) whose referenceId is a media.yaml path
    but yaml has a different / missing id
  - referenceId mismatches for yaml entries that do have kaltura_id

Example:
  source media.env
  compare-kaltura.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402
import kaltura_common as kc  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report inconsistencies between media.yaml and Kaltura."
    )
    parser.add_argument(
        "--media-yaml",
        type=Path,
        default=None,
        help="Path to media.yaml (default: $MEDIA_YAML / $COURSE_ROOT/media.yaml)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    media_yaml = args.media_yaml or kc.default_media_yaml()
    data = common.load_media_yaml(media_yaml)
    entries = data["entries"]

    cfg = kc.load_kaltura_config(require_secret=True)
    client = kc.build_client(cfg)

    print(f"media.yaml:  {media_yaml} ({len(entries)} entries)")
    print(f"partner:     {cfg.partner_id}")
    print(f"service:     {cfg.service_url}")
    if cfg.category_id:
        print(f"category:    {cfg.category_id}")
    if cfg.playlist_id:
        print(f"playlist:    {cfg.playlist_id}")

    missing_id: list[str] = []
    yaml_ids: dict[str, list[str]] = {}
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kaltura_id")
        if isinstance(kid, str) and kid.strip():
            yaml_ids.setdefault(kid.strip(), []).append(rel)
        else:
            missing_id.append(rel)

    not_in_kaltura: list[str] = []
    reference_mismatch: list[str] = []
    for kid, rels in sorted(yaml_ids.items()):
        remote = kc.find_entry_by_id(client, kid)
        if remote is None:
            for rel in rels:
                not_in_kaltura.append(f"{rel}  kaltura_id={kid}")
            continue
        remote_ref = getattr(remote, "referenceId", None) or ""
        for rel in rels:
            expected = kc.reference_id_for(rel)
            if remote_ref and remote_ref != expected:
                reference_mismatch.append(
                    f"{rel}  kaltura_id={kid}  "
                    f"referenceId={remote_ref!r}  expected={expected!r}"
                )

    # Optional: scan category for referenceIds that look like media paths.
    orphan_or_drift: list[str] = []
    if cfg.category_id:
        remote_entries = kc.list_media_by_category(client, cfg.category_id)
        print(f"category entries: {len(remote_entries)}")
        yaml_by_rel = {
            rel: entry
            for rel, entry in entries.items()
            if isinstance(entry, dict)
        }
        for remote in remote_entries:
            ref = getattr(remote, "referenceId", None) or ""
            rid = getattr(remote, "id", None) or ""
            if not ref or ref not in yaml_by_rel:
                continue
            entry = yaml_by_rel[ref]
            current = entry.get("kaltura_id")
            current_id = (
                current.strip()
                if isinstance(current, str) and current.strip()
                else None
            )
            if current_id is None:
                orphan_or_drift.append(
                    f"{ref}  kaltura has {rid} but media.yaml kaltura_id is empty"
                )
            elif current_id != rid:
                orphan_or_drift.append(
                    f"{ref}  yaml={current_id}  kaltura={rid}"
                )
    else:
        # Without a category, still check missing yaml ids via reference lookup.
        for rel in missing_id:
            remote = kc.find_entry_by_reference(client, kc.reference_id_for(rel))
            if remote is not None:
                rid = getattr(remote, "id", "?")
                orphan_or_drift.append(
                    f"{rel}  kaltura has referenceId match {rid} "
                    "(use upload --adopt)"
                )

    problems = 0
    problems += common.section(
        "media.yaml entries with null/empty kaltura_id", missing_id
    )
    problems += common.section(
        "media.yaml kaltura_id not found in Kaltura", not_in_kaltura
    )
    problems += common.section(
        "media.yaml kaltura_id with unexpected referenceId", reference_mismatch
    )
    problems += common.section(
        "Kaltura referenceId matches media path but yaml id differs/missing",
        orphan_or_drift,
    )

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
