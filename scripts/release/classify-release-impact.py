#!/usr/bin/env python3
"""Classify whether a ChannelWatch release requires a new container image."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import PurePosixPath
from typing import NamedTuple


class ReleaseImpact(NamedTuple):
    image_required: bool
    triggering_paths: tuple[str, ...]


class ReleaseImpactMismatch(RuntimeError):
    pass


EXACT_RUNTIME_PATHS = {
    "app/core/docker-entrypoint.py",
    "app/core/runtime_launcher.py",
    "app/core/update_center.py",
    "app/ui/pnpm-lock.yaml",
    "app/ui/pnpm-workspace.yaml",
    "deploy/docker/supervisord.conf.template",
}

RUNTIME_PREFIXES = (
    "deploy/compose/",
    "deploy/helm/channelwatch/templates/",
    "deploy/requirements/",
    "deploy/unraid/",
)

STRUCTURED_RELEASE_PATHS = {
    "app/ui/package.json",
    "deploy/helm/channelwatch/Chart.yaml",
    "deploy/helm/channelwatch/values.yaml",
    "deploy/docker/Dockerfile",
}

BUNDLE_RUNTIME_TOOLING = {
    "scripts/release/build-update-bundle.py",
}


def normalize_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix().lstrip("./")


def requires_image(path: str) -> bool:
    normalized = normalize_path(path)
    return (
        normalized in EXACT_RUNTIME_PATHS
        or normalized in BUNDLE_RUNTIME_TOOLING
        or normalized.startswith(RUNTIME_PREFIXES)
    )


def classify_paths(paths: list[str]) -> ReleaseImpact:
    triggering = tuple(sorted({normalize_path(path) for path in paths if requires_image(path)}))
    return ReleaseImpact(bool(triggering), triggering)


def _normalized_package_runtime(content: str | None) -> object:
    if content is None:
        return None
    parsed = json.loads(content)
    return {
        key: parsed.get(key)
        for key in ("dependencies", "devDependencies", "peerDependencies", "engines", "packageManager")
    }


def _without_release_version_lines(content: str | None, *, values: bool = False) -> str | None:
    if content is None:
        return None
    ignored = ("version:", "appVersion:") if not values else ("tag:",)
    return "\n".join(
        line for line in content.splitlines() if not line.strip().startswith(ignored)
    )


def _without_docker_version_default(content: str | None) -> str | None:
    if content is None:
        return None
    return "\n".join(
        "ARG VERSION=<release-version>" if line.strip().startswith("ARG VERSION=") else line
        for line in content.splitlines()
    )


def classify_changes(before: dict[str, str | None], after: dict[str, str | None]) -> ReleaseImpact:
    paths = sorted(set(before) | set(after))
    triggering = set(classify_paths(paths).triggering_paths)
    for path in paths:
        normalized = normalize_path(path)
        if normalized == "app/ui/package.json":
            if _normalized_package_runtime(before.get(path)) != _normalized_package_runtime(after.get(path)):
                triggering.add(normalized)
        elif normalized == "deploy/helm/channelwatch/Chart.yaml":
            if _without_release_version_lines(before.get(path)) != _without_release_version_lines(after.get(path)):
                triggering.add(normalized)
        elif normalized == "deploy/helm/channelwatch/values.yaml":
            if _without_release_version_lines(before.get(path), values=True) != _without_release_version_lines(after.get(path), values=True):
                triggering.add(normalized)
        elif normalized == "deploy/docker/Dockerfile":
            if _without_docker_version_default(before.get(path)) != _without_docker_version_default(after.get(path)):
                triggering.add(normalized)
    ordered = tuple(sorted(triggering))
    return ReleaseImpact(bool(ordered), ordered)


def verify_declared_impact(result: ReleaseImpact, *, declared_image_required: bool) -> None:
    if result.image_required == declared_image_required:
        return
    declared = str(declared_image_required).lower()
    required = str(result.image_required).lower()
    details = ", ".join(result.triggering_paths) or "no image-runtime paths changed"
    raise ReleaseImpactMismatch(
        f"release-config declares image_required={declared}, but classifier requires {required}: {details}"
    )


def changed_paths(base_ref: str, target_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}..{target_ref}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def file_at_ref(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--config", default="scripts/release/release-config.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = json.loads(open(args.config, encoding="utf-8").read())
    declared = config.get("image_required")
    if not isinstance(declared, bool):
        raise ReleaseImpactMismatch("release-config image_required must be a boolean")
    paths = changed_paths(args.base_ref, args.target_ref)
    structured = [path for path in paths if normalize_path(path) in STRUCTURED_RELEASE_PATHS]
    before = {path: file_at_ref(args.base_ref, path) for path in structured}
    after = {path: file_at_ref(args.target_ref, path) for path in structured}
    result = classify_changes(before, after)
    ordinary = classify_paths([path for path in paths if path not in structured])
    merged = tuple(sorted(set(result.triggering_paths) | set(ordinary.triggering_paths)))
    result = ReleaseImpact(bool(merged), merged)
    verify_declared_impact(result, declared_image_required=declared)
    print(json.dumps(result._asdict(), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseImpactMismatch, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"release impact check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
