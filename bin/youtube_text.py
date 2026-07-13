#!/usr/bin/env python3
"""Sanitize text for YouTube titles, tags, and descriptions."""

from __future__ import annotations

import re

# Match HTML-like tags: <html>, </body>, <a href="...">, etc.
_HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")


def sanitize_youtube_text(text: str) -> str:
    """Remove HTML-like markup YouTube rejects in titles/descriptions.

    Turns ``<html>`` / ``</body>`` into backtick code spans (`` `html` ``) and
    replaces any remaining ``<`` / ``>`` with Unicode angle brackets.
    """
    if not text:
        return text

    def repl(match: re.Match[str]) -> str:
        return f"`{match.group(1)}`"

    cleaned = _HTML_TAG_RE.sub(repl, text)
    # YouTube invalidDescription often trips on leftover angle brackets.
    cleaned = cleaned.replace("<", "‹").replace(">", "›")
    return cleaned


def sanitize_youtube_tags(text: str) -> str:
    """Sanitize a comma-separated tag string (no HTML / angle brackets / backticks)."""
    if not text:
        return text

    def repl(match: re.Match[str]) -> str:
        return match.group(1)

    cleaned = _HTML_TAG_RE.sub(repl, text)
    cleaned = cleaned.replace("<", "").replace(">", "")
    cleaned = cleaned.replace("`", "")
    # Keep comma-separated form tidy.
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    return ", ".join(parts)
