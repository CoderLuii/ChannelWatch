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


def validate_single_commit_release(
    candidate_tag: str,
    previous_tag: str,
    *,
    commit_count: int,
    commit_message: str,
    tag_object_type: str,
) -> None:
    """Enforce the repository's one-commit, lightweight release contract."""

    _version(candidate_tag)
    _version(previous_tag)
    if commit_count != 1:
        raise ValueError(
            f"Release {candidate_tag} must contain exactly one commit after "
            f"{previous_tag}; found {commit_count}."
        )
    if commit_message.strip() != candidate_tag or "\n" in commit_message.strip():
        raise ValueError(
            f"Release commit message must be exactly {candidate_tag} with no body."
        )
    if tag_object_type.strip() != "commit":
        raise ValueError(f"Release tag {candidate_tag} must be lightweight.")


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
        previous_tags = [
            tag
            for tag in tags
            if SEMVER_TAG.fullmatch(tag.strip()) and _version(tag) < _version(args.tag)
        ]
        if not previous_tags:
            raise ValueError(
                f"Release {args.tag} requires a previous semantic-version tag."
            )
        previous_tag = max(previous_tags, key=_version)
        validate_single_commit_release(
            args.tag,
            previous_tag,
            commit_count=int(
                _git("rev-list", "--count", f"{previous_tag}..{candidate_commit}")
            ),
            commit_message=_git("log", "-1", "--format=%B", candidate_commit),
            tag_object_type=_git("cat-file", "-t", args.tag),
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
