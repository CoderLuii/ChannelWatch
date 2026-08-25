#!/usr/bin/env python3
"""Fail unless a signed catalog preserves the public manual-review window."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def parse_utc(value: Any, *, name: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def verify_publication_window(
    catalog: dict[str, Any],
    *,
    version: str,
    prospective_publication: datetime,
    minimum_delay: timedelta = timedelta(hours=24),
) -> tuple[datetime, datetime]:
    if minimum_delay < timedelta(hours=24):
        raise ValueError("The public manual-review window cannot be less than 24 hours.")
    if prospective_publication.tzinfo is None:
        raise ValueError("prospective_publication must include a timezone.")
    prospective_publication = prospective_publication.astimezone(timezone.utc)
    if catalog.get("schema") != 2 or not isinstance(catalog.get("payload"), dict):
        raise ValueError("The update catalog must use signed schema 2.")
    releases = catalog["payload"].get("releases")
    if not isinstance(releases, list):
        raise ValueError("The update catalog release list is invalid.")
    normalized_version = version.strip().lstrip("v")
    matching = [
        release
        for release in releases
        if isinstance(release, dict)
        and str(release.get("version") or "").strip().lstrip("v")
        == normalized_version
    ]
    if len(matching) != 1:
        raise ValueError("The catalog must contain exactly one matching release.")
    release = matching[0]
    publication_time = parse_utc(
        release.get("publication_time"), name="publication_time"
    )
    automatic_install_after = parse_utc(
        release.get("automatic_install_after"),
        name="automatic_install_after",
    )
    if automatic_install_after < publication_time + minimum_delay:
        raise ValueError(
            "automatic_install_after is less than 24 hours after the signed "
            "publication time."
        )
    if automatic_install_after < prospective_publication + minimum_delay:
        raise ValueError(
            "automatic_install_after no longer preserves a full 24-hour public "
            "manual-review window. Amend the unpublished candidate and review it again."
        )
    if publication_time > prospective_publication:
        raise ValueError(
            "The signed publication time is still in the future. Publication must wait "
            "or the unpublished candidate must be amended."
        )
    return publication_time, automatic_install_after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--minimum-hours", type=int, default=24)
    parser.add_argument(
        "--prospective-publication-time",
        help="Test-only/current-publication override; defaults to the current UTC time.",
    )
    args = parser.parse_args()
    if args.minimum_hours < 24:
        parser.error("--minimum-hours cannot weaken the 24-hour release policy.")
    try:
        raw = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("The update catalog must be a JSON object.")
        prospective = (
            parse_utc(
                args.prospective_publication_time,
                name="prospective_publication_time",
            )
            if args.prospective_publication_time
            else datetime.now(timezone.utc)
        )
        _publication, automatic = verify_publication_window(
            raw,
            version=args.version,
            prospective_publication=prospective,
            minimum_delay=timedelta(hours=args.minimum_hours),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"publication-window verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Publication window verified for "
        f"v{args.version.strip().lstrip('v')}; automatic installation remains "
        f"disabled until {automatic.isoformat().replace('+00:00', 'Z')}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
