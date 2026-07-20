#!/usr/bin/env python3
"""Refresh media.yaml filesystem / container metadata from MEDIA_ROOT files.

Updates (per entry that needs it):
  size, md5, duration, duration_text, container_creation, qt_creation

Does **not** change title / tags / description / youtube_id / kaltura_id.

Selection:
  - default: entries whose on-disk size differs from yaml, or that are missing
    size/md5/duration/creation fields
  - --all: re-probe every entry that exists on disk (expensive: full md5)
  - --only REL: force refresh for one or more relative paths

Dry-run by default; pass --apply to write media.yaml.

Example:
  source media.env
  update-media-meta-from-fs.py
  update-media-meta-from-fs.py --only lesson-31-ads3/03-favwc-walkthrough.m4v
  update-media-meta-from-fs.py --only lesson-31-ads3/03-favwc-walkthrough.m4v --apply
  update-media-meta-from-fs.py --all --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402

META_KEYS = (
    "size",
    "md5",
    "duration",
    "duration_text",
    "container_creation",
    "qt_creation",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Update size/md5/duration/creation fields in media.yaml from MEDIA_ROOT."
        )
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=None,
        help="Media binary tree (default: $MEDIA_ROOT)",
    )
    parser.add_argument(
        "--media-yaml",
        type=Path,
        default=None,
        help="Path to media.yaml (default: $MEDIA_YAML / $COURSE_ROOT/media.yaml)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="REL",
        help="Only refresh this relative path (repeatable)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Re-probe every on-disk media.yaml entry (full md5)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write media.yaml (default is dry-run)",
    )
    return parser.parse_args()


def yaml_value(entry: dict[str, Any], key: str) -> Any:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def needs_refresh(
    entry: dict[str, Any],
    path: Path,
    *,
    force: bool,
) -> tuple[bool, str]:
    if force:
        return True, "forced"
    size = yaml_value(entry, "size")
    try:
        disk_size = path.stat().st_size
    except OSError as exc:
        raise SystemExit(f"Error: cannot stat {path}: {exc}") from exc
    if size is None or int(size) != disk_size:
        return True, f"size yaml={size} disk={disk_size}"
    for key in ("md5", "duration", "duration_text", "container_creation"):
        # qt_creation is optional (absent on many M4Vs) — do not force refresh alone
        if yaml_value(entry, key) is None:
            return True, f"missing {key}"
    return False, "ok"


def normalize_existing(value: Any, key: str, bootstrap: Any) -> Any:
    if key in ("container_creation", "qt_creation"):
        return bootstrap.normalize_creation_timestamp(value)
    if key == "duration_text" and value is not None:
        return str(value)
    if key in ("size", "duration") and value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if key == "md5" and isinstance(value, str):
        return value.strip().lower() or None
    return value


def apply_meta(entry: CommentedMap, meta: dict[str, Any], bootstrap: Any) -> list[str]:
    """Write META_KEYS onto entry (reordering known keys). Return change lines."""
    changes: list[str] = []
    new_vals = {
        "size": meta["size"],
        "md5": meta["md5"],
        "duration": meta["duration"],
        "duration_text": bootstrap.format_duration_text(meta["duration"]),
        "container_creation": meta.get("container_creation"),
        "qt_creation": meta.get("qt_creation"),
    }

    for key in META_KEYS:
        old = normalize_existing(entry.get(key), key, bootstrap)
        new = new_vals[key]
        if old != new:
            changes.append(f"  {key}: {old!r} -> {new!r}")

    # Rebuild known key order while preserving extras / description placement.
    known = [key for key in entry.keys() if key in bootstrap.ENTRY_KEYS]
    needs_reorder = known != list(bootstrap.ENTRY_KEYS)
    if needs_reorder or not entry:
        extras = [
            (key, value)
            for key, value in list(entry.items())
            if key not in bootstrap.ENTRY_KEYS
        ]
        preserved = {
            key: entry.get(key)
            for key in bootstrap.ENTRY_KEYS
            if key not in META_KEYS
        }
        entry.clear()
        for key in bootstrap.ENTRY_KEYS:
            if key in META_KEYS:
                entry[key] = new_vals[key]
            else:
                entry[key] = preserved.get(key)
        for key, value in extras:
            entry[key] = value
    else:
        for key in META_KEYS:
            entry[key] = new_vals[key]
    return changes


def main() -> int:
    args = parse_args()
    bootstrap = common.load_bootstrap()
    ffprobe = bootstrap.require_ffprobe()

    media_root = args.media_root or common.default_media_root()
    media_yaml = args.media_yaml or common.default_media_yaml()
    if not media_root.is_dir():
        raise SystemExit(f"Error: media root is not a directory: {media_root}")

    yaml = bootstrap.build_yaml()
    data = bootstrap.load_existing(media_yaml, yaml)
    entries = data.get("entries")
    if not isinstance(entries, CommentedMap):
        raise SystemExit(f"Error: {media_yaml} entries must be a mapping")

    only = [rel.strip().lstrip("./") for rel in args.only if rel and rel.strip()]
    if only:
        candidates = only
    else:
        candidates = list(entries.keys())

    print(f"MEDIA_ROOT:  {media_root}")
    print(f"media.yaml:  {media_yaml} ({len(entries)} entries)")
    print(f"mode:        {'APPLY' if args.apply else 'DRY-RUN'}")
    if only:
        print(f"only:        {len(only)} path(s)")
    elif args.all:
        print("selection:   --all (every on-disk entry)")
    else:
        print("selection:   size mismatch or missing meta fields")

    refreshed = 0
    skipped = 0
    missing = 0
    changed_entries = 0

    for rel in candidates:
        entry = entries.get(rel)
        if entry is None:
            print(f"SKIP missing yaml entry: {rel}", file=sys.stderr)
            missing += 1
            continue
        entry = bootstrap.ensure_entry_map(entry)
        entries[rel] = entry

        path = media_root / rel
        if not path.is_file():
            print(f"SKIP missing file: {path}", file=sys.stderr)
            missing += 1
            continue

        force = bool(only) or args.all
        do_it, reason = needs_refresh(entry, path, force=force)
        if not do_it:
            skipped += 1
            continue

        print(f"\n{rel}  ({reason})")
        probed = bootstrap.probe_media_meta(ffprobe, path)
        disk_size = path.stat().st_size
        yaml_size = yaml_value(entry, "size")
        yaml_md5 = yaml_value(entry, "md5")
        # Avoid full-file md5 when size already matches and we already have an md5
        # (typical backfill of creation timestamps).
        if (
            yaml_size is not None
            and int(yaml_size) == disk_size
            and isinstance(yaml_md5, str)
            and yaml_md5.strip()
            and not force
            and reason.startswith("missing")
        ):
            md5 = yaml_md5.strip().lower()
        else:
            md5 = bootstrap.file_md5(path)
        meta = {
            "size": disk_size,
            "md5": md5,
            "duration": probed["duration"],
            "container_creation": probed.get("container_creation"),
            "qt_creation": probed.get("qt_creation"),
        }
        changes = apply_meta(entry, meta, bootstrap)
        if not changes:
            print("  (no field changes)")
            refreshed += 1
            continue
        for line in changes:
            print(line)
        refreshed += 1
        changed_entries += 1

    print(
        f"\nRefreshed: {refreshed}  changed: {changed_entries}  "
        f"skipped: {skipped}  missing: {missing}"
    )

    if changed_entries == 0:
        print("Nothing to write.")
        return 0

    if not args.apply:
        print("Dry-run only; re-run with --apply to write media.yaml.")
        return 0

    data["entries"] = entries
    data = bootstrap.order_root_map(data)
    try:
        with media_yaml.open("w", encoding="utf-8") as handle:
            yaml.dump(data, handle)
    except OSError as exc:
        raise SystemExit(f"Error: cannot write {media_yaml}: {exc}") from exc
    print(f"Wrote {media_yaml}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
