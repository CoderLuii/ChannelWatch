"""Validate ChannelWatch release numbering and delivery policy.

Starting with v1.0.0, every minor line has one image milestone followed by at
most nine in-app releases. Historical v0.9 releases are grandfathered because
their already-published identifiers cannot be rewritten.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import NamedTuple


class ReleaseVersionPolicy(NamedTuple):
    version: str
    image_milestone: bool
    minimum_image_version: str


def release_version_policy(version: str) -> ReleaseVersionPolicy | None:
    normalized = str(version or "").strip().lstrip("v")
    parts = normalized.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Release version must use numeric X.Y.Z format.")
    major, minor, patch = (int(part) for part in parts)
    canonical = f"{major}.{minor}.{patch}"
    if canonical != normalized:
        raise ValueError("Release version must use canonical numeric X.Y.Z format.")
    if major < 1:
        return None
    if patch > 9:
        raise ValueError(
            "ChannelWatch patch versions stop at 9; advance to the next X.Y.0 image milestone."
        )
    return ReleaseVersionPolicy(
        version=canonical,
        image_milestone=patch == 0,
        minimum_image_version=f"{major}.{minor}.0",
    )


def validate_release_config(config: dict[str, object]) -> ReleaseVersionPolicy | None:
    policy = release_version_policy(str(config.get("version") or ""))
    if policy is None:
        return None

    delivery_mode = str(config.get("delivery_mode") or "")
    image_required = config.get("image_required")
    minimum_image_version = str(config.get("minimum_image_version") or "").lstrip("v")
    if minimum_image_version != policy.minimum_image_version:
        raise ValueError(
            "release-config minimum_image_version must equal the current minor-line "
            f"image milestone ({policy.minimum_image_version})."
        )
    if policy.image_milestone:
        if image_required is not True or delivery_mode != "image_required":
            raise ValueError(
                "X.Y.0 releases must require the matching ChannelWatch container image."
            )
        if config.get("automatic_install_allowed") is not False:
            raise ValueError(
                "X.Y.0 image milestones cannot allow automatic in-app installation."
            )
    elif image_required is not False or delivery_mode == "image_required":
        raise ValueError(
            "X.Y.1 through X.Y.9 releases must remain in-app updates."
        )
    return policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate ChannelWatch release numbering and delivery policy."
    )
    parser.add_argument(
        "config",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parent / "release-config.json",
    )
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Release configuration must contain a JSON object.")
    policy = validate_release_config(config)
    if policy is None:
        print("Historical pre-v1 release policy accepted.")
    else:
        delivery = "container image" if policy.image_milestone else "in-app"
        print(
            f"v{policy.version}: {delivery}; minimum image "
            f"v{policy.minimum_image_version}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
