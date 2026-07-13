#!/usr/bin/env python3
"""Compare whisper/ artifacts against MEDIA_ROOT; optionally remove orphans."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common  # noqa: E402


WHISPER_KINDS = ("txt", "vtt", "srt", "desc")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report whisper/ files whose matching media no longer exists "
            "under MEDIA_ROOT. Use --remove to delete them."
        )
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=None,
        help="Media binary tree (default: $MEDIA_ROOT)",
    )
    parser.add_argument(
        "--whisper-root",
        type=Path,
        default=None,
        help="Whisper tree (default: $WHISPER_ROOT)",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Delete orphan whisper artifacts",
    )
    return parser.parse_args()


def default_whisper_root() -> Path:
    import os

    value = os.environ.get("WHISPER_ROOT")
    if not value:
        raise SystemExit(
            "Error: set WHISPER_ROOT (source media.env) or pass --whisper-root"
        )
    return Path(value)


def main() -> int:
    args = parse_args()
    bootstrap = common.load_bootstrap()

    media_root = args.media_root or common.default_media_root()
    whisper_root = args.whisper_root or default_whisper_root()

    media_files = bootstrap.scan_media_root(media_root)
    media_stems = {Path(name).with_suffix("").as_posix() for name in media_files}

    print(f"MEDIA_ROOT:   {media_root} ({len(media_files)} media files)")
    print(f"whisper root: {whisper_root}")

    orphans: list[Path] = []
    present = 0
    for kind in WHISPER_KINDS:
        root = whisper_root / kind
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            stem = path.relative_to(root).with_suffix("").as_posix()
            if stem in media_stems:
                present += 1
            else:
                orphans.append(path)

    problems = common.section(
        "Whisper artifacts with no matching MEDIA_ROOT file",
        [str(p.relative_to(whisper_root)) for p in orphans],
    )
    print(f"\nWhisper artifacts still matched to MEDIA_ROOT: {present}")

    if args.remove and orphans:
        for path in orphans:
            path.unlink()
            print(f"REMOVED: {path.relative_to(whisper_root)}")

        print(f"\nRemoved {len(orphans)} orphan whisper artifact(s).")
        return 0

    return common.summary_and_exit(problems)


if __name__ == "__main__":
    sys.exit(main())
