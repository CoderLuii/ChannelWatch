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
import re
import secrets
import shutil
import sys

# This release canary intentionally invokes fixed Docker argv.
import subprocess  # nosec B404
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

# Immutable generated path inside the disposable ChannelWatch image.
SUPERVISOR_CONFIG = "/tmp/supervisord.conf"  # nosec B108
APP_ROOT = Path(__file__).resolve().parents[2] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class CanaryError(RuntimeError):
    """Raised when the minimum-image app-update contract is not satisfied."""


def pinned_image(repository: str, index_digest: str) -> tuple[str, str]:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", index_digest):
        raise CanaryError("The minimum image requires an immutable index digest.")
    index = json.loads(run([
        "docker", "buildx", "imagetools", "inspect", "--raw",
        f"{repository}@{index_digest}",
    ]).stdout)
    manifests = index.get("manifests") if isinstance(index, dict) else None
    if not isinstance(manifests, list):
        raise CanaryError("The minimum image must identify a multi-platform index.")
    matches = [
        member for member in manifests
        if isinstance(member, dict)
        and member.get("platform", {}).get("os") == "linux"
        and member.get("platform", {}).get("architecture") == "amd64"
    ]
    if len(matches) != 1 or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", str(matches[0].get("digest", ""))
    ):
        raise CanaryError("The minimum image requires one exact Linux AMD64 manifest.")
    digest = matches[0]["digest"]
    return f"{repository}@{digest}", digest


def verify_pulled_image(image: str) -> str:
    values = json.loads(run(["docker", "image", "inspect", image]).stdout)
    if not isinstance(values, list) or len(values) != 1:
        raise CanaryError("The exact minimum image could not be inspected.")
    value = values[0]
    if (
        value.get("Os") != "linux"
        or value.get("Architecture") != "amd64"
        or image not in value.get("RepoDigests", [])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get("Id", "")))
    ):
        raise CanaryError("The pulled minimum image does not match its pinned platform.")
    return value["Id"]


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


def docker_exec(name: str, code: str, user: str | None = None) -> str:
    command = ["docker", "exec"]
    if user is not None:
        command.extend(["--user", user])
    command.extend([name, "python", "-c", code])
    return run(command).stdout.strip()


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


def expected_runtime_identity(
    catalog_path: Path,
    bundle_path: Path,
    minimum_version: str,
    target_version: str,
) -> dict[str, object]:
    from core.runtime_generation import (
        descriptor_generation_id,
        detect_runtime_platform,
        validate_runtime_descriptor,
    )
    from core.update_center import (
        UPDATE_PUBLIC_KEYS,
        read_update_document_bytes,
        verify_ed25519_signature,
    )

    catalog_bytes = catalog_path.read_bytes()
    bundle_bytes = bundle_path.read_bytes()
    selected = read_update_document_bytes(
        catalog_bytes,
        UPDATE_PUBLIC_KEYS,
        current_version=minimum_version,
        runtime_abi="channelwatch-runtime-v1",
        settings_schema_version=7,
        launcher_protocol=4,
    )
    payload = selected.get("payload") if isinstance(selected, dict) else None
    signature = selected.get("signature") if isinstance(selected, dict) else None
    descriptor = payload.get("runtime_descriptor") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or str(payload.get("version") or "").lstrip("v") != target_version
        or not isinstance(signature, dict)
        or not isinstance(descriptor, dict)
    ):
        raise CanaryError("The signed catalog did not select the requested runtime.")
    validate_runtime_descriptor(
        descriptor, detect_runtime_platform(launcher_protocol=4)
    )
    generation = descriptor_generation_id(descriptor)
    if descriptor.get("generation_id") != generation:
        raise CanaryError("The signed runtime descriptor identity is invalid.")
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            embedded_descriptor = json.loads(
                archive.read("channelwatch-bundle.json").decode("utf-8")
            )
    except (KeyError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise CanaryError("The runtime bundle descriptor is unreadable.") from exc
    bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
    if (
        embedded_descriptor != descriptor
        or payload.get("bundle_sha256") != bundle_sha256
    ):
        raise CanaryError("The runtime bundle does not match the signed catalog.")
    key_id = str(payload.get("key_id") or signature.get("key_id") or "")
    try:
        verify_ed25519_signature(
            UPDATE_PUBLIC_KEYS,
            key_id,
            str(payload.get("bundle_signature") or ""),
            bytes.fromhex(bundle_sha256),
        )
    except Exception as exc:
        raise CanaryError("The runtime bundle signature is invalid.") from exc
    runtime_abi = str(payload.get("runtime_abi") or "")
    settings_schema_version = payload.get("settings_schema_version")
    if runtime_abi != "channelwatch-runtime-v1" or settings_schema_version != 7:
        raise CanaryError("The selected runtime is incompatible with the canary.")
    return {
        "version": target_version,
        "path": f"/config/channelwatch-runtime/releases/{generation}",
        "runtime_abi": runtime_abi,
        "settings_schema_version": settings_schema_version,
        "runtime_generation_id": generation,
        "runtime_descriptor_sha256": generation,
        "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
        "runtime_trust_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "manifest": {
            "release_url": payload.get("release_url"),
            "bundle_sha256": bundle_sha256,
            "key_id": key_id,
        },
        "metadata": {
            "version": str(payload.get("version") or ""),
            "version_tag": str(
                payload.get("version_tag") or f"v{target_version}"
            ),
            "runtime_abi": runtime_abi,
            "settings_schema_version": settings_schema_version,
            "git_sha": descriptor["source_commit"],
            "bundle_type": descriptor["bundle_format"],
        },
    }


def _validated_protocol_four_active(
    active: object, expected: dict[str, object]
) -> dict[str, object]:
    identity_fields = (
        "runtime_generation_id",
        "runtime_descriptor_sha256",
        "dependency_inventory_sha256",
        "runtime_trust_sha256",
    )
    if (
        not isinstance(active, dict)
        or active.get("activation_protocol") != 4
        or not str(active.get("activation_id") or "")
        or active.get("runtime_abi") != "channelwatch-runtime-v1"
        or active.get("settings_schema_version") != 7
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(active.get(field) or "")) is None
            for field in identity_fields
        )
        or active.get("runtime_generation_id")
        != active.get("runtime_descriptor_sha256")
        or active.get("path")
        != (
            "/config/channelwatch-runtime/releases/"
            + str(active.get("runtime_generation_id") or "")
        )
    ):
        raise CanaryError("The canary requires a complete protocol-4 active identity.")
    if any(active.get(field) != value for field, value in expected.items()):
        raise CanaryError("The active selection does not match the signed runtime identity.")
    return active


def apply_runtime_update(
    name: str, minimum: str, expected: dict[str, object]
) -> tuple[dict[str, object], str]:
    version = str(expected["version"])
    code = (
        "import json; from pathlib import Path; "
        "from core.runtime_launcher import request_container_restart; "
        "from core.update_center import UpdateManager; "
        "root=Path('/config/minimum-image-canary'); "
        "catalog_url='https://channelwatch.coderluii.dev/updates/v3/stable.json'; "
        "catalog=(root/'catalog.json').read_bytes(); "
        "bundle=(root/'runtime.zip').read_bytes(); "
        "fetch=lambda url,maximum: (catalog if url==catalog_url else bundle) "
        "if len(catalog if url==catalog_url else bundle)<=maximum "
        "else (_ for _ in ()).throw(ValueError('canary asset exceeds product limit')); "
        f"manager=UpdateManager(config_dir=Path('/config'), current_version={minimum!r}, "
        f"image_version={minimum!r}, launcher_protocol=4, manifest_url=catalog_url, "
        "fetcher=fetch, "
        "restart_callable=lambda: request_container_restart() is None); "
        "manager.check(); "
        f"job=manager.apply({version!r}); "
        "active=json.loads(manager.active_path.read_text(encoding='utf-8')); "
        "print(json.dumps({'job':job,'active':active},sort_keys=True))"
    )
    try:
        result = json.loads(docker_exec(name, code, user="501:20"))
    except (CanaryError, json.JSONDecodeError) as exc:
        raise CanaryError("The minimum image could not prepare the signed runtime.") from exc
    job = result.get("job") if isinstance(result, dict) else None
    active = result.get("active") if isinstance(result, dict) else None
    if (
        not isinstance(job, dict)
        or job.get("operation") != "apply"
        or job.get("status") != "restarting"
        or not str(job.get("job_id") or "")
        or str(job.get("version") or "").lstrip("v") != version
        or job.get("bundle_sha256") != expected["manifest"]["bundle_sha256"]
    ):
        raise CanaryError("The minimum image did not accept the signed runtime update.")
    return _validated_protocol_four_active(active, expected), str(job["job_id"])


def _activation_state_converged(
    state: object,
    selected_active: dict[str, object],
    expected: dict[str, object],
    apply_job_id: str,
) -> bool:
    if not isinstance(state, dict):
        return False
    health = state.get("health")
    job = state.get("job")
    supervisor = state.get("supervisor")
    return bool(
        isinstance(health, dict)
        and health.get("live") is True
        and health.get("startup") is True
        and isinstance(supervisor, str)
        and re.search(r"(?m)^core\s+RUNNING\s+pid\s+[1-9][0-9]*,", supervisor)
        and re.search(r"(?m)^ui\s+RUNNING\s+pid\s+[1-9][0-9]*,", supervisor)
        and state.get("active") == selected_active
        and isinstance(job, dict)
        and job.get("job_id") == apply_job_id
        and job.get("operation") == "apply"
        and job.get("status") == "success"
        and str(job.get("version") or "").lstrip("v") == expected["version"]
        and job.get("bundle_sha256") == expected["manifest"]["bundle_sha256"]
        and state.get("restart_required") is False
        and state.get("handoff") is False
        and state.get("activation_artifacts") == []
    )


def wait_for_activation_convergence(
    name: str,
    selected_active: dict[str, object],
    expected: dict[str, object],
    apply_job_id: str,
    timeout: int,
) -> None:
    code = (
        "import json,subprocess,sys,urllib.request; from pathlib import Path; "
        "root=Path('/config/channelwatch-runtime'); "
        "read=lambda p: json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None; "
        "healthy=lambda path: urllib.request.urlopen("
        "'http://127.0.0.1:8501'+path,timeout=3).status==200; "
        "supervisor=subprocess.run([sys.executable,'-m','supervisor.supervisorctl',"
        f"'-c',{SUPERVISOR_CONFIG!r},'status'],text=True,capture_output=True,check=False); "
        "print(json.dumps({'health':{'live':healthy('/healthz/live'),"
        "'startup':healthy('/healthz/startup')},'supervisor':supervisor.stdout,"
        "'active':read(root/'active.json'),'job':read(root/'update-job.json'),"
        "'restart_required':(root/'restart-required.json').exists(),"
        "'handoff':(root/'restart-services-accepted.json').exists(),"
        "'activation_artifacts':sorted(p.name for p in root.glob('activation-*.json'))},"
        "sort_keys=True))"
    )
    deadline = time.monotonic() + timeout
    last_state: object = None
    while time.monotonic() < deadline:
        try:
            last_state = json.loads(docker_exec(name, code, user="501:20"))
            if _activation_state_converged(
                last_state, selected_active, expected, apply_job_id
            ):
                return
        except (CanaryError, json.JSONDecodeError, OSError):
            last_state = None
        time.sleep(1)
    summary = json.dumps(last_state, sort_keys=True)[:1000]
    raise CanaryError(
        "the selected runtime did not reach durable activation convergence: "
        + summary
    )


def write_active(
    name: str, active: dict[str, object], expected: dict[str, object]
) -> None:
    payload = json.dumps(
        _validated_protocol_four_active(active, expected),
        sort_keys=True,
        separators=(",", ":"),
    )
    code = (
        "import json; from pathlib import Path; "
        "from core.helpers.atomic_io import atomic_write_json; "
        "p=Path('/config/channelwatch-runtime/active.json'); "
        f"atomic_write_json(p,json.loads({payload!r}))"
    )
    docker_exec(name, code, user="501:20")


def remove_active(name: str) -> None:
    docker_exec(
        name,
        "from pathlib import Path; "
        "Path('/config/channelwatch-runtime/active.json').unlink()",
        user="501:20",
    )


def read_active(name: str, expected: dict[str, object]) -> dict[str, object]:
    code = (
        "from pathlib import Path; "
        "print(Path('/config/channelwatch-runtime/active.json').read_text(encoding='utf-8'))"
    )
    try:
        active = json.loads(docker_exec(name, code, user="501:20"))
    except (CanaryError, json.JSONDecodeError) as exc:
        raise CanaryError("The selected protocol-4 active identity is unreadable.") from exc
    return _validated_protocol_four_active(active, expected)


def retain_failure_diagnostics(
    name: str, evidence_json: Path, error: BaseException
) -> dict[str, object]:
    docker_log = run(["docker", "logs", name], check=False)
    supervisor = run(
        [
            "docker", "exec", name, "python", "-m", "supervisor.supervisorctl",
            "-c", SUPERVISOR_CONFIG, "status",
        ],
        check=False,
    )
    docker_path = evidence_json.with_name(evidence_json.stem + "-docker.log")
    supervisor_path = evidence_json.with_name(
        evidence_json.stem + "-supervisor.log"
    )
    failure_path = evidence_json.with_name(evidence_json.stem + "-failure.json")
    evidence_json.parent.mkdir(parents=True, exist_ok=True)
    docker_path.write_text(
        docker_log.stdout + docker_log.stderr, encoding="utf-8"
    )
    supervisor_path.write_text(
        supervisor.stdout + supervisor.stderr, encoding="utf-8"
    )
    retained = {
        "schema": 1,
        "status": "failed",
        "error": str(error),
        "docker_log": docker_path.name,
        "supervisor_log": supervisor_path.name,
    }
    failure_path.write_text(
        json.dumps(retained, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(docker_log.stdout + docker_log.stderr, file=sys.stderr)
    print(supervisor.stdout + supervisor.stderr, file=sys.stderr)
    return retained


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
    parser.add_argument("--runtime-bundle", type=Path, required=True)
    parser.add_argument("--runtime-catalog", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--minimum-image-version", required=True)
    parser.add_argument("--image-repository", default="coderluii/channelwatch")
    parser.add_argument("--image-index-digest", required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=120)
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    minimum = args.minimum_image_version.strip().lstrip("v")
    image, platform_digest = pinned_image(args.image_repository, args.image_index_digest)
    name = f"cw-minimum-image-canary-{secrets.token_hex(6)}"
    temp_root = Path(tempfile.mkdtemp(prefix="channelwatch-minimum-image-canary-"))
    config_dir = temp_root / "config"
    config_dir.mkdir(mode=0o777)
    input_dir = config_dir / "minimum-image-canary"
    input_dir.mkdir()
    app_bundle = args.bundle.resolve()
    runtime_bundle = args.runtime_bundle.resolve()
    runtime_catalog = args.runtime_catalog.resolve()
    container_started = False

    try:
        for path, label in (
            (app_bundle, "application bundle"),
            (runtime_bundle, "runtime bundle"),
            (runtime_catalog, "runtime catalog"),
        ):
            if not path.is_file() or path.is_symlink():
                raise CanaryError(f"The {label} must be a regular, non-symlink file.")
        expected_identity = expected_runtime_identity(
            runtime_catalog, runtime_bundle, minimum, version
        )
        shutil.copyfile(runtime_bundle, input_dir / "runtime.zip")
        shutil.copyfile(runtime_catalog, input_dir / "catalog.json")
        run(["docker", "pull", image])
        image_id = verify_pulled_image(image)
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
        container_started = True
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == minimum,
            args.startup_timeout,
        )

        # Prepare and select only after Supervisor has captured its image-owned
        # environment. The minimum image verifies the signed catalog, bundle,
        # dependency generation, and durable protocol-4 identity itself.
        selected_active, apply_job_id = apply_runtime_update(
            name, minimum, expected_identity
        )
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == version,
            args.startup_timeout,
        )
        wait_for_activation_convergence(
            name,
            selected_active,
            expected_identity,
            apply_job_id,
            args.startup_timeout,
        )
        if read_active(name, expected_identity) != selected_active:
            raise CanaryError("The active runtime identity changed during restart.")
        initial = verify_frontend(name, app_bundle)

        restart(name, "ui")
        wait_for_json(name, "/healthz/live", lambda payload: True, args.startup_timeout)
        if read_active(name, expected_identity) != selected_active:
            raise CanaryError("The active runtime identity changed during UI restart.")
        ui_restart = verify_frontend(name, app_bundle)

        remove_active(name)
        restart(name)
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == minimum,
            args.startup_timeout,
        )
        write_active(name, selected_active, expected_identity)
        restart(name)
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == version,
            args.startup_timeout,
        )
        if read_active(name, expected_identity) != selected_active:
            raise CanaryError("The reapplied runtime identity changed during restart.")
        reapply = verify_frontend(name, app_bundle)

        run(["docker", "restart", name])
        wait_for_json(
            name,
            "/api/about",
            lambda payload: str(payload.get("version", "")).lstrip("v") == version,
            args.startup_timeout,
        )
        if read_active(name, expected_identity) != selected_active:
            raise CanaryError("The active runtime identity changed after container restart.")
        container_restart = verify_frontend(name, app_bundle)

        evidence = {
            "schema": 1,
            "status": "passed",
            "source_image_version": minimum,
            "image_index_digest": args.image_index_digest,
            "platform_manifest_digest": platform_digest,
            "pulled_image_id": image_id,
            "platform": "linux/amd64",
            "target_application_version": version,
            "bundle_sha256": hashlib.sha256(app_bundle.read_bytes()).hexdigest(),
            "runtime_bundle_sha256": hashlib.sha256(
                runtime_bundle.read_bytes()
            ).hexdigest(),
            "runtime_catalog_sha256": hashlib.sha256(
                runtime_catalog.read_bytes()
            ).hexdigest(),
            "runtime_generation_id": selected_active["runtime_generation_id"],
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
    except BaseException as exc:
        if container_started:
            retain_failure_diagnostics(name, args.evidence_json, exc)
        raise
    finally:
        run(["docker", "rm", "--force", name], check=False)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
