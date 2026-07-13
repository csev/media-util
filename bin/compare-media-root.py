#!/usr/bin/env python3
"""Compare MEDIA_ROOT files against media.yaml entries."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report inconsistencies between MEDIA_ROOT and media.yaml."
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
        "--check-meta",
        action="store_true",
        help="Also compare size / md5 / duration presence for files present in both",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bootstrap = common.load_bootstrap()

    media_root = args.media_root or common.default_media_root()
    media_yaml = args.media_yaml or common.default_media_yaml()

    root_files = set(bootstrap.scan_media_root(media_root))
    data = common.load_media_yaml(media_yaml)
    yaml_files = set(common.entry_keys(data))

    print(f"MEDIA_ROOT:  {media_root} ({len(root_files)} media files)")
    print(f"media.yaml:  {media_yaml} ({len(yaml_files)} entries)")

    only_root = sorted(root_files - yaml_files)
    only_yaml = sorted(yaml_files - root_files)
    both = sorted(root_files & yaml_files)

    problems = 0
    problems += common.section("In MEDIA_ROOT but missing from media.yaml", only_root)
    problems += common.section("In media.yaml but missing from MEDIA_ROOT", only_yaml)

    if args.check_meta:
        size_mismatch: list[str] = []
        md5_mismatch: list[str] = []
        missing_meta: list[str] = []
        for rel in both:
            path = media_root / rel
            entry = data["entries"][rel] or {}
            size = entry.get("size")
            md5 = entry.get("md5")
            if size is None or md5 is None:
                missing_meta.append(rel)
                continue
            actual_size = path.stat().st_size
            if int(size) != actual_size:
                size_mismatch.append(f"{rel}  yaml={size}  disk={actual_size}")
            actual_md5 = file_md5(path)
            if str(md5) != actual_md5:
                md5_mismatch.append(f"{rel}  yaml={md5}  disk={actual_md5}")
        problems += common.section("Size mismatches", size_mismatch)
        problems += common.section("MD5 mismatches", md5_mismatch)
        problems += common.section("Missing size/md5 in media.yaml", missing_meta)
    else:
        print(f"\nIn both: {len(both)} (use --check-meta to verify size/md5)")

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
