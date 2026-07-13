#!/usr/bin/env python3
"""Shared helpers for media-util compare scripts."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


CWD = Path.cwd()
SCRIPT_DIR = Path(__file__).resolve().parent


def load_bootstrap():
    """Import bootstrap-media-yaml.py as a module (hyphenated filename)."""
    path = SCRIPT_DIR / "bootstrap-media-yaml.py"
    spec = importlib.util.spec_from_file_location("bootstrap_media_yaml", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Error: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_media_root() -> Path:
    value = os.environ.get("MEDIA_ROOT")
    if not value:
        raise SystemExit("Error: set MEDIA_ROOT (source media.env) or pass --media-root")
    return Path(value)


def default_media_yaml() -> Path:
    value = os.environ.get("MEDIA_YAML")
    if value:
        return Path(value)
    course_root = os.environ.get("COURSE_ROOT")
    if course_root:
        return Path(course_root) / "media.yaml"
    return CWD / "media.yaml"


def default_lessons() -> Path:
    value = os.environ.get("LESSONS_JSON")
    if value:
        return Path(value)
    course_root = os.environ.get("COURSE_ROOT")
    if course_root:
        return Path(course_root) / "lessons.json"
    return CWD / "lessons.json"


def default_youtube_jsonl() -> Path:
    value = os.environ.get("YOUTUBE_PLAYLIST_JSONL")
    if value:
        return Path(value)
    youtube_dir = os.environ.get("YOUTUBE_DIR")
    if youtube_dir:
        return Path(youtube_dir) / "youtube-playlist.jsonl"
    return CWD / "youtube" / "youtube-playlist.jsonl"


def load_media_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Error: media.yaml not found: {path}")
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Error: cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or "entries" not in data:
        raise SystemExit(f"Error: {path} must contain an 'entries' mapping")
    entries = data["entries"]
    if entries is None:
        entries = {}
    if not isinstance(entries, dict):
        raise SystemExit(f"Error: {path} 'entries' must be a mapping")
    return data


def entry_keys(data: dict[str, Any]) -> list[str]:
    return list(data["entries"].keys())


def section(title: str, items: list[str]) -> int:
    """Print a section; return item count."""
    print(f"\n{title} ({len(items)})")
    if not items:
        print("  (none)")
        return 0
    for item in items:
        print(f"  {item}")
    return len(items)


def summary_and_exit(problems: int) -> int:
    print()
    if problems:
        print(f"INCONSISTENT: {problems} issue(s)")
        return 1
    print("OK: no inconsistencies found")
    return 0
