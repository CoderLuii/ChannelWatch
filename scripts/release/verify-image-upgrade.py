#!/usr/bin/env python3
"""Qualify one native old-volume image upgrade into an exact sealed candidate."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import importlib.util
import json
import os
import platform
import re
import secrets
import shutil
import subprocess  # nosec B404 - fixed Docker argv only
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
ASSET_VERIFIER = ROOT / "scripts/release/verify-update-assets.py"
NATIVE_VERIFIER = ROOT / "scripts/release/verify-native-image.py"
CAPABILITY_VERIFIER = ROOT / "scripts/release/verify-runtime-capability.py"
LABEL_KEY = "codex.task"
LABEL_VALUE = "channelwatch-image-upgrade"
LABEL = f"{LABEL_KEY}={LABEL_VALUE}"
LEGACY_CATALOG_URL = "https://channelwatch.coderluii.dev/updates/v2/stable.json"
ARCHITECTURES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA256_PREFIXED = re.compile(r"sha256:[0-9a-f]{64}")
SHA40 = re.compile(r"[0-9a-f]{40}")
JOB_ID = re.compile(r"[0-9a-f]{32}")
SERVER_ASSIGNED_JOB_ID_IMAGES = frozenset({"1.4.0", "1.4.1"})
IMAGE_PYTHON_VERSION = "3.14.8"


class ImageUpgradeError(RuntimeError):
    """Raised when native old-volume qualification does not hold."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImageUpgradeError(f"Could not load {path.name}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(
    command: list[str],
    *,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # nosec B603
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise ImageUpgradeError(f"{command[0]} failed: {detail[:500]}")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def native_architecture(requested: str, *, runner: Runner = run) -> str:
    host = ARCHITECTURES.get(platform.machine().lower())
    docker = ARCHITECTURES.get(
        runner(
            ["docker", "info", "--format", "{{.Architecture}}"], timeout=30
        ).stdout.strip().lower()
    )
    if host is None or docker is None or requested != host or docker != host:
        raise ImageUpgradeError(
            "Host, Docker server, and requested architecture must match natively."
        )
    return host


def verify_signed_baseline_assets(
    catalog: Path,
    bundle: Path,
    *,
    version: str,
    source_commit: str,
) -> tuple[dict[str, Any], str]:
    verifier = _load_script(ASSET_VERIFIER, "image_upgrade_asset_verifier")
    catalog_bytes = catalog.read_bytes()
    bundle_bytes = bundle.read_bytes()
    try:
        verified = verifier.verify_update_catalog(
            catalog_bytes,
            bundle_bytes,
            expected_version=version,
            expected_delivery_mode="app_update",
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
            expected_recommended_image_version=version,
        )
        metadata = verifier.validate_bundle_archive(
            bundle_bytes,
            expected_version=version,
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
        )
        if metadata.get("git_sha") != source_commit:
            raise ValueError("bundle source commit mismatch")
        releases = verified["payload"]["releases"]
        release = next(
            row
            for row in releases
            if isinstance(row, dict) and row.get("version") == version
        )
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        raise ImageUpgradeError(
            "The signed baseline catalog does not contain the expected app update."
        ) from exc
    bundle_url = str(release.get("bundle_url") or "")
    if not bundle_url.startswith(
        f"https://github.com/CoderLuii/ChannelWatch/releases/download/v{version}/"
    ):
        raise ImageUpgradeError("The baseline bundle URL is not release-bound.")
    return release, bundle_url


def _native_manifest(
    repository: str,
    index_digest: str,
    architecture: str,
    *,
    expected_manifest_digest: str,
    expected_version: str,
    expected_source_commit: str,
) -> tuple[str, str, str]:
    capability = _load_script(CAPABILITY_VERIFIER, "image_upgrade_capability")
    try:
        reference, manifest, image_id, _raw, _inspection = capability.pinned_native_image(
            repository, index_digest, architecture
        )
    except Exception as exc:
        raise ImageUpgradeError("The baseline image could not be bound natively.") from exc
    if manifest != expected_manifest_digest:
        raise ImageUpgradeError("The baseline native manifest digest does not match.")
    inspection = json.loads(run(["docker", "image", "inspect", reference]).stdout)[0]
    labels = inspection.get("Config", {}).get("Labels") or {}
    if (
        labels.get("org.opencontainers.image.version") != expected_version
        or labels.get("org.opencontainers.image.revision") != expected_source_commit
    ):
        raise ImageUpgradeError("The baseline image labels do not bind its release source.")
    return reference, manifest, image_id


def _load_candidate(
    args: argparse.Namespace,
    root: Path,
    *,
    image_tag: str,
) -> dict[str, str]:
    native = _load_script(NATIVE_VERIFIER, "image_upgrade_native")
    archive = args.candidate_oci_archive.resolve()
    descriptor_path = args.candidate_descriptor.resolve()
    if sha256_file(archive) != args.expected_archive_sha256:
        raise ImageUpgradeError("Candidate OCI archive digest does not match.")
    if sha256_file(descriptor_path) != args.expected_descriptor_sha256:
        raise ImageUpgradeError("Candidate descriptor digest does not match.")
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    platform_row = next(
        (
            row
            for row in descriptor.get("platforms", [])
            if row.get("platform") == f"linux/{args.architecture}"
        ),
        None,
    )
    if not isinstance(platform_row, dict):
        raise ImageUpgradeError("Candidate descriptor has no native platform row.")
    layout = root / "candidate-layout"
    selected = root / "candidate.tar"
    selected.touch(mode=0o600)
    native._safe_extract(archive, layout)
    if native._load_describer().describe_layout(layout) != descriptor:
        raise ImageUpgradeError("Candidate OCI layout does not match its descriptor.")
    run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--volume",
            f"{layout}:/work/layout:ro",
            "--volume",
            f"{root}:/output",
            args.skopeo_image,
            "copy",
            "--override-os",
            "linux",
            "--override-arch",
            args.architecture,
            "oci:/work/layout:release-candidate",
            f"docker-archive:/output/{selected.name}:{image_tag}",
        ],
        timeout=300,
    )
    expected_layers = native._verify_selected_archive(
        selected, platform_row["config"]["digest"], image_tag
    )
    loaded = run(["docker", "load", "--input", str(selected)], timeout=300).stdout
    if loaded.split("Loaded image: ", 1)[-1].strip() != image_tag:
        raise ImageUpgradeError("Docker loaded an unexpected candidate image tag.")
    inspection = json.loads(run(["docker", "image", "inspect", image_tag]).stdout)[0]
    labels = inspection.get("Config", {}).get("Labels") or {}
    if (
        inspection.get("Os") != "linux"
        or inspection.get("Architecture") != args.architecture
        or labels.get("org.opencontainers.image.version") != args.candidate_version
        or labels.get("org.opencontainers.image.revision") != args.source_commit
        or inspection.get("RootFS", {}).get("Layers") != expected_layers
    ):
        raise ImageUpgradeError("Loaded candidate identity does not match the sealed input.")
    return {
        "tag": image_tag,
        "image_id": str(inspection["Id"]),
        "index_digest": str(descriptor["image_index"]["digest"]),
        "manifest_digest": str(platform_row["manifest"]["digest"]),
        "config_digest": str(platform_row["config"]["digest"]),
        "python_version": candidate_python_version(inspection),
    }


class ApiClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url
        self.username = username
        self.password = password
        self.csrf = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def set_base_url(self, base_url: str) -> None:
        self.base_url = base_url

    def request(
        self, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        body = json.dumps(payload).encode() if payload is not None else None
        with self.opener.open(
            urllib.request.Request(self.base_url + path, data=body, headers=headers),
            timeout=20,
        ) as response:
            return json.loads(response.read() or b"{}")

    def login(self) -> None:
        response = self.request(
            "/api/v1/auth/login",
            {"username": self.username, "password": self.password},
        )
        self.csrf = str(response.get("csrf_token") or "")
        if not self.csrf:
            raise ImageUpgradeError("Synthetic administrator login returned no CSRF state.")


def wait_until(callback, timeout: int, description: str):
    deadline = time.monotonic() + timeout
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            value = callback()
            if value:
                return value
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last = exc
        time.sleep(1)
    suffix = type(last).__name__ if last else "no matching state"
    raise ImageUpgradeError(f"Timed out waiting for {description}: {suffix}")


def published_base_url(container: str) -> str:
    output = run(["docker", "port", container, "8501/tcp"], timeout=30).stdout.strip()
    try:
        port = int(output.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ImageUpgradeError("Docker did not publish the HTTP port.") from exc
    return f"http://127.0.0.1:{port}"


def _docker_json(container: str, code: str) -> dict[str, Any]:
    output = run(
        ["docker", "exec", container, "/venv/bin/python", "-I", "-c", code],
        timeout=60,
    ).stdout.strip().splitlines()
    if not output:
        raise ImageUpgradeError("Container probe returned no evidence.")
    return json.loads(output[-1])


def persistent_hashes(container: str) -> dict[str, str]:
    code = """
import hashlib,json
from pathlib import Path
paths=[Path('/config/settings.json'),Path('/config/encryption.key')]
out={}
for path in paths:
 if not path.is_file(): raise SystemExit('missing persistent path')
 out[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(out,sort_keys=True))
"""
    value = _docker_json(container, code)
    if set(value) != {
        "/config/settings.json",
        "/config/encryption.key",
    } or any(SHA256.fullmatch(str(item)) is None for item in value.values()):
        raise ImageUpgradeError("Persistent hash evidence is incomplete.")
    return {str(key): str(item) for key, item in value.items()}


def control_inventory(container: str) -> dict[str, Any]:
    code = """
import hashlib,json,stat
from pathlib import Path
root=Path('/config/channelwatch-runtime'); out={}
if root.exists():
 for path in sorted(root.iterdir(),key=lambda p:p.name):
  st=path.lstat()
  if stat.S_ISREG(st.st_mode): out[path.name]={'type':'file','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mode':oct(stat.S_IMODE(st.st_mode))}
  elif stat.S_ISDIR(st.st_mode): out[path.name]={'type':'directory','mode':oct(stat.S_IMODE(st.st_mode))}
  else: out[path.name]={'type':'other'}
print(json.dumps(out,sort_keys=True))
"""
    return _docker_json(container, code)


def deactivated_active(container: str) -> dict[str, Any]:
    code = """
import json,stat
from pathlib import Path
p=Path('/config/channelwatch-runtime/deactivated-active.json')
st=p.lstat()
if not stat.S_ISREG(st.st_mode): raise SystemExit('invalid deactivated active record')
value=json.loads(p.read_text(encoding='utf-8'))
if not isinstance(value,dict): raise SystemExit('invalid deactivated active payload')
print(json.dumps(value,sort_keys=True))
"""
    return _docker_json(container, code)


def external_key_hash(container: str) -> str:
    code = """
import hashlib,json,os
from pathlib import Path
p=Path(os.environ['CHANNELWATCH_SECRET_STORAGE_KEY_FILE'])
print(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
"""
    digest = str(_docker_json(container, code).get("sha256") or "")
    if SHA256.fullmatch(digest) is None:
        raise ImageUpgradeError("External storage-key evidence is incomplete.")
    return digest


def encrypted_settings_usable(container: str) -> bool:
    code = """
import json
import sys
from pathlib import Path
p=Path('/config/settings.json')
raw=p.read_text(encoding='utf-8')
data=json.loads(raw)
servers=data.get('dvr_servers') or []
rows=[item for item in servers if isinstance(item,dict) and item.get('id')=='upgrade-canary']
cipher=str(rows[0].get('api_key') or '') if len(rows)==1 else ''
decrypted=''
try:
 sys.path.insert(0,'/app')
 from core.helpers.encryption import bootstrap_encryption_key,decrypt_value
 key=bootstrap_encryption_key(settings_file=p)
 decrypted=decrypt_value(cipher,key)
except Exception:
 pass
print(json.dumps({'encrypted':cipher.startswith('fernet:'),'plaintext_absent':'synthetic-upgrade-secret' not in raw,'decrypts_expected':decrypted=='synthetic-upgrade-secret'}))
"""
    value = _docker_json(container, code)
    return value == {
        "encrypted": True,
        "plaintext_absent": True,
        "decrypts_expected": True,
    }


def image_process_commands(container: str) -> dict[str, str]:
    rows = run(
        ["docker", "top", container, "-eo", "pid,args"], timeout=30
    ).stdout.splitlines()
    if not rows or rows[0].split() != ["PID", "COMMAND"]:
        raise ImageUpgradeError(
            "Candidate process listing is missing the exact PID COMMAND header."
        )

    process_commands: list[str] = []
    process_ids: set[int] = set()
    for row in rows[1:]:
        columns = row.strip().split(maxsplit=1)
        if (
            len(columns) != 2
            or not columns[0].isdigit()
            or int(columns[0]) <= 0
            or not columns[1].strip()
        ):
            raise ImageUpgradeError("Candidate process listing contains a malformed PID row.")
        process_id = int(columns[0])
        if process_id in process_ids:
            raise ImageUpgradeError("Candidate process listing contains a duplicate PID.")
        process_ids.add(process_id)
        process_commands.append(columns[1].strip())

    commands: dict[str, str] = {}
    for component in ("core", "ui"):
        pattern = re.compile(
            rf"/app/core/runtime_launcher\.py\s+{component}(?:\s|$)"
        )
        matches = [command for command in process_commands if pattern.search(command)]
        if len(matches) != 1:
            raise ImageUpgradeError(f"Expected one {component} runtime process.")
        command = matches[0]
        if "/config/channelwatch-runtime/releases/" in command:
            raise ImageUpgradeError("Candidate selected the historical app bundle.")
        if "/app/core/runtime_launcher.py" not in command:
            raise ImageUpgradeError("Candidate runtime process is not image-owned.")
        commands[component] = command
    return commands


def browser_smoke(port: int) -> str:
    script = """
const {chromium}=require(process.argv[2]);
(async()=>{const b=await chromium.launch({headless:true});try{const p=await b.newPage();const r=await p.goto(process.argv[1]);if(!r||!r.ok())throw new Error('request failed');await p.locator('[data-testid="auth-bootstrap-shell"],[data-testid="auth-login-shell"],[data-testid="auth-noauth-shell"]').first().waitFor({state:'visible'});console.log(JSON.stringify({title:await p.title(),hydrated:true}));}finally{await b.close();}})().catch(e=>{console.error(e.message);process.exit(2)});
""".strip()
    result = run(
        [
            shutil.which("node") or "node",
            "-e",
            script,
            f"http://127.0.0.1:{port}/",
            str(ROOT / "app/ui/node_modules/@playwright/test"),
        ],
        timeout=120,
    )
    evidence = json.loads(result.stdout)
    if evidence != {"title": "ChannelWatch", "hydrated": True}:
        raise ImageUpgradeError("Headless browser did not prove the hydrated UI.")
    return hashlib.sha256(result.stdout.encode()).hexdigest()


def candidate_python_version(inspection: dict[str, Any]) -> str:
    environment = inspection.get("Config", {}).get("Env")
    if not isinstance(environment, list) or any(
        not isinstance(value, str) for value in environment
    ):
        raise ImageUpgradeError("Candidate image Python version is invalid.")
    versions = [
        value.partition("=")[2]
        for value in environment
        if value.startswith("PYTHON_VERSION=")
    ]
    if versions != [IMAGE_PYTHON_VERSION]:
        raise ImageUpgradeError("Candidate image Python version is invalid.")
    return versions[0]


def validate_cli_runtime_info(
    output: str, *, candidate_version: str, expected_python_version: str
) -> dict[str, Any]:
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImageUpgradeError("Candidate runtime-info output is not valid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "diagnostic_source",
        "application_dir",
        "application_version",
        "process_runtime",
        "verified_generation",
    }:
        raise ImageUpgradeError("Candidate runtime-info fields are invalid.")
    process = payload.get("process_runtime")
    if not isinstance(process, dict) or set(process) != {
        "pid",
        "python_executable",
        "python_version",
        "python_prefix",
        "dependencies",
    }:
        raise ImageUpgradeError("Candidate runtime-info process fields are invalid.")
    dependencies = process.get("dependencies")
    expected_dependencies = {
        "requests",
        "cryptography",
        "sqlmodel",
        "uvicorn",
        "pydantic_core",
    }
    if not isinstance(dependencies, dict) or set(dependencies) != expected_dependencies:
        raise ImageUpgradeError("Candidate runtime-info dependency fields are invalid.")
    required_dependencies = {"cryptography", "sqlmodel", "pydantic_core"}
    for name, dependency in dependencies.items():
        if not isinstance(dependency, dict) or not isinstance(
            dependency.get("loaded"), bool
        ):
            raise ImageUpgradeError(
                f"Candidate runtime-info dependency {name} is invalid."
            )
        if not dependency["loaded"]:
            if set(dependency) != {"loaded"}:
                raise ImageUpgradeError(
                    f"Candidate runtime-info dependency {name} is invalid."
                )
            continue
        path = dependency.get("path")
        version = dependency.get("version")
        normalized_path = PurePosixPath(path) if isinstance(path, str) else None
        if (
            set(dependency) != {"loaded", "path", "version"}
            or not isinstance(path, str)
            or not path.startswith("/venv/")
            or ".." in normalized_path.parts
            or str(normalized_path) != path
            or not isinstance(version, str)
        ):
            raise ImageUpgradeError(
                f"Candidate runtime-info dependency {name} is not image-owned."
            )
    if any(dependencies[name]["loaded"] is not True for name in required_dependencies):
        raise ImageUpgradeError(
            "Candidate runtime-info mandatory image dependencies are not loaded."
        )
    if (
        payload.get("diagnostic_source") != "image"
        or payload.get("application_dir") != "/app"
        or payload.get("application_version") != candidate_version
        or payload.get("verified_generation") is not None
        or type(process.get("pid")) is not int
        or process["pid"] <= 0
        or process.get("python_executable") != "/venv/bin/python"
        or process.get("python_prefix") != "/venv"
        or process.get("python_version") != expected_python_version
    ):
        raise ImageUpgradeError(
            "Candidate runtime-info did not prove the immutable image CLI identity."
        )
    return payload


def verify_cli_runtime_info(
    container: str,
    *,
    candidate_version: str,
    expected_python_version: str,
    evidence_path: Path,
) -> dict[str, Any]:
    result = run(
        [
            "docker",
            "exec",
            container,
            "/usr/local/bin/channelwatch",
            "doctor",
            "runtime-info",
        ],
        timeout=60,
    )
    payload = validate_cli_runtime_info(
        result.stdout,
        candidate_version=candidate_version,
        expected_python_version=expected_python_version,
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    process = payload["process_runtime"]
    return {
        "sha256": sha256_file(evidence_path),
        "identity": {
            "diagnostic_source": payload["diagnostic_source"],
            "application_dir": payload["application_dir"],
            "application_version": payload["application_version"],
            "python_executable": process["python_executable"],
            "python_version": process["python_version"],
            "mandatory_dependencies": {
                name: process["dependencies"][name]["path"]
                for name in ("cryptography", "pydantic_core", "sqlmodel")
            },
            "verified_generation": payload["verified_generation"],
        },
    }


def validate_upgrade_state(
    *,
    old_container_id: str,
    replacement_container_id: str,
    restarted_container_id: str,
    status: dict[str, Any],
    candidate_version: str,
    historical_active: dict[str, Any],
    deactivated_active: dict[str, Any],
    before: dict[str, str],
    after: dict[str, str],
) -> None:
    if old_container_id == replacement_container_id:
        raise ImageUpgradeError("Image replacement did not create a new container.")
    if replacement_container_id != restarted_container_id:
        raise ImageUpgradeError("Same-image restart changed the replacement container ID.")
    worker_runtime = status.get("worker_runtime")
    if (
        status.get("current_version") != candidate_version
        or status.get("image_version") != candidate_version
        or status.get("runtime_source") != "image"
        or status.get("active_bundle") is not None
        or not isinstance(worker_runtime, dict)
        or worker_runtime.get("python_executable") != "/venv/bin/python"
        or worker_runtime.get("verified_generation") is not None
    ):
        raise ImageUpgradeError("Candidate did not select its immutable image runtime.")
    if (
        deactivated_active.get("deactivated_reason")
        != "image-version-is-current-or-newer"
        or any(deactivated_active.get(key) != value for key, value in historical_active.items())
    ):
        raise ImageUpgradeError("Candidate did not preserve the historical active identity.")
    if not before or before != after:
        raise ImageUpgradeError("Persistent application data changed across image replacement.")


def _docker_resource_is_missing(
    result: subprocess.CompletedProcess[str], kind: str, name: str
) -> bool:
    expected = name.casefold()
    messages = {
        message.strip().casefold()
        for message in (result.stdout or "", result.stderr or "")
        if message.strip()
    }
    markers = {
        "container": {
            f"error: no such object: {expected}",
            f"error: no such container: {expected}",
            f"error response from daemon: no such container: {expected}",
        },
        "volume": {
            f"error: no such volume: {expected}",
            f"error response from daemon: get {expected}: no such volume",
            f"error response from daemon: no such volume: {expected}",
        },
        "network": {
            f"error: no such network: {expected}",
            f"error response from daemon: network {expected} not found",
            f"error response from daemon: no such network: {expected}",
        },
        "image": {
            f"error: no such image: {expected}",
            f"error response from daemon: no such image: {expected}",
        },
    }
    return bool(messages & markers[kind])


class Resources:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.containers: set[str] = set()
        self.volumes: set[str] = set()
        self.networks: set[str] = set()
        self.images: set[str] = set()

    def container(self, suffix: str) -> str:
        value = f"{self.prefix}-{suffix}"
        self.containers.add(value)
        return value

    def create_volume(self) -> str:
        value = f"{self.prefix}-config"
        run(["docker", "volume", "create", "--label", LABEL, value])
        self.volumes.add(value)
        return value

    def create_network(self) -> str:
        value = f"{self.prefix}-network"
        run(["docker", "network", "create", "--label", LABEL, value])
        self.networks.add(value)
        return value

    def image(self) -> str:
        for _attempt in range(8):
            value = f"channelwatch-image-upgrade:{secrets.token_hex(8)}"
            inspection = run(
                ["docker", "image", "inspect", value],
                check=False,
                timeout=30,
            )
            if inspection.returncode and _docker_resource_is_missing(
                inspection, "image", value
            ):
                self.images.add(value)
                return value
            if inspection.returncode:
                raise ImageUpgradeError("Could not reserve a candidate image tag.")
        raise ImageUpgradeError("Could not allocate a unique candidate image tag.")

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        for name in sorted(self.containers):
            inspection = run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    f'{{{{index .Config.Labels "{LABEL_KEY}"}}}}',
                    name,
                ],
                check=False,
            )
            if inspection.returncode:
                if not _docker_resource_is_missing(inspection, "container", name):
                    failures.append(f"container:{name}:inspect")
                continue
            if inspection.stdout.strip() != LABEL_VALUE:
                failures.append(f"container:{name}:ownership")
                continue
            if run(
                ["docker", "rm", "-f", name], check=False
            ).returncode:
                failures.append(f"container:{name}")
        for kind, names in (("volume", self.volumes), ("network", self.networks)):
            for name in sorted(names):
                inspection = run(
                    [
                        "docker",
                        kind,
                        "inspect",
                        "--format",
                        f'{{{{index .Labels "{LABEL_KEY}"}}}}',
                        name,
                    ],
                    check=False,
                )
                if inspection.returncode:
                    if not _docker_resource_is_missing(inspection, kind, name):
                        failures.append(f"{kind}:{name}:inspect")
                    continue
                if inspection.stdout.strip() != LABEL_VALUE:
                    failures.append(f"{kind}:{name}:ownership")
                    continue
                if run(
                    ["docker", kind, "rm", name], check=False
                ).returncode:
                    failures.append(f"{kind}:{name}")
        for image in sorted(self.images):
            inspection = run(
                ["docker", "image", "inspect", image],
                check=False,
                timeout=30,
            )
            if inspection.returncode:
                if not _docker_resource_is_missing(inspection, "image", image):
                    failures.append(f"image:{image}:inspect")
                continue
            if run(["docker", "image", "rm", "-f", image], check=False).returncode:
                failures.append(f"image:{image}")
        return failures


def _load_tracked_candidate(
    args: argparse.Namespace,
    root: Path,
    resources: Resources,
) -> dict[str, str]:
    return _load_candidate(args, root, image_tag=resources.image())


def _seed_settings(reference: str, volume: str) -> None:
    settings = json.dumps(
        {
            "api_key": "",
            "auth_mode": "rbac",
            "rbac_enabled": True,
            "security_setup_completed": True,
        },
        sort_keys=True,
    )
    code = (
        "from pathlib import Path;"
        "p=Path('/config/settings.json');"
        f"p.write_text({settings!r},encoding='utf-8');p.chmod(0o600)"
    )
    run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--label",
            LABEL,
            "--user",
            "0:0",
            "--network",
            "none",
            "--mount",
            f"type=volume,source={volume},target=/config",
            "--entrypoint",
            "/venv/bin/python",
            reference,
            "-c",
            code,
        ],
        timeout=60,
    )


def _configure_encrypted_setting(client: ApiClient) -> None:
    settings = client.request("/api/settings")
    settings["dvr_servers"] = [
        {
            "id": "upgrade-canary",
            "name": "Upgrade Canary",
            "host": "example.com",
            "port": 8089,
            "enabled": False,
            "api_key": "synthetic-upgrade-secret",
        }
    ]
    client.request("/api/settings", settings)


def credential_api_semantics(client: ApiClient) -> bool:
    settings = client.request("/api/settings")
    servers = settings.get("dvr_servers")
    if not isinstance(servers, list) or len(servers) != 1:
        return False
    server = servers[0]
    expected = {
        "id": "upgrade-canary",
        "name": "Upgrade Canary",
        "host": "example.com",
        "port": 8089,
        "enabled": False,
        "api_key": "****",
    }
    return isinstance(server, dict) and all(
        server.get(key) == value for key, value in expected.items()
    )


def require_encrypted_dvr_api_keys_at_rest(client: ApiClient) -> None:
    status = client.request("/api/v1/security/status")
    if (
        not isinstance(status, dict)
        or status.get("encrypted_dvr_api_keys_at_rest") is not True
    ):
        raise ImageUpgradeError("Candidate did not retain usable encrypted settings.")


def _wait_job(
    client: ApiClient, *, version: str, timeout: int, baseline_image_version: str
) -> dict[str, Any]:
    requested_job_id = secrets.token_hex(16)
    server_assigns_job_id = baseline_image_version in SERVER_ASSIGNED_JOB_ID_IMAGES
    job_id = requested_job_id
    try:
        reply = client.request(
            "/api/v1/update/apply", {"version": version, "job_id": requested_job_id}
        )
        response_job_id = reply.get("job_id")
        if not isinstance(response_job_id, str) or not JOB_ID.fullmatch(
            response_job_id
        ):
            raise ImageUpgradeError("Apply response returned an invalid job identity.")
        if server_assigns_job_id:
            job_id = response_job_id
        elif response_job_id != requested_job_id:
            raise ImageUpgradeError("Apply response did not bind the requested job.")
    except (TimeoutError, ConnectionError, urllib.error.URLError) as exc:
        if server_assigns_job_id:
            raise ImageUpgradeError(
                "Legacy baseline Apply response was lost; job identity cannot be recovered."
            ) from exc

    def complete():
        try:
            job = client.request(f"/api/v1/update/jobs/{job_id}")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 404, 503}:
                if exc.code == 401:
                    client.login()
                return None
            raise
        if job.get("job_id") != job_id:
            raise ImageUpgradeError("Baseline job identity changed.")
        if job.get("status") in {"failed", "rolled_back", "cancelled", "rejected"}:
            raise ImageUpgradeError("Baseline signed app update failed.")
        return job if job.get("status") == "success" else None

    result = wait_until(complete, timeout, f"baseline job {job_id}")
    if result.get("job_id") != job_id:
        raise ImageUpgradeError("Baseline job identity changed.")
    return result


def _write_feed(root: Path, catalog: Path, bundle_url: str, bundle: Path) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    capability = _load_script(CAPABILITY_VERIFIER, "image_upgrade_feed")
    capability.LABEL = LABEL
    ca_path, _cert, _key = capability.create_tls_material(root)
    capability.write_feed(
        root,
        {LEGACY_CATALOG_URL: catalog.resolve(), bundle_url: bundle.resolve()},
    )
    return ca_path


def _verify_feed_catalog(container: str, expected_sha256: str, timeout: int) -> str:
    if not SHA256.fullmatch(expected_sha256):
        raise ImageUpgradeError("Expected signed catalog digest is invalid.")
    code = (
        "import hashlib,sys,urllib.request;"
        "data=urllib.request.urlopen(sys.argv[1],timeout=10).read();"
        "print(hashlib.sha256(data).hexdigest())"
    )

    result = run(
        [
            "docker",
            "exec",
            container,
            "/usr/local/bin/python",
            "-c",
            code,
            LEGACY_CATALOG_URL,
        ],
        check=False,
        timeout=min(timeout, 30),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "probe failed").strip()
        raise ImageUpgradeError(f"Baseline HTTPS feed readiness probe failed: {detail[:500]}")
    observed = result.stdout.strip()
    if not SHA256.fullmatch(observed) or observed != expected_sha256:
        raise ImageUpgradeError(
            "Baseline HTTPS feed did not return the exact signed catalog bytes."
        )
    return observed


def _capture_logs(
    containers: set[str], output: Path
) -> tuple[dict[str, str], list[str], list[dict[str, Any]]]:
    output.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    empty_containers: list[str] = []
    failures: list[dict[str, Any]] = []
    for name in sorted(containers):
        path = output / f"{name}.log"
        try:
            result = run(["docker", "logs", name], check=False, timeout=30)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            path.write_text(
                f"[stdout]\n{stdout}\n[stderr]\n{stderr}",
                encoding="utf-8",
            )
            if result.returncode != 0:
                failures.append(
                    {
                        "container": name,
                        "returncode": result.returncode,
                        "failure_summary": (stderr or stdout or "docker logs failed")
                        .strip()[:500],
                    }
                )
            elif not stdout.strip() and not stderr.strip():
                empty_containers.append(name)
        except Exception as exc:
            summary = str(exc)[:500]
            path.write_text(
                f"[capture-error]\n{type(exc).__name__}: {summary}\n",
                encoding="utf-8",
            )
            failures.append(
                {
                    "container": name,
                    "failure_type": type(exc).__name__,
                    "failure_summary": summary,
                }
            )
        hashes[name] = sha256_file(path)
    return hashes, empty_containers, failures


def _record_primary_failure(receipt: dict[str, Any], exc: Exception) -> None:
    receipt["status"] = "failed"
    if "failure_type" not in receipt:
        receipt["failure_type"] = type(exc).__name__
        receipt["failure_summary"] = str(exc)[:1000]


def qualify(args: argparse.Namespace) -> dict[str, Any]:
    architecture = native_architecture(args.architecture)
    release, bundle_url = verify_signed_baseline_assets(
        args.baseline_catalog,
        args.baseline_bundle,
        version=args.baseline_app_version,
        source_commit=args.baseline_app_source_commit,
    )
    baseline_reference, baseline_manifest, baseline_image_id = _native_manifest(
        args.baseline_repository,
        args.baseline_index_digest,
        architecture,
        expected_manifest_digest=args.baseline_manifest_digest,
        expected_version=args.baseline_image_version,
        expected_source_commit=args.baseline_source_commit,
    )
    prefix = f"cw-image-upgrade-{architecture}-{secrets.token_hex(5)}"
    resources = Resources(prefix)
    root = Path(tempfile.mkdtemp(prefix=prefix + "-"))
    receipt: dict[str, Any] = {
        "schema": 1,
        "status": "running",
        "architecture": architecture,
        "source_commit": args.source_commit,
        "candidate_version": args.candidate_version,
        "baseline": {
            "repository": args.baseline_repository,
            "image_version": args.baseline_image_version,
            "image_index_digest": args.baseline_index_digest,
            "platform_manifest_digest": baseline_manifest,
            "image_id": baseline_image_id,
            "source_commit": args.baseline_source_commit,
            "signed_app_version": args.baseline_app_version,
            "signed_app_source_commit": args.baseline_app_source_commit,
            "signed_catalog_sha256": sha256_file(args.baseline_catalog),
            "signed_bundle_sha256": sha256_file(args.baseline_bundle),
            "bundle_url": bundle_url,
        },
    }
    old = resources.container("old")
    candidate = resources.container("candidate")
    feed = resources.container("feed")
    external_key = root / "external-storage-key"
    external_key.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    # The task-owned parent is private.  The file itself must remain readable
    # after Docker bind-mounts it into the UID 1000 application container.
    external_key.chmod(0o444)
    expected_external_hash = sha256_file(external_key)
    try:
        candidate_image = _load_tracked_candidate(args, root, resources)
        candidate_image["oci_archive_sha256"] = args.expected_archive_sha256
        candidate_image["descriptor_sha256"] = args.expected_descriptor_sha256
        receipt["candidate"] = candidate_image
        network = resources.create_network()
        volume = resources.create_volume()
        ca_path = _write_feed(root / "feed", args.baseline_catalog, bundle_url, args.baseline_bundle)
        _seed_settings(baseline_reference, volume)
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                feed,
                "--label",
                LABEL,
                "--network",
                network,
                "--network-alias",
                "github.com",
                "--network-alias",
                "channelwatch.coderluii.dev",
                "--mount",
                f"type=bind,source={root / 'feed'},target=/feed,readonly",
                "--entrypoint",
                "/usr/local/bin/python",
                "--pull=never",
                candidate_image["tag"],
                "/feed/feed.py",
            ],
            timeout=60,
        )
        username = "upgrade-admin"
        password = secrets.token_urlsafe(36)
        common = [
            "--label",
            LABEL,
            "--network",
            network,
            "--platform",
            f"linux/{architecture}",
            "--mount",
            f"type=volume,source={volume},target=/config",
            "--mount",
            f"type=bind,source={external_key},target=/run/secrets/channelwatch-storage-key,readonly",
            "--mount",
            f"type=bind,source={ca_path},target=/canary/ca.pem,readonly",
            "--env",
            "CHANNELWATCH_SECRET_STORAGE_KEY_FILE=/run/secrets/channelwatch-storage-key",
            "--env",
            "SSL_CERT_FILE=/canary/ca.pem",
            "--env",
            f"CW_ADMIN_USER={username}",
            "--env",
            f"CW_ADMIN_PASS={password}",
            "-p",
            "127.0.0.1::8501",
        ]
        run(
            [
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--name",
                old,
                *common,
                baseline_reference,
            ]
        )
        old_client = ApiClient(published_base_url(old), username, password)
        wait_until(
            lambda: old_client.request("/healthz/startup").get("status") == "ready",
            args.startup_timeout,
            "baseline startup",
        )
        receipt["baseline"]["feed_catalog_sha256_verified"] = _verify_feed_catalog(
            old,
            receipt["baseline"]["signed_catalog_sha256"],
            args.startup_timeout,
        )
        old_client.login()
        _configure_encrypted_setting(old_client)
        if not encrypted_settings_usable(old):
            raise ImageUpgradeError("Baseline did not persist an encrypted DVR credential.")
        checked = old_client.request("/api/v1/update/check", {})
        if (
            checked.get("update_available") is not True
            or checked.get("trusted_target", {}).get("version")
            != args.baseline_app_version
        ):
            raise ImageUpgradeError("Baseline did not select the signed app update.")
        baseline_job = _wait_job(
            old_client,
            version=args.baseline_app_version,
            timeout=args.update_timeout,
            baseline_image_version=args.baseline_image_version,
        )
        old_client.set_base_url(published_base_url(old))
        wait_until(
            lambda: old_client.request("/api/v1/update/status").get("current_version")
            == args.baseline_app_version,
            args.update_timeout,
            "protocol-3 app activation",
        )
        old_client.login()
        old_status = old_client.request("/api/v1/update/status")
        baseline_encrypted_settings = encrypted_settings_usable(old)
        if not baseline_encrypted_settings:
            raise ImageUpgradeError(
                "Baseline activation could not decrypt the DVR credential."
            )
        baseline_credential_semantics = credential_api_semantics(old_client)
        if not baseline_credential_semantics:
            raise ImageUpgradeError(
                "Baseline activation did not retain the masked DVR credential."
            )
        active = old_status.get("active_bundle")
        if (
            old_status.get("launcher_protocol") != 3
            or not isinstance(active, dict)
            or active.get("version") != args.baseline_app_version
        ):
            raise ImageUpgradeError("Baseline did not retain a healthy protocol-3 active app.")
        before = persistent_hashes(old)
        old_control = control_inventory(old)
        if "active.json" not in old_control or "rollback.json" not in old_control:
            raise ImageUpgradeError("Baseline control state is incomplete.")
        if "activation-pending.json" in old_control:
            raise ImageUpgradeError("Baseline activation is still pending.")
        if external_key_hash(old) != expected_external_hash:
            raise ImageUpgradeError("Baseline external key configuration changed.")
        old_id = run(["docker", "inspect", "--format", "{{.Id}}", old]).stdout.strip()
        run(["docker", "stop", old], timeout=120)
        run(
            [
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--name",
                candidate,
                *common,
                candidate_image["tag"],
            ]
        )
        candidate_client = ApiClient(published_base_url(candidate), username, password)
        wait_until(
            lambda: candidate_client.request("/healthz/startup").get("status") == "ready",
            args.startup_timeout,
            "candidate startup on old volume",
        )
        candidate_client.login()
        candidate_status = candidate_client.request("/api/v1/update/status")
        replacement_credential_semantics = credential_api_semantics(candidate_client)
        if not replacement_credential_semantics:
            raise ImageUpgradeError(
                "Image replacement did not retain the masked DVR credential."
            )
        replacement_id = run(
            ["docker", "inspect", "--format", "{{.Id}}", candidate]
        ).stdout.strip()
        commands = image_process_commands(candidate)
        after_replace = persistent_hashes(candidate)
        if external_key_hash(candidate) != expected_external_hash:
            raise ImageUpgradeError("Candidate external key configuration changed.")
        require_encrypted_dvr_api_keys_at_rest(candidate_client)
        replacement_encrypted_settings = encrypted_settings_usable(candidate)
        if not replacement_encrypted_settings:
            raise ImageUpgradeError("Candidate could not read the encrypted DVR credential.")
        cli = run(
            ["docker", "exec", candidate, "/usr/local/bin/channelwatch", "doctor", "config-check"],
            timeout=60,
        )
        replacement_cli_runtime = verify_cli_runtime_info(
            candidate,
            candidate_version=args.candidate_version,
            expected_python_version=candidate_image["python_version"],
            evidence_path=args.log_directory
            / "candidate-runtime-info-after-replace.json",
        )
        run(["docker", "restart", candidate], timeout=120)
        candidate_client.set_base_url(published_base_url(candidate))
        wait_until(
            lambda: candidate_client.request("/healthz/startup").get("status") == "ready",
            args.startup_timeout,
            "candidate same-container restart",
        )
        candidate_client.login()
        restarted_status = candidate_client.request("/api/v1/update/status")
        restart_credential_semantics = credential_api_semantics(candidate_client)
        if not restart_credential_semantics:
            raise ImageUpgradeError(
                "Candidate restart did not retain the masked DVR credential."
            )
        restart_encrypted_settings = encrypted_settings_usable(candidate)
        if not restart_encrypted_settings:
            raise ImageUpgradeError(
                "Candidate restart could not read the encrypted DVR credential."
            )
        restarted_cli_runtime = verify_cli_runtime_info(
            candidate,
            candidate_version=args.candidate_version,
            expected_python_version=candidate_image["python_version"],
            evidence_path=args.log_directory
            / "candidate-runtime-info-after-restart.json",
        )
        resolved_deactivated_active = deactivated_active(candidate)
        browser_output_sha256 = browser_smoke(
            int(candidate_client.base_url.rsplit(":", 1)[1])
        )
        restarted_id = run(
            ["docker", "inspect", "--format", "{{.Id}}", candidate]
        ).stdout.strip()
        after_restart = persistent_hashes(candidate)
        validate_upgrade_state(
            old_container_id=old_id,
            replacement_container_id=replacement_id,
            restarted_container_id=restarted_id,
            status=restarted_status,
            candidate_version=args.candidate_version,
            historical_active=active,
            deactivated_active=resolved_deactivated_active,
            before=before,
            after=after_restart,
        )
        if after_replace != before or external_key_hash(candidate) != expected_external_hash:
            raise ImageUpgradeError("Replacement did not preserve persistent identities.")
        receipt.update(
            {
                "status": "passed",
                "baseline_job_id": baseline_job["job_id"],
                "old_container_id": old_id,
                "replacement_container_id": replacement_id,
                "restarted_container_id": restarted_id,
                "old_status": old_status,
                "candidate_status": restarted_status,
                "old_control_inventory": old_control,
                "resolved_control_inventory": control_inventory(candidate),
                "deactivated_active": resolved_deactivated_active,
                "image_process_commands": commands,
                "preservation_before_sha256": before,
                "preservation_after_replace_sha256": after_replace,
                "preservation_after_restart_sha256": after_restart,
                "external_storage_key_sha256": expected_external_hash,
                "cli_output_sha256": hashlib.sha256(cli.stdout.encode()).hexdigest(),
                "cli_runtime_info": {
                    "after_replace": replacement_cli_runtime,
                    "after_restart": restarted_cli_runtime,
                },
                "browser_output_sha256": browser_output_sha256,
                "api_health": True,
                "headless_browser": True,
                "cli": True,
                "runtime_ready": True,
                "different_container_on_image_upgrade": True,
                "same_container_on_candidate_restart": True,
                "encrypted_settings_usable": (
                    baseline_encrypted_settings
                    and replacement_encrypted_settings
                    and restart_encrypted_settings
                ),
                "credential_api_semantics": {
                    "after_baseline": baseline_credential_semantics,
                    "after_replace": replacement_credential_semantics,
                    "after_restart": restart_credential_semantics,
                },
            }
        )
    except Exception as exc:
        _record_primary_failure(receipt, exc)
    finally:
        try:
            log_hashes, empty_logs, log_failures = _capture_logs(
                resources.containers, args.log_directory
            )
            receipt["logs_sha256"] = log_hashes
            if empty_logs or log_failures:
                reasons = []
                if log_failures:
                    reasons.append(
                        f"{len(log_failures)} command failure"
                        + ("s" if len(log_failures) != 1 else "")
                    )
                if empty_logs:
                    reasons.append(
                        f"{len(empty_logs)} empty log"
                        + ("s" if len(empty_logs) != 1 else "")
                    )
                log_error = ImageUpgradeError(
                    "Container log evidence failed: " + "; ".join(reasons) + "."
                )
                receipt["log_capture"] = {
                    "status": "failed",
                    "empty_containers": empty_logs,
                    "command_failures": log_failures,
                    "failure_type": type(log_error).__name__,
                    "failure_summary": str(log_error)[:1000],
                }
                _record_primary_failure(receipt, log_error)
            else:
                receipt["log_capture"] = {
                    "status": "passed",
                    "empty_containers": [],
                    "command_failures": [],
                }
        except Exception as exc:
            receipt["log_capture"] = {
                "status": "failed",
                "failure_type": type(exc).__name__,
                "failure_summary": str(exc)[:1000],
            }
            _record_primary_failure(receipt, exc)
        cleanup = resources.cleanup()
        shutil.rmtree(root, ignore_errors=True)
        receipt["cleanup"] = {"status": "passed" if not cleanup else "failed", "failures": cleanup}
        if cleanup:
            _record_primary_failure(
                receipt,
                ImageUpgradeError("Owned Docker resources could not be removed."),
            )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-oci-archive", type=Path, required=True)
    parser.add_argument("--candidate-descriptor", type=Path, required=True)
    parser.add_argument("--expected-archive-sha256", required=True)
    parser.add_argument("--expected-descriptor-sha256", required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--skopeo-image", required=True)
    parser.add_argument("--baseline-repository", default="coderluii/channelwatch")
    parser.add_argument("--baseline-image-version", required=True)
    parser.add_argument("--baseline-source-commit", required=True)
    parser.add_argument("--baseline-index-digest", required=True)
    parser.add_argument("--baseline-manifest-digest", required=True)
    parser.add_argument("--baseline-app-version", required=True)
    parser.add_argument("--baseline-app-source-commit", required=True)
    parser.add_argument("--baseline-catalog", type=Path, required=True)
    parser.add_argument("--baseline-bundle", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--log-directory", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=240)
    parser.add_argument("--update-timeout", type=int, default=600)
    args = parser.parse_args()
    args.baseline_image_version = args.baseline_image_version.strip().lstrip("v")
    args.baseline_app_version = args.baseline_app_version.strip().lstrip("v")
    args.candidate_version = args.candidate_version.strip().lstrip("v")
    receipt: dict[str, Any]
    try:
        if not SHA256.fullmatch(args.expected_archive_sha256):
            raise ImageUpgradeError("Expected archive digest must be lowercase SHA-256.")
        if not SHA256.fullmatch(args.expected_descriptor_sha256):
            raise ImageUpgradeError("Expected descriptor digest must be lowercase SHA-256.")
        if not SHA256_PREFIXED.fullmatch(args.baseline_index_digest):
            raise ImageUpgradeError("Baseline index digest is invalid.")
        if not SHA256_PREFIXED.fullmatch(args.baseline_manifest_digest):
            raise ImageUpgradeError("Baseline native manifest digest is invalid.")
        if not SHA40.fullmatch(args.source_commit):
            raise ImageUpgradeError("Source commit must be a complete lowercase Git SHA.")
        if not SHA40.fullmatch(args.baseline_source_commit):
            raise ImageUpgradeError("Baseline source commit must be a complete lowercase Git SHA.")
        if not SHA40.fullmatch(args.baseline_app_source_commit):
            raise ImageUpgradeError(
                "Baseline app source commit must be a complete lowercase Git SHA."
            )
        receipt = qualify(args)
    except Exception as exc:
        receipt = {
            "schema": 1,
            "status": "failed",
            "architecture": args.architecture,
            "candidate_version": args.candidate_version,
            "source_commit": args.source_commit,
            "failure_type": type(exc).__name__,
            "failure_summary": str(exc)[:1000],
            "cleanup": {"status": "not-started", "failures": []},
        }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if receipt.get("status") != "passed":
        print("Native image-upgrade qualification failed.", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
