#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import tempfile


def load_replacements(path):
    replacements = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if "=>" not in line:
                continue

            left, right = line.split("=>", 1)

            left = left.strip()
            right = right.strip()

            if not left:
                continue

            # Skip no-op replacements.
            if left == right:
                continue

            replacements.append((left, right))

    # Longest-first prevents partial replacement collisions.
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    return replacements


TIMESTAMP_RE = re.compile(
    r"^\d\d:\d\d:\d\d[,.]\d\d\d\s+-->"
)

VTT_TIMESTAMP_RE = re.compile(
    r"^\d\d:\d\d\.\d\d\d\s+-->"
)

SRT_INDEX_RE = re.compile(r"^\d+$")


def is_caption_metadata(line):
    stripped = line.strip()

    if not stripped:
        return True

    if stripped == "WEBVTT":
        return True

    if TIMESTAMP_RE.match(stripped):
        return True

    if VTT_TIMESTAMP_RE.match(stripped):
        return True

    if SRT_INDEX_RE.match(stripped):
        return True

    return False


def apply_replacements(text, replacements, stats):
    out = text

    for src, dst in replacements:
        count = out.count(src)

        if count > 0:
            stats[(src, dst)] = stats.get((src, dst), 0) + count
            out = out.replace(src, dst)

    return out


def cleanup_file(path, replacements, make_backup=False):
    suffix = path.suffix.lower()

    stats = {}

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    output = []

    for line in lines:
        if suffix in [".vtt", ".srt"]:
            if is_caption_metadata(line):
                output.append(line)
                continue

        cleaned = apply_replacements(line, replacements, stats)
        output.append(cleaned)

    if output == lines:
        print(f"NO CHANGES: {path}")
        return

    if make_backup:
        backup = path.with_suffix(path.suffix + ".raw")

        if not backup.exists():
            shutil.copy2(path, backup)

    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        encoding="utf-8",
        dir=str(path.parent),
    ) as tmp:
        tmp.writelines(output)
        temp_name = tmp.name

    pathlib.Path(temp_name).replace(path)

    print(f"UPDATED: {path}")

    if stats:
        print("Replacement counts:")
        for (src, dst), count in sorted(stats.items()):
            print(f"  {src} => {dst} ({count})")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--replacements",
        required=True,
    )

    parser.add_argument(
        "--backup",
        action="store_true",
        help="Keep a .raw backup of the original file before cleanup.",
    )

    parser.add_argument(
        "files",
        nargs="+",
    )

    args = parser.parse_args()

    replacements = load_replacements(args.replacements)

    for filename in args.files:
        cleanup_file(
            pathlib.Path(filename),
            replacements,
            make_backup=args.backup,
        )


if __name__ == "__main__":
    main()
