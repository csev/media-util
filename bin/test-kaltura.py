#!/usr/bin/env python3
"""Smoke-test Kaltura admin credentials (session + media.list).

Usage:
  source media.env
  test-kaltura.py

Credentials:
  KALTURA_PARTNER_ID + KALTURA_ADMIN_SECRET
  or secret file ~/.ssh/kaltura_admin_secret (see kaltura_common.py)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kaltura_common as kc  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Kaltura partner id + admin secret with a read-only call."
    )
    parser.add_argument(
        "--secret-file",
        type=Path,
        default=None,
        help="Admin secret file (default: $KALTURA_SECRET_FILE or ~/.ssh/kaltura_admin_secret)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = kc.load_kaltura_config(
        require_secret=True, secret_file=args.secret_file
    )
    print(f"partner_id:  {cfg.partner_id}")
    print(f"service_url: {cfg.service_url}")
    print(f"user_id:     {cfg.user_id}")
    if cfg.category_id:
        print(f"category_id: {cfg.category_id}")
    if cfg.playlist_id:
        print(f"playlist_id: {cfg.playlist_id}")

    client = kc.build_client(cfg)
    api = kc.require_kaltura_client()
    pager = api["KalturaFilterPager"]()
    pager.pageSize = 1
    pager.pageIndex = 1
    result = client.media.list(api["KalturaMediaEntryFilter"](), pager)
    total = getattr(result, "totalCount", None)
    print(f"OK: session started; media.list totalCount={total}")
    objects = getattr(result, "objects", None) or []
    if objects:
        sample = objects[0]
        print(
            f"sample: id={getattr(sample, 'id', None)} "
            f"name={getattr(sample, 'name', None)!r}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
