#!/usr/bin/env python3
"""Verify that an app update serves its own frontend on the minimum image.

This canary deliberately starts the published minimum compatible image before
selecting the candidate bundle. That preserves Supervisor's image-owned
environment and reproduces the real in-app update boundary. A canary that
starts with ``active.json`` already present cannot detect split runtimes.
"""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import secrets
import shutil

# This release canary intentionally invokes fixed Docker argv.
import subprocess  # nosec B404
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

# Immutable generated path inside the disposable ChannelWatch image.
SUPERVISOR_CONFIG = "/tmp/supervisord.conf"  # nosec B108


class CanaryError(RuntimeError):
    """Raised when the minimum-image app-update contract is not satisfied."""


class _StaticAssetParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attribute = "src" if tag == "script" else "href" if tag == "link" else ""
        if not attribute:
            return
        for name, value in attrs:
            if name != attribute or not value:
                continue
            parsed = urllib.parse.urlsplit(value)
            if not parsed.scheme and not parsed.netloc and parsed.path.startswith("/"):
                self.paths.add(parsed.path)


def referenced_static_assets(index_html: bytes) -> list[str]:
    parser = _StaticAssetParser()
    parser.feed(index_html.decode("utf-8"))
    return sorted(parser.paths)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Every caller constructs a fixed argv list and never invokes a shell.
    result = subprocess.run(  # nosec B603
        command, text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise CanaryError(f"{command[0]} failed: {detail[:500]}")
    return result


def docker_exec(name: str, code: str) -> str:
    return run(["docker", "exec", name, "python", "-c", code]).stdout.strip()


def wait_for_json(name: str, path: str, predicate, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    code = (
        "import json,urllib.request; "
        f"print(urllib.request.urlopen('http://127.0.0.1:8501{path}', timeout=3)"
        ".read().decode('utf-8'))"
    )
    while time.monotonic() < deadline:
        try:
            payload = json.loads(docker_exec(name, code))
            if predicate(payload):
                return payload
        except (CanaryError, json.JSONDecodeError, OSError):
            pass
        time.sleep(1)
    raise CanaryError(f"container did not satisfy {path} within {timeout} seconds")


def served_sha256(name: str, path: str) -> str:
    quoted_path = json.dumps(path)
    code = (
        "import hashlib,urllib.request; "
        f"response=urllib.request.urlopen('http://127.0.0.1:8501'+{quoted_path}, timeout=5); "
        "print(response.status, hashlib.sha256(response.read()).hexdigest())"
    )
    output = docker_exec(name, code).split()
    if len(output) != 2 or output[0] != "200":
        raise CanaryError(f"selected frontend asset {path} was not served successfully")
    return output[1]


def restart(name: str, target: str = "all") -> None:
    run(
        [
            "docker",
            "exec",
            name,
            "python",
            "-m",
            "supervisor.supervisorctl",
            "-c",
            SUPERVISOR_CONFIG,
            "restart",
            target,
        ]
    )


def write_active(name: str, version: str) -> None:
    payload = (
        json.dumps(
            {
                "version": version,
                "path": f"/config/channelwatch-runtime/releases/v{version}",
                "runtime_abi": "channelwatch-runtime-v1",
                "settings_schema_version": 7,
                "activation_protocol": 3,
                "activation_id": "minimum-image-static-canary",
            },
            sort_keys=True,
        )
        + "\n"
    )
    code = (
        "from pathlib import Path; "
        "p=Path('/config/channelwatch-runtime/active.json'); "
        f"p.write_text({payload!r}, encoding='utf-8'); "
        "p.chmod(0o644)"
    )
    docker_exec(name, code)


def remove_active(name: str) -> None:
    docker_exec(
        name,
        "from pathlib import Path; "
        "Path('/config/channelwatch-runtime/active.json').unlink()",
    )


def extract_bundle(bundle: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    root = destination.resolve()
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            target = (root / info.filename).resolve()
            if root != target and root not in target.parents:
                raise CanaryError(
                    "bundle extraction path escaped the release directory"
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            target.chmod(0o644)


def verify_frontend(name: str, bundle: Path) -> dict[str, object]:
    with zipfile.ZipFile(bundle) as archive:
        index_member = "ui/backend/static_ui/index.html"
        index = archive.read(index_member)
        expected: dict[str, str] = {"/": hashlib.sha256(index).hexdigest()}
        for path in referenced_static_assets(index):
            member = f"ui/backend/static_ui/{path.lstrip('/')}"
            try:
                data = archive.read(member)
            except KeyError as exc:
                raise CanaryError(
                    f"index references missing bundle frontend asset {path}"
                ) from exc
            expected[path] = hashlib.sha256(data).hexdigest()

    for path, digest in expected.items():
        if served_sha256(name, path) != digest:
            raise CanaryError(
                f"selected backend did not serve matching bundle frontend asset {path}"
            )
    return {"index_matches": True, "referenced_assets_verified": len(expected) - 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--minimum-image-version", required=True)
    parser.add_argument("--image-repository", default="coderluii/channelwatch")
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=120)
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    minimum = args.minimum_image_version.strip().lstrip("v")
    image = f"{args.image_repository}:{minimum}"
    name = f"cw-minimum-image-canary-{secrets.token_hex(6)}"
    temp_root = Path(tempfile.mkdtemp(prefix="channelwatch-minimum-image-canary-"))
    config_dir = temp_root / "config"
    config_dir.mkdir(mode=0o777)
    release_dir = config_dir / "channelwatch-runtime" / "releases" / f"v{version}"

    try:
        run(["docker", "pull", image])
        # Stage the bundle before the historical image repairs /config ownership.
        # Selection still happens only after Supervisor has captured the original
        # image-owned environment, preserving the in-app activation boundary.
        extract_bundle(args.bundle.resolve(), release_dir)
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--env",
                "TZ=UTC",
                "--env",
                "PUID=501",
                "--env",
                "PGID=20",
                "--mount",
                f"type=bind,src={config_dir},dst=/config",
                image,
            ]
        )
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == minimum,
            args.startup_timeout,
        )

        # Select only after Supervisor has captured its image-owned environment.
        write_active(name, version)
        restart(name)
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == version,
            args.startup_timeout,
        )
        initial = verify_frontend(name, args.bundle.resolve())

        restart(name, "ui")
        wait_for_json(name, "/healthz/live", lambda payload: True, args.startup_timeout)
        ui_restart = verify_frontend(name, args.bundle.resolve())

        remove_active(name)
        restart(name)
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == minimum,
            args.startup_timeout,
        )
        write_active(name, version)
        restart(name)
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == version,
            args.startup_timeout,
        )
        reapply = verify_frontend(name, args.bundle.resolve())

        run(["docker", "restart", name])
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == version,
            args.startup_timeout,
        )
        container_restart = verify_frontend(name, args.bundle.resolve())

        evidence = {
            "schema": 1,
            "status": "passed",
            "source_image_version": minimum,
            "target_application_version": version,
            "bundle_sha256": hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
            "dynamic_activation": initial,
            "ui_restart": ui_restart,
            "rollback_reapply": reapply,
            "container_restart": container_restart,
        }
        args.evidence_json.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_json.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, sort_keys=True))
        return 0
    finally:
        run(["docker", "rm", "--force", name], check=False)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
