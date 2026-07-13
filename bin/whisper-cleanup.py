#!/usr/bin/env python3
"""Apply *whisper-replacements* rules to whisper transcripts.

With media.env sourced and no arguments:

    whisper-cleanup.py

discovers *whisper-replacements* under $WHISPER_ROOT (then upward) and cleans
all files under $WHISPER_ROOT/{txt,vtt,srt}/.

You can still pass --replacements and explicit files to override.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import sys
import tempfile


def parse_replacement_line(line: str) -> tuple[str, str] | None:
    """Parse one rule: 'from => to', or tab/2+ spaces separated (DJ4E style)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    if "=>" in line:
        left, right = line.split("=>", 1)
    elif "\t" in line:
        left, right = line.split("\t", 1)
    else:
        # Two or more spaces: incorrect<spaces>correct
        match = re.match(r"^(.+?) {2,}(.+)$", line)
        if not match:
            return None
        left, right = match.group(1), match.group(2)

    left = left.strip()
    right = right.strip()
    if not left or left == right:
        return None
    return left, right


def load_replacements(path: pathlib.Path) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parsed = parse_replacement_line(line)
            if parsed is None:
                continue
            replacements.append(parsed)

    return replacements


def merge_replacements(paths: list[pathlib.Path]) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    for path in paths:
        replacements.extend(load_replacements(path))
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


def is_caption_metadata(line: str) -> bool:
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


def apply_replacements(text: str, replacements: list[tuple[str, str]], stats: dict) -> str:
    out = text

    for src, dst in replacements:
        count = out.count(src)

        if count > 0:
            stats[(src, dst)] = stats.get((src, dst), 0) + count
            out = out.replace(src, dst)

    return out


def cleanup_file(path: pathlib.Path, replacements: list[tuple[str, str]], make_backup: bool = False) -> None:
    suffix = path.suffix.lower()

    stats: dict[tuple[str, str], int] = {}

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


def find_matching_files_upward(pattern: str, start: pathlib.Path) -> list[pathlib.Path]:
    """Return all matches in the first directory that has any."""
    dir_path = start.resolve()
    while True:
        matches = sorted(dir_path.glob(pattern))
        matches = [p for p in matches if p.is_file()]
        if matches:
            return matches
        if dir_path.parent == dir_path:
            break
        dir_path = dir_path.parent
    return []


def default_whisper_root() -> pathlib.Path | None:
    value = os.environ.get("WHISPER_ROOT")
    if value:
        return pathlib.Path(value)
    course = os.environ.get("COURSE_ROOT")
    if course:
        candidate = pathlib.Path(course) / "whisper"
        if candidate.is_dir():
            return candidate
    cwd = pathlib.Path.cwd()
    if (cwd / "txt").is_dir() and (cwd / "vtt").is_dir():
        return cwd
    if (cwd / "whisper").is_dir():
        return cwd / "whisper"
    return None


def find_replacements_files(whisper_root: pathlib.Path | None) -> list[pathlib.Path]:
    starts: list[pathlib.Path] = []
    if whisper_root is not None:
        starts.append(whisper_root)
    course = os.environ.get("COURSE_ROOT")
    if course:
        starts.append(pathlib.Path(course))
    starts.append(pathlib.Path.cwd())
    home = pathlib.Path.home()

    seen: set[pathlib.Path] = set()
    for start in starts:
        resolved = start.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        matches = find_matching_files_upward("*whisper-replacements*", resolved)
        if matches:
            return matches

    home_matches = sorted(home.glob("*whisper-replacements*"))
    return [p for p in home_matches if p.is_file()]


def default_transcript_files(whisper_root: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for sub, pattern in (("txt", "*.txt"), ("vtt", "*.vtt"), ("srt", "*.srt")):
        root = whisper_root / sub
        if root.is_dir():
            files.extend(sorted(p for p in root.rglob(pattern) if p.is_file()))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply *whisper-replacements* rules to transcripts. "
            "With no args (and media.env sourced), cleans $WHISPER_ROOT/{txt,vtt,srt}."
        )
    )

    parser.add_argument(
        "--replacements",
        action="append",
        default=None,
        help=(
            "Replacements file (repeatable). Default: discover *whisper-replacements* "
            "under $WHISPER_ROOT / $COURSE_ROOT / cwd."
        ),
    )

    parser.add_argument(
        "--backup",
        action="store_true",
        help="Keep a .raw backup of the original file before cleanup.",
    )

    parser.add_argument(
        "files",
        nargs="*",
        help="Transcript files to clean. Default: all under $WHISPER_ROOT/{txt,vtt,srt}.",
    )

    args = parser.parse_args()
    whisper_root = default_whisper_root()

    if args.replacements:
        replacement_paths = [pathlib.Path(p) for p in args.replacements]
    else:
        replacement_paths = find_replacements_files(whisper_root)

    if not replacement_paths:
        print(
            "Error: no *whisper-replacements* file found. "
            "Pass --replacements PATH or add one under $WHISPER_ROOT.",
            file=sys.stderr,
        )
        return 1

    for path in replacement_paths:
        if not path.is_file():
            print(f"Error: replacements file not found: {path}", file=sys.stderr)
            return 1

    if args.files:
        files = [pathlib.Path(p) for p in args.files]
    else:
        if whisper_root is None:
            print(
                "Error: no transcript files given and WHISPER_ROOT is not set. "
                "Source media.env or pass files explicitly.",
                file=sys.stderr,
            )
            return 1
        files = default_transcript_files(whisper_root)
        if not files:
            print(f"Error: no transcripts found under {whisper_root}/{{txt,vtt,srt}}", file=sys.stderr)
            return 1

    print("Replacements:")
    for path in replacement_paths:
        print(f"  {path}")
    print(f"Files: {len(files)}")

    replacements = merge_replacements(replacement_paths)
    if not replacements:
        print("Warning: no replacement rules loaded (empty or comment-only files).", file=sys.stderr)

    for filename in files:
        cleanup_file(
            pathlib.Path(filename),
            replacements,
            make_backup=args.backup,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
