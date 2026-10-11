#!/usr/bin/env python3
"""Qualify one native child from an exact sealed ChannelWatch OCI candidate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable

SHA256 = re.compile(r"[0-9a-f]{64}")
SHA40 = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
LABEL = "channelwatch.native-image-qualification=true"
Runner = Callable[..., subprocess.CompletedProcess[str]]


class NativeImageError(ValueError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_describer():
    path = Path(__file__).with_name("describe-oci-image.py")
    spec = importlib.util.spec_from_file_location("channelwatch_describe_oci", path)
    if spec is None or spec.loader is None:
        raise NativeImageError("OCI descriptor verifier is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, "r:*") as bundle:
        members = bundle.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise NativeImageError("OCI archive contains an unsafe member.")
        bundle.extractall(destination, members=members, filter="data")


def _verify_selected_archive(archive: Path, expected_config: str, expected_tag: str) -> list[str]:
    expected_sha = expected_config.removeprefix("sha256:")
    with tarfile.open(archive, "r:*") as bundle:
        members = bundle.getmembers()
        by_name = {member.name: member for member in members}
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise NativeImageError("Selected Docker archive contains an unsafe member.")
            if member.isfile() or member.isdir():
                continue
            if not member.issym() or PurePosixPath(member.linkname).is_absolute():
                raise NativeImageError("Selected Docker archive contains an unsafe member.")
            target_name = posixpath.normpath(str(path.parent / member.linkname))
            if target_name == ".." or target_name.startswith("../"):
                raise NativeImageError("Selected Docker archive contains an unsafe member.")
            target = by_name.get(target_name)
            if target is None or not target.isfile():
                raise NativeImageError("Selected Docker archive contains an unsafe member.")
        manifest_member = by_name.get("manifest.json")
        if manifest_member is None or not manifest_member.isfile() or manifest_member.size > 1024 * 1024:
            raise NativeImageError("Selected Docker archive has no bounded manifest.")
        manifest_handle = bundle.extractfile(manifest_member)
        if manifest_handle is None:
            raise NativeImageError("Selected Docker archive manifest is unreadable.")
        try:
            manifest = json.load(manifest_handle)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise NativeImageError("Selected Docker archive manifest is invalid.") from exc
        if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
            raise NativeImageError("Selected Docker archive must contain exactly one image.")
        row = manifest[0]
        config_name = row.get("Config")
        accepted_tags = ([expected_tag], [f"docker.io/library/{expected_tag}"])
        if row.get("RepoTags") not in accepted_tags or not isinstance(config_name, str):
            raise NativeImageError("Selected Docker archive tag or config reference is invalid.")
        config_member = by_name.get(config_name)
        if config_member is None or not config_member.isfile() or config_member.size > 16 * 1024 * 1024:
            raise NativeImageError("Selected Docker archive config is missing or oversized.")
        config_handle = bundle.extractfile(config_member)
        if config_handle is None:
            raise NativeImageError("Selected Docker archive config is unreadable.")
        config_raw = config_handle.read()
    if hashlib.sha256(config_raw).hexdigest() != expected_sha:
        raise NativeImageError("Selected Docker archive config does not match the approved descriptor.")
    try:
        config = json.loads(config_raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NativeImageError("Selected Docker archive config is invalid JSON.") from exc
    diff_ids = config.get("rootfs", {}).get("diff_ids") if isinstance(config, dict) else None
    if not isinstance(diff_ids, list) or not diff_ids or any(not DIGEST.fullmatch(str(item)) for item in diff_ids):
        raise NativeImageError("Selected Docker archive config has invalid rootfs layers.")
    return diff_ids


def _run(runner: Runner, command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = runner(command, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise NativeImageError(f"Command failed: {command[0]}: {result.stderr.strip()}")
    return result


def _native_architecture(runner: Runner, requested: str) -> str:
    host = ARCHITECTURES.get(platform.machine().lower())
    docker = ARCHITECTURES.get(
        _run(runner, ["docker", "info", "--format", "{{.Architecture}}"], timeout=30).stdout.strip().lower()
    )
    if host is None or docker is None or host != docker or requested != host:
        raise NativeImageError("Host, Docker server, and requested architecture must match natively.")
    return host


def _wait_ready(port: int, timeout: int) -> bytes:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz/startup"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read()
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None
            if response.status == 200 and payload == {"status": "ready"}:
                return body
        except OSError:
            pass
        time.sleep(1)
    raise NativeImageError("Exact candidate did not become ready.")


def _browser_smoke(runner: Runner, port: int) -> subprocess.CompletedProcess[str]:
    script = """
const {chromium}=require(process.argv[2]);
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();
    const response=await page.goto(process.argv[1]);
    if(!response||!response.ok()) throw new Error('ChannelWatch UI request failed');
    const shell=page.locator('[data-testid="auth-bootstrap-shell"],[data-testid="auth-login-shell"],[data-testid="auth-noauth-shell"]').first();
    await shell.waitFor({state:'visible'});
    const title=await page.title();
    if(title!=='ChannelWatch') throw new Error('ChannelWatch title mismatch');
    console.log(JSON.stringify({title,hydrated_shell:true}));
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error.message);process.exit(2)});
""".strip()
    result = _run(
        runner,
        [
            shutil.which("node") or "node", "-e", script,
            f"http://127.0.0.1:{port}/",
            str(Path("app/ui/node_modules/@playwright/test").absolute()),
        ],
        timeout=120,
    )
    try:
        evidence = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise NativeImageError("Headless browser evidence is malformed.") from exc
    if evidence != {"title": "ChannelWatch", "hydrated_shell": True}:
        raise NativeImageError("Headless browser evidence does not prove the hydrated ChannelWatch UI.")
    return result


def _combined_logs(result: subprocess.CompletedProcess[str]) -> bytes:
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    return f"[stdout]\n{stdout}\n[stderr]\n{stderr}".encode("utf-8")


def _capture_logs(runner: Runner, container: str, created: bool, output: Path) -> str | None:
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not created:
            output.write_bytes(b"[diagnostic]\ncontainer was not created; no container logs are available\n")
            return None
        result = runner(
            ["docker", "logs", container], text=True, capture_output=True,
            timeout=30, check=False,
        )
        output.write_bytes(_combined_logs(result))
        if result.returncode:
            return "container-log-capture"
    except Exception as exc:
        try:
            output.write_text(
                f"[diagnostic]\ncontainer log capture raised {type(exc).__name__}\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return "container-log-capture"
    return None


def _cleanup_resources(
    runner: Runner,
    *,
    container: str,
    container_created: bool,
    volume: str,
    volume_created: bool,
    image: str | None,
) -> list[str]:
    commands: list[tuple[str, list[str]]] = []
    if container_created:
        commands.append(("container", ["docker", "rm", "-f", container]))
    if volume_created:
        commands.append(("volume", ["docker", "volume", "rm", "-f", volume]))
    if image is not None:
        commands.append(("image", ["docker", "image", "rm", "-f", image]))
    failures: list[str] = []
    for label, command in commands:
        try:
            result = runner(command, text=True, capture_output=True, timeout=60, check=False)
            if result.returncode:
                failures.append(label)
        except Exception:
            failures.append(label)
    return failures


def _published_port(runner: Runner, container: str) -> int:
    value = _run(runner, ["docker", "port", container, "8501/tcp"], timeout=30).stdout.strip()
    try:
        return int(value.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise NativeImageError("Docker did not publish the candidate UI port.") from exc


def _preservation(runner: Runner, container: str) -> dict[str, str]:
    command = "sha256sum /config/settings.json /config/encryption.key"
    output = _run(runner, ["docker", "exec", container, "sh", "-c", command], timeout=30).stdout
    result: dict[str, str] = {}
    for line in output.splitlines():
        digest, path = line.split(maxsplit=1)
        path = path.lstrip(" *")
        if SHA256.fullmatch(digest) and path in {"/config/settings.json", "/config/encryption.key"}:
            result[path] = digest
    if set(result) != {"/config/settings.json", "/config/encryption.key"}:
        raise NativeImageError("Settings and managed-key preservation hashes are incomplete.")
    return result


def _image_runtime_identity(runner: Runner, container: str, version: str) -> int:
    probe = (
        "import json,runpy,sys;sys.path.insert(0,'/app');"
        "scope=runpy.run_path('/app/core/runtime_launcher.py');"
        "assert str(scope['IMAGE_APP_DIR'])=='/app';"
        "print(json.dumps(scope['launcher_protocol_status']()))"
    )
    result = _run(
        runner,
        ["docker", "exec", container, "/venv/bin/python", "-I", "-c", probe],
        timeout=60,
    )
    try:
        identity = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise NativeImageError("Candidate image did not report a valid protocol-4 identity.") from exc
    if (
        not isinstance(identity, dict)
        or identity.get("image_version") != version
        or type(identity.get("launcher_protocol")) is not int
        or identity["launcher_protocol"] != 4
    ):
        raise NativeImageError("Candidate image did not report the expected protocol-4 identity.")
    return identity["launcher_protocol"]


def qualify(args: argparse.Namespace, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    archive = args.oci_archive.absolute()
    descriptor_path = args.descriptor.absolute()
    if _sha(archive) != args.expected_archive_sha256 or _sha(descriptor_path) != args.expected_descriptor_sha256:
        raise NativeImageError("Sealed OCI archive or descriptor digest does not match the approved candidate.")
    architecture = _native_architecture(runner, args.architecture)
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    platform_row = next((row for row in descriptor.get("platforms", []) if row.get("platform") == f"linux/{architecture}"), None)
    if not isinstance(platform_row, dict) or not DIGEST.fullmatch(str(platform_row.get("config", {}).get("digest", ""))):
        raise NativeImageError("Approved descriptor has no exact native child config.")
    prefix = f"cw-native-{architecture}-{secrets.token_hex(4)}"
    container = prefix + "-app"
    volume = prefix + "-config"
    image_tag = f"channelwatch-native:{prefix}"
    volume_created = False
    loaded_image: str | None = None
    container_created = False
    receipt: dict[str, Any] | None = None
    failure: Exception | None = None
    evidence_error: str | None = None
    cleanup_failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix=prefix + "-") as temporary:
        root = Path(temporary)
        layout = root / "layout"
        selected = root / "selected.tar"
        selected.touch(mode=0o600)
        try:
            _safe_extract(archive, layout)
            described = _load_describer().describe_layout(layout)
            if described != descriptor:
                raise NativeImageError("Sealed OCI archive does not match the approved descriptor bytes.")
            _run(runner, ["docker", "volume", "create", "--label", LABEL, volume], timeout=30)
            volume_created = True
            _run(
                runner,
                [
                    "docker", "run", "--rm", "--pull=never",
                    "--volume", f"{layout}:/work/layout:ro",
                    "--volume", f"{root}:/output",
                    args.skopeo_image, "copy", "--override-os", "linux",
                    "--override-arch", architecture,
                    "oci:/work/layout:release-candidate",
                    f"docker-archive:/output/selected.tar:{image_tag}",
                ],
            )
            expected_diff_ids = _verify_selected_archive(
                selected, platform_row["config"]["digest"], image_tag
            )
            loaded = _run(runner, ["docker", "load", "--input", str(selected)]).stdout
            image = loaded.split("Loaded image: ", 1)[-1].strip()
            if image != image_tag:
                raise NativeImageError("Docker loaded an unexpected candidate image tag.")
            loaded_image = image
            inspection = json.loads(_run(runner, ["docker", "image", "inspect", image]).stdout)[0]
            labels = inspection.get("Config", {}).get("Labels") or {}
            if inspection.get("Architecture") != architecture or inspection.get("Os") != "linux":
                raise NativeImageError("Selected image is not the requested native Linux child.")
            if labels.get("org.opencontainers.image.version") != args.version or labels.get("org.opencontainers.image.revision") != args.source_commit:
                raise NativeImageError("Selected image labels do not bind the release version and source commit.")
            if inspection.get("RootFS", {}).get("Layers") != expected_diff_ids:
                raise NativeImageError("Loaded image rootfs does not match the approved config.")
            _run(
                runner,
                ["docker", "run", "-d", "--name", container, "--label", LABEL,
                 "--mount", f"type=volume,source={volume},target=/config", "-p", "127.0.0.1::8501", image],
            )
            container_created = True
            port = _published_port(runner, container)
            startup = _wait_ready(port, args.startup_timeout)
            launcher_protocol = _image_runtime_identity(runner, container, args.version)
            cli = _run(runner, ["docker", "exec", container, "/usr/local/bin/channelwatch", "doctor", "config-check"], timeout=60)
            browser = _browser_smoke(runner, port)
            before = _preservation(runner, container)
            container_id = _run(runner, ["docker", "inspect", "--format", "{{.Id}}", container], timeout=30).stdout.strip()
            _run(runner, ["docker", "restart", container], timeout=120)
            restart_port = _published_port(runner, container)
            _wait_ready(restart_port, args.startup_timeout)
            _image_runtime_identity(runner, container, args.version)
            after = _preservation(runner, container)
            if before != after:
                raise NativeImageError("Settings or managed encryption key changed across same-container restart.")
            if _run(runner, ["docker", "inspect", "--format", "{{.Id}}", container], timeout=30).stdout.strip() != container_id:
                raise NativeImageError("Qualification replaced the candidate container.")
            receipt = {
                "schema": 1, "status": "passed", "architecture": architecture,
                "native": True, "source_commit": args.source_commit, "version": args.version,
                "oci_archive_sha256": args.expected_archive_sha256,
                "descriptor_sha256": args.expected_descriptor_sha256,
                "image_index_digest": descriptor["image_index"]["digest"],
                "platform_manifest_digest": platform_row["manifest"]["digest"],
                "config_digest": platform_row["config"]["digest"],
                "runtime_ready": bool(startup), "cli": True, "headless_browser": True,
                "launcher_protocol": launcher_protocol,
                "same_container_restart": True, "persistence": True,
                "settings_key_preserved": before,
                "cli_output_sha256": hashlib.sha256(cli.stdout.encode()).hexdigest(),
                "browser_output_sha256": hashlib.sha256(browser.stdout.encode()).hexdigest(),
            }
        except Exception as exc:
            failure = exc
        finally:
            evidence_error = _capture_logs(runner, container, container_created, args.log_output)
            cleanup_failures = _cleanup_resources(
                runner,
                container=container,
                container_created=container_created,
                volume=volume,
                volume_created=volume_created,
                image=loaded_image,
            )
    postflight = [item for item in ([evidence_error] + cleanup_failures) if item]
    if failure is not None:
        if postflight:
            raise NativeImageError(
                f"{failure} Evidence or cleanup failed: {', '.join(postflight)}."
            ) from failure
        raise failure
    if postflight:
        raise NativeImageError(f"Evidence or cleanup failed: {', '.join(postflight)}.")
    if receipt is None:
        raise NativeImageError("Native image qualification produced no receipt.")
    receipt["container_log_sha256"] = _sha(args.log_output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oci-archive", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--expected-archive-sha256", required=True)
    parser.add_argument("--expected-descriptor-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--skopeo-image", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=180)
    args = parser.parse_args()
    log_output_fresh = False
    try:
        args.log_output.unlink(missing_ok=True)
        log_output_fresh = True
        for value, label in ((args.expected_archive_sha256, "archive"), (args.expected_descriptor_sha256, "descriptor")):
            if not SHA256.fullmatch(value):
                raise NativeImageError(f"Expected {label} must be a lowercase SHA-256 value.")
        if not SHA40.fullmatch(args.source_commit):
            raise NativeImageError("Expected source commit must be a complete lowercase Git SHA.")
        receipt = qualify(args)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    except Exception as exc:
        failure_receipt: dict[str, Any] = {
            "schema": 1,
            "status": "failed",
            "architecture": args.architecture,
            "native": False,
            "source_commit": args.source_commit,
            "version": args.version,
            "oci_archive_sha256": args.expected_archive_sha256,
            "descriptor_sha256": args.expected_descriptor_sha256,
            "failure_type": type(exc).__name__,
            "failure_summary": "Native image qualification did not pass.",
        }
        if log_output_fresh and args.log_output.is_file():
            failure_receipt["container_log_sha256"] = _sha(args.log_output)
        try:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(
                json.dumps(failure_receipt, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        except OSError as receipt_error:
            print(f"Could not write native image failure receipt: {receipt_error}", file=sys.stderr)
        print(f"Native image qualification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
