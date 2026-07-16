#!/usr/bin/env python3
"""Upload media.yaml lectures that lack kaltura_id to Kaltura.

Dry-run by default. With --apply, uploads each missing file (title, description,
tags, referenceId=media path), then writes the new kaltura_id into media.yaml
after every successful upload so a mid-batch abort keeps progress.

Requires Kaltura admin credentials (see kaltura_common.py / media.env).

Example:
  source media.env
  upload-kaltura-from-media-yaml.py              # preview
  upload-kaltura-from-media-yaml.py --apply --limit 1
  upload-kaltura-from-media-yaml.py --apply --only lesson-01-welcome
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kaltura_common as kc  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Upload media.yaml entries without kaltura_id to Kaltura "
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
        "--media-root",
        type=Path,
        default=None,
        help="Media binary tree (default: $MEDIA_ROOT)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform uploads and write kaltura_id back to media.yaml",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Upload at most N missing entries (0 = no limit)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Only media path substring(s) (repeatable)",
    )
    parser.add_argument(
        "--adopt",
        action="store_true",
        help=(
            "If Kaltura already has this referenceId, adopt that entry id into "
            "media.yaml instead of uploading"
        ),
    )
    return parser.parse_args()


def load_yaml_rt(path: Path) -> CommentedMap:
    if not path.is_file():
        kc.fail(f"media.yaml not found: {path}")
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "entries" not in data:
        kc.fail(f"{path} must contain an 'entries' mapping")
    if data["entries"] is None:
        data["entries"] = CommentedMap()
    if not isinstance(data["entries"], dict):
        kc.fail(f"{path} 'entries' must be a mapping")
    return data  # type: ignore[return-value]


def save_yaml_rt(path: Path, data: CommentedMap) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.width = 4096
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    tmp.replace(path)


def has_kaltura_id(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    value = entry.get("kaltura_id")
    return isinstance(value, str) and bool(value.strip())


def entry_matches_only(rel: str, only: list[str]) -> bool:
    if not only:
        return True
    blob = rel.lower()
    return any(token.lower() in blob for token in only if token.strip())


def candidates(
    data: CommentedMap,
    media_root: Path,
    only: list[str],
) -> list[tuple[str, dict[str, Any], Path]]:
    rows: list[tuple[str, dict[str, Any], Path]] = []
    entries = data["entries"]
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        if has_kaltura_id(entry):
            continue
        if not entry_matches_only(rel, only):
            continue
        path = media_root / rel
        if not path.is_file():
            print(f"WARNING: missing media file, skip: {path}", file=sys.stderr)
            continue
        rows.append((rel, entry, path))
    return rows


def main() -> int:
    args = parse_args()
    media_yaml = args.media_yaml or kc.default_media_yaml()
    media_root = args.media_root or kc.default_media_root()

    data = load_yaml_rt(media_yaml)
    rows = candidates(data, media_root, args.only)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    print(f"media.yaml:  {media_yaml}")
    print(f"MEDIA_ROOT:  {media_root}")
    print(f"missing kaltura_id (selected): {len(rows)}")
    if not rows:
        print("Nothing to upload.")
        return 0

    for rel, entry, path in rows:
        title = entry.get("title") or path.stem
        ref = kc.reference_id_for(rel)
        print(f"  {rel}")
        print(f"    title={title}")
        print(f"    referenceId={ref}")
        print(f"    file={path} ({path.stat().st_size} bytes)")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to upload and write kaltura_id.")
        return 0

    cfg = kc.load_kaltura_config(require_secret=True)
    print(f"\nKaltura partner={cfg.partner_id} service={cfg.service_url}")
    if cfg.category_id:
        print(f"category_id={cfg.category_id}")
    if cfg.playlist_id:
        print(f"playlist_id={cfg.playlist_id}")

    client = kc.build_client(cfg)
    uploaded = 0
    adopted = 0

    for rel, entry, path in rows:
        ref = kc.reference_id_for(rel)
        title = str(entry.get("title") or path.stem)
        description = entry.get("description") or ""
        if not isinstance(description, str):
            description = str(description)
        tags = kc.tags_to_kaltura(entry.get("tags"))

        print(f"\n→ {rel}")
        existing = kc.find_entry_by_reference(client, ref)
        if existing is not None:
            entry_id = getattr(existing, "id", None)
            if args.adopt and isinstance(entry_id, str) and entry_id.strip():
                entry["kaltura_id"] = entry_id.strip()
                save_yaml_rt(media_yaml, data)
                print(f"  adopted existing entry {entry_id}")
                adopted += 1
                continue
            kc.fail(
                f"referenceId {ref!r} already exists as {entry_id}; "
                "pass --adopt to record it, or remove/reuse that Kaltura entry"
            )

        try:
            created = kc.create_and_upload_entry(
                client,
                cfg=cfg,
                file_path=path,
                title=title,
                description=description,
                tags=tags,
                reference_id=ref,
            )
        except SystemExit:
            raise
        except Exception as exc:
            print(f"  ERROR: upload failed: {exc}", file=sys.stderr)
            return 1

        entry_id = getattr(created, "id", None)
        if not isinstance(entry_id, str) or not entry_id.strip():
            print("  ERROR: upload returned no entry id", file=sys.stderr)
            return 1

        entry["kaltura_id"] = entry_id.strip()
        save_yaml_rt(media_yaml, data)
        print(f"  kaltura_id={entry_id} (saved to media.yaml)")
        uploaded += 1

    print(f"\nDone. uploaded={uploaded} adopted={adopted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
