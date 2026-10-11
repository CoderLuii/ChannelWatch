"""Validate ChannelWatch release numbering and delivery policy.

Starting with v1.0.0, each minor line defaults to one image milestone followed
by at most nine in-app releases. A failed, unpublished image milestone may be
recovered by a later patch in the same line. Prospective releases may retain a
proven protocol-4 image floor at v1.5.0 or newer. Historical declarations keep
their original rules because published identifiers cannot be rewritten.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class ReleaseVersionPolicy(NamedTuple):
    version: str
    image_milestone: bool
    minimum_image_version: str
    retained_capability_floor: bool = False


def _canonical_version_parts(version: str, *, label: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"{label} must use numeric X.Y.Z format.")
    parsed = tuple(int(part) for part in parts)
    if version != ".".join(str(part) for part in parsed):
        raise ValueError(f"{label} must use canonical numeric X.Y.Z format.")
    return parsed


def _unpublished_release_tags(config: dict[str, object]) -> list[str]:
    raw_tags = config.get("unpublished_release_tags", [])
    if not isinstance(raw_tags, list) or any(
        not isinstance(tag, str) or not tag.startswith("v") for tag in raw_tags
    ):
        raise ValueError(
            "release-config unpublished_release_tags must be a list of version tags."
        )
    if len(raw_tags) != len(set(raw_tags)):
        raise ValueError(
            "release-config unpublished_release_tags cannot contain duplicates."
        )
    for tag in raw_tags:
        _canonical_version_parts(
            tag.removeprefix("v"),
            label="release-config unpublished release tags",
        )
    return raw_tags


def release_version_policy(version: str) -> ReleaseVersionPolicy | None:
    normalized = str(version or "").strip().lstrip("v")
    major, minor, patch = _canonical_version_parts(
        normalized,
        label="Release version",
    )
    canonical = f"{major}.{minor}.{patch}"
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
    version_parts = _canonical_version_parts(
        policy.version,
        label="Release version",
    )
    recovery_version = config.get("recovers_unpublished_image_milestone")
    if recovery_version is not None:
        if not isinstance(recovery_version, str):
            raise ValueError(
                "recovers_unpublished_image_milestone must use canonical numeric "
                "X.Y.Z format."
            )
        recovery_parts = _canonical_version_parts(
            recovery_version,
            label="recovers_unpublished_image_milestone",
        )
        if recovery_parts[2] != 0:
            raise ValueError(
                "recovers_unpublished_image_milestone must name an X.Y.0 milestone."
            )
        if recovery_parts[:2] != version_parts[:2]:
            raise ValueError(
                "An image-milestone recovery must stay in the same minor release line."
            )
        if recovery_parts >= version_parts:
            raise ValueError(
                "An image-milestone recovery must follow the unpublished milestone."
            )
        if minimum_image_version != policy.version or config.get(
            "recommended_image_version"
        ) != policy.version:
            raise ValueError(
                "An image-milestone recovery must declare the recovery as its matching "
                "image floor and recommended image."
            )
        expected_predecessor_tags = [
            f"v{version_parts[0]}.{version_parts[1]}.{patch}"
            for patch in range(version_parts[2] - 1, recovery_parts[2] - 1, -1)
        ]
        if config.get("unpublished_predecessor_tags") != expected_predecessor_tags:
            raise ValueError(
                "An image-milestone recovery must name the exact contiguous unpublished "
                "predecessors in descending order."
            )
        unpublished_release_tags = _unpublished_release_tags(config)
        if any(
            tag not in unpublished_release_tags for tag in expected_predecessor_tags
        ):
            raise ValueError(
                "Every recovered predecessor must remain excluded from public release "
                "history."
            )
        policy = ReleaseVersionPolicy(
            version=policy.version,
            image_milestone=True,
            minimum_image_version=policy.version,
        )
    elif minimum_image_version != policy.minimum_image_version:
        floor_parts = _canonical_version_parts(
            minimum_image_version,
            label="release-config minimum_image_version",
        )
        if floor_parts < (1, 5, 0):
            raise ValueError(
                "A retained dependency-runtime image floor must be v1.5.0 or newer."
            )
        if floor_parts >= version_parts:
            raise ValueError(
                "A retained dependency-runtime image floor must be strictly older than "
                "the release."
            )
        if floor_parts[0] != version_parts[0]:
            raise ValueError(
                "A retained dependency-runtime image floor must stay in the same major "
                "release line."
            )
        if (
            config.get("runtime_generation_required") is not True
            or config.get("minimum_launcher_protocol") != 4
            or config.get("compatible_launcher_protocols") != [4]
        ):
            raise ValueError(
                "A retained image floor requires the protocol-4 runtime generation "
                "contract."
            )
        bootstrap_sha256 = config.get("runtime_bootstrap_sha256")
        if (
            not isinstance(bootstrap_sha256, str)
            or len(bootstrap_sha256) != 64
            or any(char not in "0123456789abcdef" for char in bootstrap_sha256)
        ):
            raise ValueError(
                "A retained image floor requires the exact immutable protocol-4 "
                "bootstrap SHA-256."
            )
        if image_required is not False or delivery_mode == "image_required":
            raise ValueError(
                "A retained protocol-4 image floor is valid only for an in-app release."
            )
        policy = ReleaseVersionPolicy(
            version=policy.version,
            image_milestone=False,
            minimum_image_version=minimum_image_version,
            retained_capability_floor=True,
        )
    if version_parts >= (1, 5, 0) and not policy.image_milestone:
        if f"v{policy.minimum_image_version}" in _unpublished_release_tags(config):
            raise ValueError(
                "A retained dependency-runtime image floor must name a published image "
                "foundation."
            )
        if (
            config.get("updater_protocol") != 4
            or config.get("runtime_generation_required") is not True
            or config.get("minimum_launcher_protocol") != 4
            or config.get("compatible_launcher_protocols") != [4]
        ):
            raise ValueError(
                "Post-bootstrap in-app releases require the protocol-4 runtime "
                "generation contract, including unchanged dependencies."
            )
        bootstrap_sha256 = config.get("runtime_bootstrap_sha256")
        if (
            not isinstance(bootstrap_sha256, str)
            or len(bootstrap_sha256) != 64
            or any(char not in "0123456789abcdef" for char in bootstrap_sha256)
        ):
            raise ValueError(
                "Post-bootstrap in-app releases require the exact immutable "
                "protocol-4 bootstrap SHA-256."
            )
        if config.get("minimum_image_sha256") != f"sha256:{bootstrap_sha256}":
            raise ValueError(
                "The protocol-4 bootstrap SHA-256 must match the immutable "
                "minimum image index digest."
            )
    if policy.image_milestone:
        if image_required is not True or delivery_mode != "image_required":
            release_kind = (
                "Image-milestone recovery releases"
                if recovery_version is not None
                else "X.Y.0 releases"
            )
            raise ValueError(
                f"{release_kind} must require the matching ChannelWatch container image."
            )
        if version_parts >= (1, 5, 0):
            if (
                config.get("updater_protocol") != 4
                or config.get("minimum_launcher_protocol") != 4
                or config.get("compatible_launcher_protocols") != [4]
                or config.get("runtime_generation_required") is not False
            ):
                raise ValueError(
                    "A v1.5.0 or newer protocol-4 image milestone requires the "
                    "exact image bootstrap launcher contract."
                )
            if any(
                field in config
                for field in (
                    "runtime_bootstrap_sha256",
                    "runtime_variants",
                    "runtime_descriptor",
                    "minimum_image_sha256",
                )
            ) or config.get("runtime_compatibility_evidence", {}) != {}:
                raise ValueError(
                    "A v1.5.0 or newer protocol-4 image milestone cannot declare "
                    "runtime generation fields."
                )
        if config.get("automatic_install_allowed") is not False:
            raise ValueError(
                "Image foundation releases cannot allow automatic in-app installation."
            )
    elif image_required is not False or delivery_mode == "image_required":
        raise ValueError(
            "X.Y.1 through X.Y.9 releases must remain in-app updates."
        )
    return policy


def verify_release_source(root: Path, version: str) -> None:
    source = (root / "app" / "core" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    source_version = match.group(1) if match else ""
    if source_version != version:
        raise ValueError(
            "Release configuration and app/core/__init__.py disagree: "
            f"{version!r} != {source_version!r}."
        )

    document = root / "deploy" / "security" / f"channelwatch-v{version}.openvex.json"
    if not document.is_file():
        raise ValueError(f"Release VEX document is missing: {document.relative_to(root)}")
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "release" / "verify-vex.py"),
            "--document",
            str(document),
            "--expected-version",
            version,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "verification failed"
        raise ValueError(f"Release VEX verification failed: {detail}")


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
    version = str(config.get("version") or "").strip().lstrip("v")
    verify_release_source(Path(__file__).resolve().parents[2], version)
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
