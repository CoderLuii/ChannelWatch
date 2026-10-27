#!/usr/bin/env python3
"""Check release dates before preparing an exact candidate."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def parse_utc(value: Any, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def verify_release_timing(
    config: dict[str, Any],
    vex: dict[str, Any],
    changelog: str,
    *,
    candidate_created_at: datetime,
) -> None:
    if candidate_created_at.tzinfo is None:
        raise ValueError("candidate creation must include a timezone.")
    publication = parse_utc(config.get("publication_time"), name="publication_time")
    automatic = parse_utc(
        config.get("automatic_install_after"), name="automatic_install_after"
    )
    if publication < candidate_created_at:
        raise ValueError(
            "publication_time precedes candidate creation; prepare a new publication "
            "window before creating the final release commit."
        )
    if automatic - publication != timedelta(days=7):
        raise ValueError("Preserve the selected seven-day automatic-install delay.")
    if config.get("automatic_install_allowed") is not False:
        raise ValueError("Automatic installation must remain disabled.")
    version = str(config.get("version") or "")
    if re.fullmatch(rf"# ChannelWatch v{re.escape(version)} - [^\r\n]+", str(config.get("release_heading") or "")) is None:
        raise ValueError("The release heading must name the exact ChannelWatch version and update title.")
    if parse_utc(vex.get("timestamp"), name="OpenVEX timestamp") != publication:
        raise ValueError("The OpenVEX timestamp must match publication_time.")
    version = str(config.get("version") or "")
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError("The release version must use X.Y.Z format.")
    dates = re.findall(
        rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})\s*$",
        changelog, re.MULTILINE,
    )
    if dates != [publication.date().isoformat()]:
        raise ValueError("The changelog must contain one matching UTC publication date.")


def prepare_release_timing(
    config: dict[str, Any], vex: dict[str, Any], changelog: str, *, publication: datetime
) -> tuple[dict[str, Any], dict[str, Any], str]:
    config = dict(config)
    vex = dict(vex)
    version = re.escape(str(config.get("version") or ""))
    heading = f"## [{config['version']}] - {publication.date().isoformat()}"
    changelog, count = re.subn(
        rf"^## \[{version}\] - \d{{4}}-\d{{2}}-\d{{2}}[ \t]*$",
        heading, changelog, flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError("The changelog must contain one release heading to prepare.")
    config["publication_time"] = publication.isoformat().replace("+00:00", "Z")
    config["automatic_install_after"] = (
        (publication + timedelta(days=7)).isoformat().replace("+00:00", "Z")
    )
    vex["timestamp"] = config["publication_time"]
    return config, vex, changelog


def _write_atomic(path: Path, content: str) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    creation = parser.add_mutually_exclusive_group()
    creation.add_argument(
        "--candidate-created-at",
        help="Prospective commit creation time; defaults to the current UTC time.",
    )
    creation.add_argument("--source-ref", help="Use this existing commit's real timestamp.")
    parser.add_argument(
        "--prepare-publication-time",
        help="Explicit UTC publication anchor; preview synchronized dates without writing.",
    )
    parser.add_argument("--write", action="store_true", help="Write the prepared dates locally.")
    args = parser.parse_args(argv)
    if args.write and not args.prepare_publication_time:
        parser.error("--write requires --prepare-publication-time.")
    try:
        root = args.root.resolve()
        config = json.loads(
            (root / "scripts/release/release-config.json").read_text(encoding="utf-8")
        )
        if not isinstance(config, dict):
            raise ValueError("Release configuration must be a JSON object.")
        version = str(config.get("version") or "")
        if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
            raise ValueError("The release version must use X.Y.Z format.")
        vex = json.loads(
            (root / f"deploy/security/channelwatch-v{version}.openvex.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(vex, dict):
            raise ValueError("OpenVEX must be a JSON object.")
        changelog = (root / "docs/releases/CHANGELOG.md").read_text(encoding="utf-8")
        if args.source_ref:
            sha = subprocess.run(
                ["git", "rev-parse", "--verify", f"{args.source_ref}^{{commit}}"],
                cwd=root, capture_output=True, text=True, check=True,
            ).stdout.strip()
            epoch = subprocess.run(
                ["git", "show", "-s", "--format=%ct", sha, "--"],
                cwd=root, capture_output=True, text=True, check=True,
            ).stdout.strip()
            created = datetime.fromtimestamp(int(epoch), timezone.utc)
        else:
            created = (
                parse_utc(args.candidate_created_at, name="candidate creation")
                if args.candidate_created_at else datetime.now(timezone.utc)
            )
        if args.prepare_publication_time:
            config, vex, changelog = prepare_release_timing(
                config, vex, changelog,
                publication=parse_utc(args.prepare_publication_time, name="publication_time"),
            )
        verify_release_timing(config, vex, changelog, candidate_created_at=created)
        if args.write:
            _write_atomic(
                root / "scripts/release/release-config.json",
                json.dumps(config, indent=2) + "\n",
            )
            _write_atomic(
                root / f"deploy/security/channelwatch-v{version}.openvex.json",
                json.dumps(vex, indent=2) + "\n",
            )
            _write_atomic(root / "docs/releases/CHANGELOG.md", changelog)
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"release timing verification failed: {exc}", file=sys.stderr)
        return 1
    if args.prepare_publication_time:
        print(json.dumps({
            "version": version,
            "publication_time": config["publication_time"],
            "automatic_install_after": config["automatic_install_after"],
            "automatic_install_allowed": config["automatic_install_allowed"],
            "release_heading": config["release_heading"],
            "openvex_timestamp": vex["timestamp"],
            "changelog_heading": f"## [{version}] - {parse_utc(config['publication_time'], name='publication_time').date()}",
            "written": args.write,
        }, indent=2))
    else:
        print(f"Release timing verified for v{version}; candidate creation {created.isoformat()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
