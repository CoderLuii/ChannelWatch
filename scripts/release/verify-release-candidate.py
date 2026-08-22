#!/usr/bin/env python3
"""Reject stale or divergent release tags before publication mutates channels."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable


SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _version(tag: str) -> tuple[int, int, int]:
    match = SEMVER_TAG.fullmatch(tag.strip())
    if match is None:
        raise ValueError(f"Release tag must use vX.Y.Z format, got {tag!r}.")
    return tuple(int(part) for part in match.groups())


def validate_candidate(
    candidate: str,
    tags: Iterable[str],
    *,
    candidate_sha: str | None = None,
    main_sha: str | None = None,
) -> str:
    candidate = candidate.strip()
    candidate_version = _version(candidate)
    releases = [tag.strip() for tag in tags if SEMVER_TAG.fullmatch(tag.strip())]
    if candidate not in releases:
        raise ValueError(
            f"Release candidate {candidate} is not present in the fetched tag set."
        )
    latest = max(releases, key=_version)
    if _version(latest) > candidate_version:
        raise ValueError(
            f"Refusing stale release {candidate}: newer release tag {latest} already exists."
        )
    if candidate_sha is not None and main_sha is not None and candidate_sha != main_sha:
        raise ValueError(
            f"Refusing release {candidate}: tagged commit {candidate_sha} "
            f"is not current main {main_sha}."
        )
    return latest


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--sha",
        help="Optional event SHA; it must dereference to the candidate tag commit.",
    )
    parser.add_argument("--main-ref", default="origin/main")
    args = parser.parse_args()

    try:
        _version(args.tag)
        tags = _git("tag", "--list").splitlines()
        candidate_commit = _git("rev-parse", "--verify", f"{args.tag}^{{commit}}")
        main_commit = _git("rev-parse", "--verify", f"{args.main_ref}^{{commit}}")
        if args.sha:
            event_commit = _git("rev-parse", "--verify", f"{args.sha}^{{commit}}")
            if event_commit != candidate_commit:
                raise ValueError(
                    f"Release event SHA {args.sha} does not identify tag {args.tag}."
                )
        latest = validate_candidate(
            args.tag,
            tags,
            candidate_sha=candidate_commit,
            main_sha=main_commit,
        )
    except (ValueError, subprocess.CalledProcessError, OSError) as exc:
        print(f"release candidate verification failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Release candidate {args.tag} is current main "
        f"(latest release tag: {latest})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
