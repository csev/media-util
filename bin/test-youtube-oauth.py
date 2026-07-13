#!/usr/bin/env python3
"""Smoke-test YouTube OAuth credentials (client_secret + API access).

Checks that:
  1. client_secret.json exists and looks valid
  2. OAuth consent works (browser on first run)
  3. YouTube Data API accepts a simple authenticated call

Usage:
  source media.env
  test-youtube-oauth.py

Credentials:
  $YOUTUBE_CLIENT_SECRETS  or  ~/.ssh/youtube_client_secret.json
  token: $YOUTUBE_TOKEN    or  $YOUTUBE_DIR/youtube-oauth-token.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# Same scope as update-youtube-from-media.py so one consent covers both tools.
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def env_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value.strip())
    return None


def default_youtube_dir() -> Path:
    path = env_path("YOUTUBE_DIR")
    if path:
        return path
    course = env_path("COURSE_ROOT")
    if course:
        return course / "youtube"
    return Path.cwd() / "youtube"


def default_client_secrets() -> Path:
    path = env_path("YOUTUBE_CLIENT_SECRETS")
    if path:
        return path
    return Path.home() / ".ssh" / "youtube_client_secret.json"


def default_token_path() -> Path:
    path = env_path("YOUTUBE_TOKEN")
    if path:
        return path
    return default_youtube_dir() / "youtube-oauth-token.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify YouTube OAuth client_secret.json with a read-only API call."
    )
    parser.add_argument(
        "--client-secrets",
        type=Path,
        default=None,
        help="OAuth client secrets JSON (default: ~/.ssh/youtube_client_secret.json)",
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=None,
        help="OAuth token cache path (default: $YOUTUBE_DIR/youtube-oauth-token.json)",
    )
    parser.add_argument(
        "--video",
        default="oxJQB4f2MMs",
        help="Optional public video id to fetch (default: DJ4E welcome)",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Ignore cached token and force a new browser consent",
    )
    return parser.parse_args()


def validate_client_secrets(path: Path) -> dict:
    if not path.is_file():
        fail(
            f"OAuth client secrets not found: {path}\n"
            "Download a Desktop OAuth client JSON from Google Cloud Console "
            "(YouTube Data API v3 enabled) and save it there."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot parse client secrets {path}: {exc}")

    if not isinstance(data, dict):
        fail(f"{path} is not a JSON object")

    if "installed" in data:
        kind = "installed"
        block = data["installed"]
    elif "web" in data:
        kind = "web"
        block = data["web"]
        print(
            "WARNING: this is a Web OAuth client, not Desktop (installed).\n"
            "         Local browser login may fail. Prefer a Desktop app client."
        )
    else:
        fail(
            f"{path} has neither 'installed' nor 'web' keys. "
            "Expected a Google OAuth client JSON."
        )

    if not isinstance(block, dict) or not block.get("client_id"):
        fail(f"{path}: missing client_id under '{kind}'")

    print(f"OK client secrets: {path}")
    print(f"   type: {kind}")
    print(f"   client_id: {block['client_id']}")
    return data


def build_youtube(client_secrets: Path, token_path: Path, *, reauth: bool):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        fail(
            "missing Google API packages. Install with:\n"
            "  pip3 install -r /Users/csev/htdocs/media-util/requirements.txt\n"
            f"({exc})"
        )

    if reauth and token_path.is_file():
        token_path.unlink()
        print(f"Removed cached token: {token_path}")

    creds = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing OAuth token…")
            creds.refresh(Request())
        else:
            print("Opening browser for Google OAuth consent…")
            # InstalledAppFlow expects Desktop ("installed"). If the file is
            # "web"-only, rewrite a temp copy as installed so local testing works.
            secrets_path = client_secrets
            raw = json.loads(client_secrets.read_text(encoding="utf-8"))
            if "installed" not in raw and "web" in raw:
                tmp = client_secrets.with_name(client_secrets.name + ".installed-tmp")
                tmp.write_text(
                    json.dumps({"installed": raw["web"]}, indent=2) + "\n",
                    encoding="utf-8",
                )
                secrets_path = tmp
                print(f"NOTE: using temporary Desktop-shaped copy: {tmp}")
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"OK saved token: {token_path}")
    else:
        print(f"OK using cached token: {token_path}")

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def main() -> int:
    args = parse_args()
    client_secrets = args.client_secrets or default_client_secrets()
    token_path = args.token or default_token_path()

    print("=== YouTube OAuth smoke test ===")
    validate_client_secrets(client_secrets)

    youtube = build_youtube(client_secrets, token_path, reauth=args.reauth)

    print("Calling channels.list(mine=True)…")
    try:
        response = (
            youtube.channels()
            .list(part="snippet,contentDetails", mine=True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        fail(f"YouTube API call failed: {exc}")

    items = response.get("items") or []
    if not items:
        fail(
            "API call succeeded but returned no channels for this account.\n"
            "Make sure you signed in with the Google account that owns the channel."
        )

    channel = items[0]
    snippet = channel.get("snippet") or {}
    print("OK authenticated channel:")
    print(f"   id:    {channel.get('id')}")
    print(f"   title: {snippet.get('title')}")

    if args.video:
        print(f"Calling videos.list(id={args.video})…")
        try:
            vresp = (
                youtube.videos()
                .list(part="snippet", id=args.video)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"videos.list failed: {exc}")
        vitems = vresp.get("items") or []
        if not vitems:
            print(f"WARNING: video not found or not visible: {args.video}")
        else:
            title = (vitems[0].get("snippet") or {}).get("title")
            print(f"OK sample video title: {title}")

    print("=== SUCCESS: OAuth client works ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
