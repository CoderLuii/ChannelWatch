#!/usr/bin/env python3
"""Prove one native protocol-4 runtime update through the public HTTP API."""

from __future__ import annotations

import argparse
import base64
import datetime as datetime_module
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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ASSET_VERIFIER = ROOT / "scripts/release/verify-update-assets.py"
DEFAULT_CATALOG_URL = "https://channelwatch.coderluii.dev/updates/v3/stable.json"
LABEL = "codex.task=channelwatch-runtime-capability"
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
TERMINAL_FAILURES = {"failed", "rolled_back", "cancelled", "rejected"}
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
HEX_RE = re.compile(r"[0-9a-f]{64}")


class CapabilityError(RuntimeError):
    """Raised when native protocol-4 qualification does not hold."""


def run(
    command: list[str], *, check: bool = True, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # nosec B603
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise CapabilityError(f"{command[0]} failed: {detail[:500]}")
    return result


def run_bytes(command: list[str], *, timeout: int | None = None) -> bytes:
    result = subprocess.run(  # nosec B603
        command,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"command failed")[:500].decode(
            "utf-8", errors="replace"
        )
        raise CapabilityError(f"{command[0]} failed: {detail.strip()}")
    return result.stdout


def _load_asset_verifier():
    spec = importlib.util.spec_from_file_location("runtime_capability_assets", ASSET_VERIFIER)
    if spec is None or spec.loader is None:
        raise CapabilityError("The runtime asset verifier could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_assets(
    catalog_path: Path,
    bundles: dict[str, Path],
    *,
    version: str,
    source_commit: str,
) -> tuple[bytes, dict[str, Any], dict[str, dict[str, Any]]]:
    catalog_bytes = catalog_path.read_bytes()
    try:
        raw = json.loads(catalog_bytes.decode("utf-8"))
        releases = raw["payload"]["releases"]
        release = next(
            item
            for item in releases
            if isinstance(item, dict) and item.get("version") == version
        )
        variants = release["runtime_variants"]
        by_arch = {
            item["runtime_descriptor"]["architecture"]: item for item in variants
        }
        urls = {architecture: by_arch[architecture]["bundle_url"] for architecture in ("amd64", "arm64")}
    except (UnicodeError, json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
        raise CapabilityError("The signed runtime catalog has no unique two-architecture target.") from exc
    if set(by_arch) != {"amd64", "arm64"} or set(bundles) != {"amd64", "arm64"}:
        raise CapabilityError("Qualification requires exactly amd64 and arm64 assets.")
    verifier = _load_asset_verifier()
    verified = verifier.verify_runtime_update_catalog(
        catalog_bytes,
        {name: bundles[name].read_bytes() for name in ("amd64", "arm64")},
        expected_version=version,
        expected_source_commit=source_commit,
        expected_bundle_urls=urls,
    )
    if verified != raw:
        raise CapabilityError("The asset verifier did not return the exact signed catalog.")
    return catalog_bytes, release, by_arch


def native_architecture(requested: str | None = None) -> str:
    host = ARCHITECTURES.get(platform.machine().lower())
    docker = ARCHITECTURES.get(
        run(["docker", "info", "--format", "{{.Architecture}}"], timeout=30)
        .stdout.strip()
        .lower()
    )
    if host is None or docker is None or host != docker:
        raise CapabilityError("Host and Docker server must expose the same native architecture.")
    if requested is not None and requested != host:
        raise CapabilityError("Requested qualification architecture is not host-native.")
    return host


def pinned_native_image(
    repository: str, index_digest: str, architecture: str
) -> tuple[str, str, str, str, dict[str, Any]]:
    if SHA256_RE.fullmatch(index_digest) is None:
        raise CapabilityError("Bootstrap image index digest is invalid.")
    raw = run_bytes(
        ["docker", "buildx", "imagetools", "inspect", "--raw", f"{repository}@{index_digest}"],
        timeout=120,
    )
    if "sha256:" + hashlib.sha256(raw).hexdigest() != index_digest:
        raise CapabilityError("Bootstrap image index bytes do not match the pinned digest.")
    try:
        index = json.loads(raw.decode("utf-8"))
        matches = [
            item
            for item in index["manifests"]
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == architecture
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CapabilityError("Bootstrap reference is not a valid multi-platform index.") from exc
    if len(matches) != 1 or SHA256_RE.fullmatch(str(matches[0].get("digest") or "")) is None:
        raise CapabilityError("Bootstrap index has no unique native Linux manifest.")
    manifest_digest = str(matches[0]["digest"])
    reference = f"{repository}@{manifest_digest}"
    run(["docker", "pull", "--platform", f"linux/{architecture}", reference], timeout=300)
    inspected = json.loads(run(["docker", "image", "inspect", reference]).stdout)
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise CapabilityError("Pulled bootstrap image inspection is invalid.")
    image = inspected[0]
    if (
        image.get("Os") != "linux"
        or image.get("Architecture") != architecture
        or SHA256_RE.fullmatch(str(image.get("Id") or "")) is None
        or reference not in image.get("RepoDigests", [])
    ):
        raise CapabilityError("Pulled bootstrap image is not the selected native manifest.")
    inspection = {
        "os": image["Os"],
        "architecture": image["Architecture"],
        "repo_digests": image["RepoDigests"],
    }
    return (
        reference,
        manifest_digest,
        str(image["Id"]),
        base64.b64encode(raw).decode("ascii"),
        inspection,
    )


def validate_update_check(
    checked: object,
    *,
    version: str,
    descriptor: dict[str, Any],
    bundle_sha256: str,
) -> dict[str, Any]:
    if not isinstance(checked, dict):
        raise CapabilityError("Update check did not select the exact signed runtime target.")
    target = checked.get("trusted_target")
    if (
        checked.get("update_available") is not True
        or checked.get("image_required") is not False
        or checked.get("catalog_state") != "update_available"
        or not isinstance(target, dict)
        or target.get("version") != version
        or target.get("runtime_descriptor") != descriptor
        or target.get("bundle_sha256") != bundle_sha256
    ):
        raise CapabilityError("Update check did not select the exact signed runtime target.")
    return checked


def validate_rollback_response(
    response: object, *, source_version: str, target_version: str
) -> dict[str, Any]:
    del target_version  # The durable post-rollback status binds the image floor.
    if (
        not isinstance(response, dict)
        or response.get("operation") != "rollback"
        or response.get("status") not in {"restarting", "succeeded"}
        or response.get("rolled_back_from") != source_version
        or not isinstance(response.get("job_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", response["job_id"]) is None
    ):
        raise CapabilityError("API rollback response is not bound to the selected runtime.")
    return response


def validate_rollback_status(
    status: object, *, target_version: str
) -> dict[str, Any]:
    if (
        not isinstance(status, dict)
        or status.get("current_version") != target_version
        or status.get("launcher_protocol") != 4
        or status.get("active_bundle") is not None
        or status.get("operation_busy") is not False
    ):
        raise CapabilityError("Durable post-rollback status is invalid.")
    return status


def validate_log_hashes(hashes: object) -> dict[str, str]:
    if (
        not isinstance(hashes, dict)
        or set(hashes) != {"app", "feed"}
        or any(
            not isinstance(value, str) or HEX_RE.fullmatch(value) is None
            for value in hashes.values()
        )
    ):
        raise CapabilityError("Native evidence requires exact nonempty app and feed logs.")
    return hashes


def validate_runtime_components(
    job: dict[str, Any], descriptor: dict[str, Any]
) -> dict[str, Any]:
    components = job.get("runtime_components")
    if not isinstance(components, dict) or set(components) != {"core", "ui"}:
        raise CapabilityError("Successful update did not retain both runtime component receipts.")
    expected_identity = {
        "runtime_generation_id": descriptor["generation_id"],
        "runtime_descriptor_sha256": descriptor["generation_id"],
        "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
    }
    generation_root = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
    expected_environment = f"{generation_root}/environment"
    expected_site_packages = f"{generation_root}/dependencies/site-packages/"
    for component, value in components.items():
        if not isinstance(value, dict) or any(
            value.get(name) != expected for name, expected in expected_identity.items()
        ):
            raise CapabilityError(f"{component} runtime identity does not match the signed descriptor.")
        process = value.get("process_runtime")
        dependencies = process.get("dependencies") if isinstance(process, dict) else None
        if (
            not isinstance(process, dict)
            or type(process.get("pid")) is not int
            or process["pid"] <= 0
            or process.get("python_version") != descriptor["python_version"]
            or process.get("python_executable") != f"{expected_environment}/bin/python"
            or process.get("python_prefix") != expected_environment
            or not isinstance(dependencies, dict)
        ):
            raise CapabilityError(f"{component} process diagnostics are missing.")
        for required in ("requests", "cryptography"):
            record = dependencies.get(required)
            if not isinstance(record, dict) or record.get("loaded") is not True:
                raise CapabilityError(f"{component} did not load required dependency {required}.")
        for name, record in dependencies.items():
            if not isinstance(record, dict):
                raise CapabilityError(f"{component} dependency diagnostic {name} is invalid.")
            if record.get("loaded") is True and not str(record.get("path") or "").startswith(expected_site_packages):
                raise CapabilityError(f"{component} loaded {name} outside the selected generation.")
    return components


def validate_preservation(before: dict[str, str], after: dict[str, str]) -> None:
    if not before or before != after:
        raise CapabilityError("Persistent settings, managed key, or image venv identity changed.")


class DockerResources:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.containers: set[str] = set()
        self.volumes: set[str] = set()
        self.networks: set[str] = set()

    def create_network(self) -> str:
        name = f"{self.prefix}-network"
        run(["docker", "network", "create", "--label", LABEL, name])
        self.networks.add(name)
        return name

    def create_volume(self) -> str:
        name = f"{self.prefix}-config"
        run(["docker", "volume", "create", "--label", LABEL, name])
        self.volumes.add(name)
        return name

    def container(self, suffix: str) -> str:
        name = f"{self.prefix}-{suffix}"
        self.containers.add(name)
        return name

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        for name in sorted(self.containers):
            if run(["docker", "inspect", "--format", '{{index .Config.Labels "codex.task"}}', name], check=False).stdout.strip() == LABEL.split("=", 1)[1]:
                if run(["docker", "rm", "-f", name], check=False).returncode != 0:
                    failures.append(f"container:{name}")
        for kind, names in (("volume", self.volumes), ("network", self.networks)):
            for name in sorted(names):
                if run(["docker", kind, "inspect", "--format", '{{index .Labels "codex.task"}}', name], check=False).stdout.strip() == LABEL.split("=", 1)[1]:
                    if run(["docker", kind, "rm", name], check=False).returncode != 0:
                        failures.append(f"{kind}:{name}")
        return failures


def create_tls_material(root: Path) -> tuple[Path, Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime_module.datetime.now(datetime_module.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ChannelWatch capability CA")])
    ca_subject_key_identifier = x509.SubjectKeyIdentifier.from_public_key(
        ca_key.public_key()
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime_module.timedelta(minutes=5))
        .not_valid_after(now + datetime_module.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ca_subject_key_identifier, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_subject_key_identifier
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "channelwatch.coderluii.dev")])
    server = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime_module.timedelta(minutes=5))
        .not_valid_after(now + datetime_module.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("channelwatch.coderluii.dev"), x509.DNSName("github.com")]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_subject_key_identifier
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = root / "ca.pem", root / "server.pem", root / "server-key.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return ca_path, cert_path, key_path


def write_feed(root: Path, routes: dict[str, Path]) -> Path:
    route_map: dict[str, str] = {}
    assets = root / "assets"
    assets.mkdir(parents=True)
    for index, (url, source) in enumerate(sorted(routes.items())):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "channelwatch.coderluii.dev"}:
            raise CapabilityError("Signed feed URL is not an approved HTTPS origin.")
        key = f"asset-{index}"
        shutil.copyfile(source, assets / key)
        route_map[f"{parsed.hostname}{parsed.path}"] = key
    script = root / "feed.py"
    script.write_text(
        "import http.server,json,ssl\n"
        "from pathlib import Path\n"
        f"ROUTES={route_map!r}\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  key=ROUTES.get(self.headers.get('Host','').split(':',1)[0]+self.path.split('?',1)[0])\n"
        "  if key is None: self.send_error(404); return\n"
        "  data=(Path('/feed/assets')/key).read_bytes(); self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)\n"
        " def log_message(self,fmt,*args): print(fmt%args,flush=True)\n"
        "server=http.server.ThreadingHTTPServer(('0.0.0.0',443),H)\n"
        "ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain('/feed/server.pem','/feed/server-key.pem');server.socket=ctx.wrap_socket(server.socket,server_side=True);server.serve_forever()\n",
        encoding="utf-8",
    )
    return script


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

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        body = json.dumps(payload).encode() if payload is not None else None
        with self.opener.open(
            urllib.request.Request(self.base_url + path, data=body, headers=headers), timeout=20
        ) as response:
            return json.loads(response.read() or b"{}")

    def login(self) -> None:
        response = self.request(
            "/api/v1/auth/login", {"username": self.username, "password": self.password}
        )
        self.csrf = str(response.get("csrf_token") or "")
        if not self.csrf:
            raise CapabilityError("Synthetic administrator login did not return CSRF state.")


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
    raise CapabilityError(f"Timed out waiting for {description}: {type(last).__name__ if last else 'no matching state'}")


def published_base_url(name: str) -> str:
    output = run(["docker", "port", name, "8501/tcp"]).stdout.strip()
    try:
        port = int(output.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise CapabilityError("Docker did not publish the ChannelWatch HTTP port.") from exc
    if not 1 <= port <= 65535:
        raise CapabilityError("Docker published an invalid ChannelWatch HTTP port.")
    return f"http://127.0.0.1:{port}"


def apply_once_and_wait(
    client: ApiClient,
    endpoint: str,
    *,
    version: str,
    job_id: str,
    timeout: int,
) -> dict[str, Any]:
    try:
        response = client.request(endpoint, {"version": version, "job_id": job_id})
        if response.get("job_id") != job_id:
            raise CapabilityError("Apply response did not bind the requested job ID.")
    except (TimeoutError, ConnectionError, urllib.error.URLError):
        pass

    def completed():
        try:
            job = client.request(f"/api/v1/update/jobs/{job_id}")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 404, 503}:
                if exc.code == 401:
                    client.login()
                return None
            raise
        status = str(job.get("status") or "")
        if status in TERMINAL_FAILURES:
            raise CapabilityError(f"Runtime update job failed: {status}")
        return job if status == "success" else None

    return wait_until(completed, timeout, f"job {job_id}")


def docker_exec_json(name: str, code: str) -> dict[str, Any]:
    result = run(["docker", "exec", name, "/usr/local/bin/python", "-c", code], timeout=60)
    return json.loads(result.stdout.strip().splitlines()[-1])


def preservation_hashes(name: str) -> dict[str, str]:
    code = """
import hashlib,json
from pathlib import Path
paths=[Path('/config/settings.json'),Path('/config/encryption.key'),Path('/venv/pyvenv.cfg'),Path('/venv/bin/python')]
out={}
for path in paths:
 if not path.exists(): raise SystemExit('missing preservation path: '+str(path))
 out[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(out,sort_keys=True))
"""
    return {str(key): str(value) for key, value in docker_exec_json(name, code).items()}


def native_import_smoke(name: str, generation_path: str) -> dict[str, Any]:
    code = f"""
import json,subprocess
python={generation_path!r}+'/environment/bin/python'
site={generation_path!r}+'/dependencies/site-packages'
snippet='import json,sys;sys.path.insert(0,'+repr(site)+');import requests,cryptography,sqlmodel,uvicorn,pydantic_core;print(json.dumps({{n:{{"path":m.__file__,"version":getattr(m,"__version__","")}} for n,m in [("requests",requests),("cryptography",cryptography),("sqlmodel",sqlmodel),("uvicorn",uvicorn),("pydantic_core",pydantic_core)]}}))'
result=subprocess.run([python,'-I','-B','-S','-c',snippet],capture_output=True,text=True,check=True)
print(result.stdout)
"""
    result = docker_exec_json(name, code)
    if set(result) != {"requests", "cryptography", "sqlmodel", "uvicorn", "pydantic_core"}:
        raise CapabilityError("Fresh generation interpreter import smoke is incomplete.")
    expected_site_packages = f"{generation_path}/dependencies/site-packages/"
    for module, record in result.items():
        if not str(record.get("path") or "").startswith(expected_site_packages):
            raise CapabilityError(f"Fresh interpreter loaded {module} outside generation dependencies.")
    return result


def seed_synthetic_rbac_settings(reference: str, volume: str) -> None:
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
        "from pathlib import Path; "
        "p=Path('/config/settings.json'); "
        f"p.write_text({settings!r},encoding='utf-8'); p.chmod(0o600)"
    )
    run(
        [
            "docker", "run", "--rm", "--label", LABEL, "--user", "0:0",
            "--network", "none", "--mount", f"type=volume,source={volume},target=/config",
            "--entrypoint", "/usr/local/bin/python", reference, "-c", code,
        ],
        timeout=60,
    )


def supervisor_commands(name: str, generation_id: str) -> dict[str, str]:
    output = run(["docker", "top", name, "-eo", "args"], timeout=30).stdout.splitlines()
    commands = {}
    for component in ("core", "ui"):
        matches = [line.strip() for line in output if f"--mode {component}" in line and generation_id in line]
        if len(matches) != 1:
            raise CapabilityError(f"Supervisor did not expose one selected {component} worker command.")
        commands[component] = matches[0]
    return commands


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--amd64-bundle", type=Path, required=True)
    parser.add_argument("--arm64-bundle", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--minimum-image-version", required=True)
    parser.add_argument("--image-repository", default="coderluii/channelwatch")
    parser.add_argument("--image-index-digest", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--update-timeout", type=int, default=900)
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    bundles = {"amd64": args.amd64_bundle.resolve(), "arm64": args.arm64_bundle.resolve()}
    catalog_bytes, release, variants = verify_assets(
        args.catalog.resolve(), bundles, version=version, source_commit=args.source_commit
    )
    architecture = native_architecture(args.architecture)
    descriptor = variants[architecture]["runtime_descriptor"]
    descriptor_sha256 = hashlib.sha256(
        json.dumps(
            descriptor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    reference, manifest_digest, image_id, encoded_index, image_inspection = pinned_native_image(
        args.image_repository, args.image_index_digest, architecture
    )
    prefix = f"cw-runtime-cap-{architecture}-{secrets.token_hex(5)}"
    resources = DockerResources(prefix)
    temp_root = Path(tempfile.mkdtemp(prefix=prefix + "-"))
    args.evidence_json.parent.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "schema": 2,
        "status": "running",
        "target_application_version": version,
        "source_commit": args.source_commit,
        "signed_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "bootstrap": {
            "status": "passed",
            "minimum_image_version": args.minimum_image_version.strip().lstrip("v"),
            "launcher_protocol": 4,
            "immutable_bootstrap_sha256": args.image_index_digest.removeprefix("sha256:"),
            "image_index_digest": args.image_index_digest,
        },
        "architecture": {
            "status": "running",
            "architecture": architecture,
            "source_commit": args.source_commit,
            "descriptor_sha256": descriptor_sha256,
            "runtime_generation_id": descriptor["generation_id"],
            "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
            "target_variant_bundle_sha256": variants[architecture]["bundle_sha256"],
            "python_implementation": descriptor["python_implementation"],
            "python_version": descriptor["python_version"],
            "python_cache_tag": descriptor["python_cache_tag"],
            "os_family": descriptor["os_family"],
            "libc_family": descriptor["libc_family"],
            "minimum_libc_version": descriptor["minimum_libc_version"],
            "qualification": {},
        },
    }
    app = resources.container("app")
    feed = resources.container("feed")
    try:
        ca_path, _cert_path, _key_path = create_tls_material(temp_root)
        routes = {DEFAULT_CATALOG_URL: args.catalog.resolve()}
        for name in ("amd64", "arm64"):
            routes[variants[name]["bundle_url"]] = bundles[name]
        write_feed(temp_root, routes)
        network = resources.create_network()
        volume = resources.create_volume()
        seed_synthetic_rbac_settings(reference, volume)
        run([
            "docker", "run", "-d", "--name", feed, "--label", LABEL,
            "--network", network, "--network-alias", "github.com", "--network-alias", "channelwatch.coderluii.dev",
            "--mount", f"type=bind,source={temp_root},target=/feed,readonly",
            "--entrypoint", "/usr/local/bin/python", reference, "/feed/feed.py",
        ])
        username = "capability-admin"
        password = secrets.token_urlsafe(36)
        run([
            "docker", "run", "-d", "--name", app, "--label", LABEL,
            "--network", network, "--platform", f"linux/{architecture}",
            "--mount", f"type=volume,source={volume},target=/config",
            "--mount", f"type=bind,source={ca_path},target=/canary/ca.pem,readonly",
            "--env", "SSL_CERT_FILE=/canary/ca.pem", "--env", f"CW_ADMIN_USER={username}",
            "--env", f"CW_ADMIN_PASS={password}", "-p", "127.0.0.1::8501", reference,
        ])
        client = ApiClient(published_base_url(app), username, password)
        wait_until(lambda: client.request("/healthz/startup").get("status") == "ready", args.startup_timeout, "initial startup")
        client.login()
        run(["docker", "restart", app], timeout=120)
        client.set_base_url(published_base_url(app))
        wait_until(lambda: client.request("/healthz/startup").get("status") == "ready", args.startup_timeout, "synthetic RBAC restart")
        client.login()
        initial_status = client.request("/api/v1/update/status")
        if initial_status.get("launcher_protocol") != 4 or initial_status.get("current_version") != args.minimum_image_version.strip().lstrip("v"):
            raise CapabilityError("Bootstrap image did not expose the expected protocol/version floor.")
        before = preservation_hashes(app)
        container_identity_before = run(
            ["docker", "inspect", "--format", "{{.Id}}", app]
        ).stdout.strip()
        checked = validate_update_check(
            client.request("/api/v1/update/check", {}),
            version=version,
            descriptor=descriptor,
            bundle_sha256=variants[architecture]["bundle_sha256"],
        )
        job_id = secrets.token_hex(16)
        applied = apply_once_and_wait(client, "/api/v1/update/apply", version=version, job_id=job_id, timeout=args.update_timeout)
        components = validate_runtime_components(applied, descriptor)
        active_status = client.request("/api/v1/update/status")
        active = active_status.get("active_bundle")
        if not isinstance(active, dict) or active.get("runtime_generation_id") != descriptor["generation_id"]:
            raise CapabilityError("Active runtime does not match the signed generation.")
        generation_path = str(active.get("path") or "")
        if not generation_path.endswith("/app"):
            raise CapabilityError("Active runtime path is not the selected generation app directory.")
        imports = native_import_smoke(app, generation_path.removesuffix("/app"))
        commands = supervisor_commands(app, descriptor["generation_id"])
        after_apply = preservation_hashes(app)
        validate_preservation(before, after_apply)
        run(["docker", "restart", app], timeout=120)
        client.set_base_url(published_base_url(app))
        wait_until(lambda: client.request("/healthz/startup").get("status") == "ready", args.startup_timeout, "container restart")
        client.login()
        restarted = client.request("/api/v1/update/status")
        if restarted.get("current_version") != version:
            raise CapabilityError("Selected runtime did not persist across container restart.")
        after_restart = preservation_hashes(app)
        validate_preservation(before, after_restart)
        commands_after_restart = supervisor_commands(app, descriptor["generation_id"])
        rollback = validate_rollback_response(
            client.request("/api/v1/update/rollback", {}),
            source_version=version,
            target_version=args.minimum_image_version.strip().lstrip("v"),
        )
        wait_until(lambda: client.request("/api/v1/update/status").get("current_version") == args.minimum_image_version.strip().lstrip("v"), args.update_timeout, "API rollback")
        client.login()
        rollback_status = validate_rollback_status(
            client.request("/api/v1/update/status"),
            target_version=args.minimum_image_version.strip().lstrip("v"),
        )
        retry_id = secrets.token_hex(16)
        reapplied = apply_once_and_wait(client, "/api/v1/update/retry", version=version, job_id=retry_id, timeout=args.update_timeout)
        reapply_components = validate_runtime_components(reapplied, descriptor)
        after_reapply = preservation_hashes(app)
        validate_preservation(before, after_reapply)
        commands_after_reapply = supervisor_commands(app, descriptor["generation_id"])
        container_identity_after = run(
            ["docker", "inspect", "--format", "{{.Id}}", app]
        ).stdout.strip()
        if not container_identity_before or container_identity_after != container_identity_before:
            raise CapabilityError("Qualification replaced the bootstrap container.")
        receipt["architecture"].update(
            {
                "status": "passed",
                "interpreter_smoke": True,
                "native_imports": True,
                "system_libraries": True,
                "persistent_restart": True,
                "rollback": True,
                "qualification": {
                    "platform_manifest_digest": manifest_digest,
                    "pulled_image_id": image_id,
                    "bootstrap_index_base64": encoded_index,
                    "pulled_image_inspection": image_inspection,
                    "native_host_architecture": architecture,
                    "docker_engine_architecture": architecture,
                    "container_identity_before": container_identity_before,
                    "container_identity_after": container_identity_after,
                    "initial_status": initial_status,
                    "check": checked,
                    "apply_job_id": job_id,
                    "apply_runtime_components": components,
                    "native_imports": imports,
                    "supervisor_processes": commands,
                    "supervisor_processes_after_restart": commands_after_restart,
                    "supervisor_processes_after_reapply": commands_after_reapply,
                    "preservation_before_sha256": before,
                    "preservation_after_apply_sha256": after_apply,
                    "preservation_after_restart_sha256": after_restart,
                    "preservation_after_reapply_sha256": after_reapply,
                    "container_restart": {"status": "passed", "current_version": restarted.get("current_version")},
                    "rollback": rollback,
                    "rollback_status": rollback_status,
                    "reapply_job_id": retry_id,
                    "reapply_runtime_components": reapply_components,
                },
            }
        )
        receipt["status"] = "passed"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["failure"] = str(exc)[:1000]
        raise
    finally:
        log_hashes: dict[str, str] = {}
        for name in (app, feed):
            result = run(["docker", "logs", name], check=False)
            if result.returncode == 0 and (result.stdout or result.stderr):
                log_path = args.evidence_json.parent / f"{args.evidence_json.stem}-{name.rsplit('-', 1)[-1]}.log"
                log_path.write_text(
                    result.stdout + result.stderr, encoding="utf-8"
                )
                log_hashes[name.rsplit("-", 1)[-1]] = sha256_file(log_path)
        receipt["logs_sha256"] = log_hashes
        try:
            validate_log_hashes(log_hashes)
        except CapabilityError as exc:
            receipt["status"] = "failed"
            receipt["failure"] = str(exc)
        cleanup_failures = resources.cleanup()
        receipt["cleanup"] = {
            "status": "passed" if not cleanup_failures else "failed",
            "failures": cleanup_failures,
        }
        if cleanup_failures and receipt["status"] == "passed":
            receipt["status"] = "failed"
            receipt["failure"] = "Owned Docker resources could not be fully removed."
        args.evidence_json.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        shutil.rmtree(temp_root, ignore_errors=True)
    print(json.dumps({"status": receipt["status"], "evidence": str(args.evidence_json)}))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
