#!/usr/bin/env python3
"""Run the signed bridge inside immutable published ChannelWatch images."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = ROOT / "scripts/release/historical-image-lock.json"
PLATFORM = "linux/amd64"
RUNTIME_FILES = (
    "active.json",
    "rollback.json",
    "update-job.json",
    "activation-core-ready.json",
    "activation-ui-ready.json",
    "restart-required.json",
    "activation-pending.json",
    "official-recovery-mode.json",
    "update-scheduler.json",
)
EXPECTED_VERSIONS = tuple(f"0.9.{patch}" for patch in range(9, 18))
CRITICAL_IMAGE_FILES = {
    "/app/core/runtime_launcher.py": "app/core/runtime_launcher.py",
    "/app/core/update_center.py": "app/core/update_center.py",
    "/app/core/docker-entrypoint.py": "app/core/docker-entrypoint.py",
    "/app/ui/backend/main.py": "app/ui/backend/main.py",
    "/etc/supervisor/conf.d/supervisord.conf.template": (
        "deploy/config/supervisor/supervisord.conf.template"
    ),
}
FAILURE_CANARIES = {
    "0.9.15": "ui",
    "0.9.17": "core",
}
TAMPER_CANARY_VERSIONS = ("0.9.15", "0.9.17")
TRANSIENT_DOCKER_RESTART_MESSAGES = (
    "is restarting, wait until the container is running",
    "container is restarting",
)


class CanaryError(RuntimeError):
    """Raised when a historical image does not meet the bridge contract."""


def is_transient_docker_restart_error(error: BaseException) -> bool:
    """Return true only for Docker's explicit container-restarting response."""

    detail = str(error).lower()
    return any(message in detail for message in TRANSIENT_DOCKER_RESTART_MESSAGES)


def read_canary_marker(volume: str, name: str) -> str:
    path = Path(volume, name)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise CanaryError(
            f"required canary marker {name} is missing or unreadable"
        ) from exc
    if not value:
        raise CanaryError(f"required canary marker {name} is empty")
    return value


def scenario_key(scenario: str, source_version: str, variant: str | None = None) -> str:
    parts = [scenario, source_version]
    if variant:
        parts.append(variant)
    return ":".join(parts)


def validate_scenario_rows(results: list[dict[str, Any]]) -> None:
    keys = [str(item.get("scenario_key") or "") for item in results]
    if any(not key for key in keys):
        raise CanaryError("every historical canary result must have a scenario key")
    if len(keys) != len(set(keys)):
        raise CanaryError("historical canary scenario keys must be unique")


def run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise CanaryError(f"{command[0]} command failed: {detail[:500]}")
    return result


class DockerResources:
    def __init__(self, prefix: str, config_root: Path) -> None:
        self.prefix = prefix
        self.containers: set[str] = set()
        self.volumes: set[str] = set()
        self.config_root = config_root
        self.config_root.mkdir(parents=True, exist_ok=True)

    def container(self, suffix: str) -> str:
        name = f"{self.prefix}-{suffix}"
        self.containers.add(name)
        return name

    def volume(self, suffix: str) -> str:
        name = f"{self.prefix}-{suffix}"
        run(["docker", "volume", "create", name])
        self.volumes.add(name)
        return name

    def state_dir(self, suffix: str) -> Path:
        path = self.config_root / f"canary-state-{suffix}"
        path.mkdir(mode=0o777)
        path.chmod(0o777)
        return path

    def cleanup(self) -> None:
        for name in sorted(self.containers):
            run(["docker", "rm", "-f", name], check=False)
        for name in sorted(self.volumes):
            run(["docker", "volume", "rm", "-f", name], check=False)


def parse_public_keys(values: list[str] | None) -> dict[str, str]:
    keys: dict[str, str] = {}
    for item in values or []:
        key_id, separator, value = item.partition("=")
        if not separator or not key_id or not value:
            raise CanaryError("--public-key must use key-id=base64 format")
        keys[key_id] = value
    return keys


def load_locks() -> list[dict[str, Any]]:
    raw = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    if raw.get("schema") != 1 or raw.get("platform") != PLATFORM:
        raise CanaryError("historical image lock schema or platform is invalid")
    images = raw.get("images")
    if not isinstance(images, list) or len(images) != len(EXPECTED_VERSIONS):
        raise CanaryError("historical image lock must contain v0.9.9-v0.9.17")
    versions = [
        str(item.get("version") or "") for item in images if isinstance(item, dict)
    ]
    if tuple(versions) != EXPECTED_VERSIONS or len(set(versions)) != len(versions):
        raise CanaryError(
            "historical image locks must be unique and ordered v0.9.9-v0.9.17"
        )
    for item in images:
        version = str(item["version"])
        protocol = int(item.get("launcher_protocol", -1))
        expected_protocol = 0 if version == "0.9.9" else 1 if version <= "0.9.15" else 2
        expected_support = (
            "image_pull_only" if version in {"0.9.9", "0.9.10"} else "app_update"
        )
        if (
            item.get("tag") != f"v{version}"
            or item.get("support") != expected_support
            or protocol != expected_protocol
            or not re.fullmatch(r"[0-9a-f]{40}", str(item.get("source_sha") or ""))
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(item.get("index_digest") or "")
            )
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(item.get("amd64_digest") or "")
            )
            or not isinstance(item.get("entrypoint"), list)
            or not isinstance(item.get("cmd"), list)
        ):
            raise CanaryError(f"historical image lock is invalid for {version}")
    return images


def inspect_remote_lock(repository: str, lock: dict[str, Any]) -> None:
    expected_index = str(lock["index_digest"])
    for remote in (repository, f"ghcr.io/{repository}"):
        reference = f"{remote}@{expected_index}"
        raw = run(
            ["docker", "buildx", "imagetools", "inspect", "--raw", reference],
            timeout=120,
        ).stdout
        manifest = json.loads(raw)
        children = manifest.get("manifests")
        if not isinstance(children, list):
            raise CanaryError(f"{remote} no longer exposes the locked image index")
        amd64 = [
            item
            for item in children
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == "amd64"
        ]
        if len(amd64) != 1 or amd64[0].get("digest") != lock["amd64_digest"]:
            raise CanaryError(
                f"{remote} AMD64 descriptor changed for {lock['version']}"
            )


def inspect_local_image(reference: str, lock: dict[str, Any]) -> None:
    run(["docker", "pull", "--platform", PLATFORM, reference], timeout=300)
    inspected = json.loads(run(["docker", "image", "inspect", reference]).stdout)[0]
    config = inspected.get("Config", {})
    labels = config.get("Labels") or {}
    env_values = config.get("Env") or []
    expected_env = f"CHANNELWATCH_IMAGE_VERSION={lock['version']}"
    if labels.get("org.opencontainers.image.revision") != lock["source_sha"]:
        raise CanaryError(f"source revision label changed for {lock['version']}")
    if labels.get("org.opencontainers.image.version") != lock["version"]:
        raise CanaryError(f"version label changed for {lock['version']}")
    if expected_env not in env_values:
        raise CanaryError(
            f"immutable image version environment changed for {lock['version']}"
        )
    if (
        config.get("Entrypoint") != lock["entrypoint"]
        or config.get("Cmd") != lock["cmd"]
    ):
        raise CanaryError(f"entrypoint contract changed for {lock['version']}")

    image_paths = json.dumps(list(CRITICAL_IMAGE_FILES))
    image_hashes = json.loads(
        run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                PLATFORM,
                "--network",
                "none",
                "--entrypoint",
                "/usr/bin/python",
                reference,
                "-c",
                (
                    "import hashlib,json; "
                    f"paths={image_paths}; "
                    "print(json.dumps({p:hashlib.sha256(open(p,'rb').read()).hexdigest() for p in paths}))"
                ),
            ],
            timeout=60,
        ).stdout.strip()
    )
    for image_path, source_path in CRITICAL_IMAGE_FILES.items():
        source = run(
            ["git", "-C", str(ROOT), "show", f"{lock['tag']}:{source_path}"],
            timeout=30,
        ).stdout.encode()
        if image_hashes.get(image_path) != hashlib.sha256(source).hexdigest():
            raise CanaryError(
                f"critical image bytes changed for {lock['version']}: {image_path}"
            )


def inspect_recovery_image(reference: str, version: str) -> None:
    inspected_result = run(
        ["docker", "image", "inspect", reference],
        check=False,
    )
    if inspected_result.returncode != 0:
        run(["docker", "pull", "--platform", PLATFORM, reference], timeout=300)
        inspected_result = run(["docker", "image", "inspect", reference])
    inspected = json.loads(inspected_result.stdout)[0]
    labels = inspected.get("Config", {}).get("Labels") or {}
    expected_sha = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    if (
        labels.get("org.opencontainers.image.version") != version
        or labels.get("org.opencontainers.image.revision") != expected_sha
    ):
        raise CanaryError("recovery image does not match the exact candidate source")


def write_probe(path: Path) -> None:
    path.write_text(
        """
import json
import sys
from pathlib import Path

from core.update_center import UPDATE_PUBLIC_KEYS, UpdateManager

manifest_path = Path(sys.argv[1])
bundle_path = Path(sys.argv[2])
version = sys.argv[3]
current_version = sys.argv[4]
config_dir = Path('/config')
override = json.loads(sys.argv[5])
manifest_bytes = manifest_path.read_bytes()
bundle_bytes = bundle_path.read_bytes()
manager = UpdateManager(
    config_dir=config_dir,
    current_version=current_version,
    settings_schema_version=7,
    public_keys=override or UPDATE_PUBLIC_KEYS,
    fetcher=lambda url, _limit: bundle_bytes if url.endswith('.zip') else manifest_bytes,
    restart_callable=lambda: True,
)
checked = manager.check()
applied = manager.apply(version)
print(json.dumps({
    'check_status': checked.get('last_job', {}).get('status'),
    'apply_status': applied.get('status'),
}))
""".strip() + "\n",
        encoding="utf-8",
    )


def write_rejection_probe(path: Path) -> None:
    path.write_text(
        """
import json
import sys
from pathlib import Path

from core.update_center import UPDATE_PUBLIC_KEYS, UpdateManager

manifest_path = Path(sys.argv[1])
bundle_path = Path(sys.argv[2])
version = sys.argv[3]
current_version = sys.argv[4]
case = sys.argv[5]
override = json.loads(sys.argv[6])
manifest_bytes = manifest_path.read_bytes()
bundle_bytes = bundle_path.read_bytes()
if case == 'manifest':
    document = json.loads(manifest_bytes)
    value = document['signature']['value']
    document['signature']['value'] = ('A' if value[0] != 'A' else 'B') + value[1:]
    manifest_bytes = (json.dumps(document, sort_keys=True) + '\\n').encode()
elif case == 'bundle':
    bundle_bytes = bundle_bytes[:-1] + bytes([bundle_bytes[-1] ^ 1])
else:
    raise SystemExit('unsupported tamper case')
manager = UpdateManager(
    config_dir=Path('/config'),
    current_version=current_version,
    settings_schema_version=7,
    public_keys=override or UPDATE_PUBLIC_KEYS,
    fetcher=lambda url, _limit: bundle_bytes if url.endswith('.zip') else manifest_bytes,
    restart_callable=lambda: True,
)
rejected = False
try:
    checked = manager.check()
    if case == 'manifest':
        raise RuntimeError('tampered manifest was accepted')
    manager.apply(version)
except Exception:
    rejected = True
active = Path('/config/channelwatch-runtime/active.json')
if not rejected or active.exists():
    raise RuntimeError('tampered update changed the active runtime')
print(json.dumps({'case': case, 'rejected': True, 'active_unchanged': True}))
""".strip() + "\n",
        encoding="utf-8",
    )


def write_sitecustomize(path: Path) -> None:
    """Install a hermetic fetch transport into the already-running old UI.

    Only the immutable historical process is patched. The bundle-fetch marker
    is durable under disposable /config, so every restarted process executes
    the candidate normally without importing image-owned updater modules first.
    """

    path.write_text(
        """
import json
import os
import stat
from pathlib import Path

config = Path(os.environ.get('CONFIG_PATH', '/config'))
canary_state = Path(os.environ.get('CHANNELWATCH_CANARY_STATE', '/config'))
assets = Path('/canary-assets')
runtime = config / 'channelwatch-runtime'
fetch_complete = canary_state / '.canary-fetch-complete'
fault = canary_state / '.canary-fault.json'
fault_applied = canary_state / '.canary-fault-applied'

if fault.is_file() and not fault_applied.exists():
    try:
        active = json.loads((runtime / 'active.json').read_text())
        component = json.loads(fault.read_text()).get('component')
        active_path = Path(str(active.get('path') or '')).resolve()
        releases = (runtime / 'releases').resolve()
        active_path.relative_to(releases)
        relative = 'core/main.py' if component == 'core' else 'ui/backend/main.py'
        target = (active_path / relative).resolve()
        target.relative_to(active_path)
        if target.is_file() and stat.S_ISREG(target.lstat().st_mode):
            target.write_text("raise RuntimeError('deterministic canary activation failure')\\n")
            target.chmod(0o640)
            fault_applied.write_text(component + '\\n')
    except Exception:
        pass

if not fetch_complete.exists():
    try:
        import core.update_center as update_center

        public_keys = json.loads(
            Path('/tmp/channelwatch-canary-public-keys.json').read_text()
        )
        if public_keys:
            update_center.UPDATE_PUBLIC_KEYS.clear()
            update_center.UPDATE_PUBLIC_KEYS.update(public_keys)
        manifest_name = os.environ['CHANNELWATCH_CANARY_MANIFEST']
        bundle_name = os.environ['CHANNELWATCH_CANARY_BUNDLE']
        tamper_case = os.environ.get('CHANNELWATCH_CANARY_TAMPER', '')
        tamper_applied = canary_state / '.canary-tamper-applied'

        def hermetic_fetch(url, max_bytes, timeout=20.0):
            del timeout
            (canary_state / '.canary-fetch-last').write_text(url + '\\n')
            update_center.validate_trusted_url(url)
            if url.endswith('.zip'):
                data = (assets / bundle_name).read_bytes()
                if tamper_case == 'bundle':
                    data = data[:-1] + bytes([data[-1] ^ 1])
                    tamper_applied.write_text('bundle\\n')
                fetch_complete.write_text('bundle\\n')
            elif url == 'https://channelwatch.coderluii.dev/updates/stable.json':
                data = (assets / manifest_name).read_bytes()
                if tamper_case == 'manifest':
                    document = json.loads(data)
                    value = document['signature']['value']
                    document['signature']['value'] = (
                        ('A' if value[0] != 'A' else 'B') + value[1:]
                    )
                    data = (json.dumps(document, sort_keys=True) + '\\n').encode()
                    tamper_applied.write_text('manifest\\n')
            else:
                raise RuntimeError('canary transport rejected an unexpected URL')
            if len(data) > max_bytes:
                raise RuntimeError('canary asset exceeded the updater limit')
            return data

        update_center.fetch_bytes = hermetic_fetch
        (canary_state / '.canary-patch-status').write_text('patched\\n')
    except Exception as exc:
        (canary_state / '.canary-patch-status').write_text(
            type(exc).__name__ + '\\n'
        )
""".strip() + "\n",
        encoding="utf-8",
    )


def init_volume(reference: str, volume: str) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            PLATFORM,
            "--network",
            "none",
            "--user",
            "0:0",
            "--entrypoint",
            "/usr/bin/python",
            "--volume",
            f"{volume}:/config",
            reference,
            "-c",
            (
                "import os,stat; "
                "os.makedirs('/config/channelwatch-runtime',exist_ok=True); "
                "os.chown('/config/channelwatch-runtime',501,20); "
                "os.chmod('/config/channelwatch-runtime',0o770); "
                "s=os.lstat('/config/channelwatch-runtime'); "
                "assert stat.S_ISDIR(s.st_mode) and s.st_uid==501 and s.st_gid==20"
            ),
        ],
        timeout=60,
    )


def prime_update(
    reference: str,
    volume: str,
    artifacts: Path,
    probe: Path,
    manifest: Path,
    bundle: Path,
    target_version: str,
    current_version: str,
    public_keys: dict[str, str],
) -> dict[str, Any]:
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            PLATFORM,
            "--network",
            "none",
            "--user",
            "501:20",
            "--entrypoint",
            "/venv/bin/python",
            "--env",
            "PYTHONPATH=/app",
            "--env",
            "CONFIG_PATH=/config",
            "--env",
            f"CHANNELWATCH_IMAGE_VERSION={current_version}",
            "--volume",
            f"{volume}:/config",
            "--volume",
            f"{artifacts}:/artifacts:ro",
            "--volume",
            f"{probe}:/canary/prime.py:ro",
            reference,
            "/canary/prime.py",
            f"/artifacts/{manifest.name}",
            f"/artifacts/{bundle.name}",
            target_version,
            current_version,
            json.dumps(public_keys, sort_keys=True),
        ],
        timeout=180,
    )
    parsed = json.loads(result.stdout.strip().splitlines()[-1])
    if (
        parsed.get("check_status") != "available"
        or parsed.get("apply_status") != "restarting"
    ):
        raise CanaryError(
            f"historical updater did not stage {target_version} from {current_version}"
        )
    return parsed


def start_container(
    resources: DockerResources,
    name: str,
    reference: str,
    volume: str,
    storage_key: str,
    *,
    canary_dir: Path | None = None,
    canary_state: Path | None = None,
    artifacts: Path | None = None,
    manifest_name: str | None = None,
    bundle_name: str | None = None,
    tamper_case: str | None = None,
) -> None:
    resources.containers.add(name)
    command = [
        "docker",
        "run",
        "--detach",
        "--platform",
        PLATFORM,
        "--name",
        name,
        "--restart",
        "always",
        "--network",
        "none",
        "--env",
        "CW_DISABLE_AUTH=true",
        "--env",
        "TZ=UTC",
        "--env",
        "CONFIG_PATH=/config",
        "--env",
        f"CHANNELWATCH_SECRET_STORAGE_KEY={storage_key}",
        "--volume",
        f"{volume}:/config",
    ]
    if canary_dir is not None and canary_state is not None and artifacts is not None:
        command.extend(
            [
                "--env",
                "PYTHONPATH=/app",
                "--env",
                f"CHANNELWATCH_CANARY_MANIFEST={manifest_name}",
                "--env",
                f"CHANNELWATCH_CANARY_BUNDLE={bundle_name}",
                "--env",
                "CHANNELWATCH_CANARY_STATE=/canary-state",
                "--volume",
                (
                    f"{canary_dir / 'public-keys.json'}:"
                    "/tmp/channelwatch-canary-public-keys.json:ro"
                ),
                "--volume",
                (
                    f"{canary_dir / 'sitecustomize.py'}:"
                    "/venv/lib/python3.14/site-packages/sitecustomize.py:ro"
                ),
                "--volume",
                f"{artifacts}:/canary-assets:ro",
                "--volume",
                f"{canary_state}:/canary-state",
            ]
        )
        if tamper_case:
            command.extend(["--env", f"CHANNELWATCH_CANARY_TAMPER={tamper_case}"])
    command.append(reference)
    run(
        command,
        timeout=60,
    )


def exec_python(name: str, code: str, *, check: bool = True) -> str:
    return run(
        ["docker", "exec", name, "/venv/bin/python", "-c", code],
        check=check,
        timeout=20,
    ).stdout.strip()


def health(name: str, path: str) -> bool:
    code = (
        "import urllib.request; "
        f"r=urllib.request.urlopen('http://127.0.0.1:8501{path}', timeout=3); "
        "print(r.status)"
    )
    result = run(
        ["docker", "exec", name, "/venv/bin/python", "-c", code],
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if "error response from daemon" in detail.lower() and not any(
            message in detail.lower() for message in TRANSIENT_DOCKER_RESTART_MESSAGES
        ):
            raise CanaryError(
                f"docker health probe failed outside restart: {detail[:500]}"
            )
        return False
    return result.returncode == 0 and result.stdout.strip() == "200"


def get_json(name: str, path: str) -> dict[str, Any]:
    code = (
        "import json,urllib.request; "
        f"r=urllib.request.urlopen('http://127.0.0.1:8501{path}', timeout=3); "
        "print(json.dumps({'status':r.status,'body':json.loads(r.read() or b'{}')}))"
    )
    output = exec_python(name, code)
    return json.loads(output.splitlines()[-1])


def wait_for_health(name: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if health(name, "/healthz/live") and health(name, "/healthz/startup"):
            return
        time.sleep(1)
    raise CanaryError(f"historical container {name} did not reach startup")


def post_api(
    name: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else b""
    code = f"""
import json, urllib.error, urllib.request
body={body!r}
request=urllib.request.Request(
    'http://127.0.0.1:8501{path}',
    data=body,
    method='POST',
    headers={{'Content-Type':'application/json'}},
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        data=response.read()
        print(json.dumps({{'status':response.status,'body':json.loads(data or b'{{}}')}}))
except urllib.error.HTTPError as error:
    data=error.read()
    try: body=json.loads(data or b'{{}}')
    except Exception: body={{}}
    print(json.dumps({{'status':error.code,'body':body}}))
"""
    result = run(
        ["docker", "exec", name, "/venv/bin/python", "-c", code],
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return {"status": None, "connection_closed": True}
    return json.loads(result.stdout.strip().splitlines()[-1])


def wait_for_restart_and_health(
    name: str,
    *,
    previous_restart_count: int,
    timeout_seconds: int,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = restart_count(name)
        if current > previous_restart_count + 1:
            raise CanaryError(f"{name} restarted more than once during activation")
        if (
            current == previous_restart_count + 1
            and health(name, "/healthz/live")
            and health(name, "/healthz/startup")
        ):
            return current
        time.sleep(1)
    raise CanaryError(f"historical container {name} did not complete a real restart")


def supervisor_state(name: str) -> dict[str, dict[str, Any]]:
    code = """
import http.client, json, socket, xmlrpc.client
class Connection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__('localhost')
        self.socket_path=socket_path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)
class Transport(xmlrpc.client.Transport):
    def __init__(self, socket_path):
        super().__init__()
        self.socket_path=socket_path
    def make_connection(self, host):
        return Connection(self.socket_path)
server=xmlrpc.client.ServerProxy(
    'http://channelwatch-supervisor/RPC2',
    transport=Transport('/tmp/channelwatch/supervisor.sock'),
    allow_none=True,
)
print(json.dumps({item['name']:{'status':item['statename'],'pid':item['pid']} for item in server.supervisor.getAllProcessInfo()},sort_keys=True))
"""
    return json.loads(exec_python(name, code))


def require_stable_children(name: str) -> dict[str, dict[str, Any]]:
    state = supervisor_state(name)
    selected = {
        process: details
        for process, details in state.items()
        if process in {"core", "ui"}
    }
    if set(selected) != {"core", "ui"} or any(
        details.get("status") != "RUNNING" or not details.get("pid")
        for details in selected.values()
    ):
        raise CanaryError(f"{name} does not have two stable Supervisor children")
    return selected


def runtime_state(name: str) -> dict[str, Any]:
    names = json.dumps(RUNTIME_FILES)
    code = f"""
import json, os, stat
from pathlib import Path
root=Path('/config/channelwatch-runtime')
out={{}}
for name in {names}:
    path=root/name
    if path.is_file():
        try: out[name]=json.loads(path.read_text())
        except Exception: out[name]={{'invalid': True}}
out['activation_artifacts']=sorted(path.name for path in root.glob('activation-*.json'))
releases=root/'releases'
out['release_directories']=sorted(
    path.name for path in releases.iterdir() if path.is_dir()
) if releases.is_dir() else []
key=Path('/config/encryption.key')
if key.exists():
    info=key.lstat()
    out['key']={{'regular': stat.S_ISREG(info.st_mode), 'symlink': key.is_symlink(), 'links': info.st_nlink, 'size': info.st_size, 'mode': stat.S_IMODE(info.st_mode)}}
print(json.dumps(out))
"""
    return json.loads(exec_python(name, code))


def volume_runtime_state(reference: str, volume: str) -> dict[str, Any]:
    """Inspect runtime control files after an immutable image can no longer boot."""

    names = json.dumps(RUNTIME_FILES)
    code = f"""
import json
from pathlib import Path
root=Path('/config/channelwatch-runtime')
out={{}}
for name in {names}:
    path=root/name
    if path.is_file():
        try: out[name]=json.loads(path.read_text())
        except Exception: out[name]={{'invalid': True}}
out['activation_artifacts']=sorted(path.name for path in root.glob('activation-*.json'))
releases=root/'releases'
out['release_directories']=sorted(
    path.name for path in releases.iterdir() if path.is_dir()
) if releases.is_dir() else []
print(json.dumps(out))
"""
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            PLATFORM,
            "--network",
            "none",
            "--user",
            "0:0",
            "--entrypoint",
            "/usr/bin/python",
            "--volume",
            f"{volume}:/config:ro",
            reference,
            "-c",
            code,
        ],
        timeout=60,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def poll_runtime_state(name: str) -> dict[str, Any] | None:
    """Read state while allowing only a proven Docker restart transition."""

    try:
        return runtime_state(name)
    except CanaryError as exc:
        if is_transient_docker_restart_error(exc):
            return None
        # ``docker exec`` can fail with several runtime-specific messages when
        # PID 1 exits after the exec was accepted. Treat any such exec failure
        # as transient only when a separate Docker inspect proves the container
        # is currently in its expected restart transition.
        if container_status(name) == "restarting":
            return None
        raise


def activation_artifacts(state: dict[str, Any]) -> list[str]:
    raw = state.get("activation_artifacts")
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise CanaryError("runtime state did not report activation artifacts")
    return raw


def transition_artifacts(state: dict[str, Any]) -> list[str]:
    artifacts = list(activation_artifacts(state))
    if "restart-required.json" in state:
        artifacts.append("restart-required.json")
    return artifacts


def restart_count(name: str) -> int:
    result = run(["docker", "inspect", "--format", "{{.RestartCount}}", name])
    return int(result.stdout.strip())


def container_status(name: str) -> str:
    result = run(["docker", "inspect", "--format", "{{.State.Status}}", name])
    status = result.stdout.strip().lower()
    if status not in {
        "created",
        "running",
        "paused",
        "restarting",
        "removing",
        "exited",
        "dead",
    }:
        raise CanaryError(f"Docker returned an unknown container state for {name}")
    return status


def logs_use_bundle(name: str, version: str) -> tuple[bool, bool]:
    result = run(["docker", "logs", name], check=False)
    logs = result.stdout + "\n" + result.stderr
    version_marker = f"releases/v{version}"
    return (
        bool(re.search(r"Launching core from .*" + re.escape(version_marker), logs)),
        bool(re.search(r"Launching ui from .*" + re.escape(version_marker), logs)),
    )


def _running_app_dirs_for_children(
    name: str, children: dict[str, dict[str, Any]]
) -> dict[str, str]:
    pids = {
        mode: int(details["pid"])
        for mode, details in children.items()
        if details.get("status") == "RUNNING" and int(details.get("pid") or 0) > 0
    }
    if not pids:
        return {}
    code = f"""
import json,os
from pathlib import Path
apps={{}}
for mode,pid in {pids!r}.items():
    process=Path(f'/proc/{{pid}}')
    environment=process.joinpath('environ').read_bytes().split(b'\\0')
    app_dir=''
    for item in environment:
        if item.startswith(b'CHANNELWATCH_APP_DIR='):
            app_dir=item.partition(b'=')[2].decode('utf-8','replace')
            break
    if not app_dir:
        app_dir=os.readlink(process/'cwd')
    apps[mode]=app_dir
print(json.dumps(apps, sort_keys=True))
"""
    output = run(
        [
            "docker",
            "exec",
            "--privileged",
            name,
            "/venv/bin/python",
            "-S",
            "-c",
            code,
        ],
        timeout=20,
    ).stdout.strip()
    parsed = json.loads(output)
    return {
        str(mode): str(app_dir)
        for mode, app_dir in parsed.items()
        if mode in {"core", "ui"}
    }


def running_app_dirs(name: str) -> dict[str, str]:
    return _running_app_dirs_for_children(name, require_stable_children(name))


def wait_for_v099_false_success(
    name: str, timeout_seconds: int
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Observe the published protocol-0 launch defect without calling it healthy."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        children = supervisor_state(name)
        core = children.get("core", {})
        ui = children.get("ui", {})
        if core.get("status") == "FATAL" and ui.get("status") == "RUNNING":
            app_dirs = _running_app_dirs_for_children(name, {"ui": ui})
            if app_dirs == {"ui": "/app"}:
                return children, app_dirs
        time.sleep(1)
    raise CanaryError(
        "v0.9.9 did not converge to its documented image-only launcher failure"
    )


def wait_for_v010_entrypoint_failure(
    name: str,
    *,
    previous_restart_count: int,
    timeout_seconds: int,
) -> int:
    """Observe the immutable v0.9.10 restart-loop defect after portal apply."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = restart_count(name)
        log_result = run(["docker", "logs", name], check=False)
        logs = log_result.stdout + "\n" + log_result.stderr
        if (
            current >= previous_restart_count + 2
            and "PermissionError" in logs
            and "/tmp/supervisord.conf" in logs
            and container_status(name) in {"running", "restarting"}
        ):
            return current
        time.sleep(1)
    raise CanaryError(
        "v0.9.10 did not reproduce its immutable entrypoint restart failure"
    )


def running_bundle_modes(name: str, version: str) -> set[str]:
    marker = f"releases/v{version}"
    return {
        mode for mode, app_dir in running_app_dirs(name).items() if marker in app_dir
    }


def run_supported_canary(
    lock: dict[str, Any],
    *,
    repository: str,
    resources: DockerResources,
    artifacts: Path,
    canary_dir: Path,
    probe: Path,
    manifest: Path,
    bundle: Path,
    target_version: str,
    public_keys: dict[str, str],
    startup_timeout: int,
    stability_seconds: int,
) -> dict[str, Any]:
    reference = f"{repository}@{lock['index_digest']}"
    inspect_remote_lock(repository, lock)
    inspect_local_image(reference, lock)
    suffix = lock["version"].replace(".", "-")
    volume = resources.volume(f"config-{suffix}")
    canary_state = resources.state_dir(f"success-{suffix}")
    init_volume(reference, volume)
    del probe, public_keys
    name = resources.container(f"app-{suffix}")
    start_container(
        resources,
        name,
        reference,
        volume,
        secrets.token_urlsafe(48),
        canary_dir=canary_dir,
        canary_state=canary_state,
        artifacts=artifacts,
        manifest_name=manifest.name,
        bundle_name=bundle.name,
    )
    wait_for_health(name, startup_timeout)
    require_stable_children(name)
    initial_restart_count = restart_count(name)
    checked = post_api(name, "/api/v1/update/check")
    if (
        checked.get("status") != 200
        or checked.get("body", {}).get("last_job", {}).get("status") != "available"
    ):
        raise CanaryError(
            f"historical portal check failed for {lock['version']}: "
            f"status={checked.get('status')} body={checked.get('body')}"
        )
    applied = post_api(
        name,
        "/api/v1/update/apply",
        {"version": target_version},
    )
    if applied.get("status") not in {202, None}:
        raise CanaryError(f"historical portal apply failed for {lock['version']}")
    final_restart_count = wait_for_restart_and_health(
        name,
        previous_restart_count=initial_restart_count,
        timeout_seconds=startup_timeout,
    )
    state = runtime_state(name)
    active = state.get("active.json", {})
    rollback = state.get("rollback.json", {})
    job = state.get("update-job.json", {})
    process_modes = running_bundle_modes(name, target_version)
    current_apps = running_app_dirs(name)
    current_supervisor = require_stable_children(name)
    key = state.get("key", {})
    stale = transition_artifacts(state)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    active_manifest = active.get("manifest") if isinstance(active, dict) else None
    active_manifest = active_manifest if isinstance(active_manifest, dict) else {}
    if active.get("version") != target_version or job.get("status") != "success":
        raise CanaryError(f"{lock['version']} did not activate {target_version}")
    if final_restart_count - initial_restart_count != 1:
        raise CanaryError(
            f"{lock['version']} activation did not use exactly one restart"
        )
    if (
        active.get("path") != f"/config/channelwatch-runtime/releases/v{target_version}"
        or active_manifest.get("bundle_sha256") != digest
        or active.get("runtime_abi") != "channelwatch-runtime-v1"
        or active.get("settings_schema_version") != 7
        or job.get("bundle_sha256") != digest
    ):
        raise CanaryError(
            f"{lock['version']} did not bind the exact candidate identity"
        )
    if (
        rollback.get("target_version") != target_version
        or rollback.get("previous_active") is not None
    ):
        raise CanaryError(
            f"{lock['version']} did not preserve an image rollback target"
        )
    if process_modes != {"core", "ui"}:
        raise CanaryError(
            f"{lock['version']} did not launch both children from the bundle "
            f"(modes={sorted(process_modes)}, apps={current_apps})"
        )
    if stale:
        raise CanaryError(f"{lock['version']} left stale activation controls: {stale}")
    if key != {"regular": True, "symlink": False, "links": 1, "size": 32, "mode": 384}:
        raise CanaryError(
            f"{lock['version']} did not converge to a private managed key"
        )
    first_restart_count = final_restart_count
    time.sleep(stability_seconds)
    stable_state = runtime_state(name)
    stable_apps = running_app_dirs(name)
    stable_supervisor = require_stable_children(name)
    if (
        restart_count(name) != first_restart_count
        or not health(name, "/healthz/live")
        or not health(name, "/healthz/startup")
        or stable_state.get("active.json") != active
        or stable_state.get("update-job.json", {}).get("status") != "success"
        or stable_state.get("key") != key
        or "official-recovery-mode.json" in stable_state
        or running_bundle_modes(name, target_version) != {"core", "ui"}
        or stable_apps != current_apps
        or stable_supervisor != current_supervisor
    ):
        raise CanaryError(f"{lock['version']} did not remain stable after activation")
    return {
        "scenario": "activation_success",
        "scenario_key": scenario_key("activation_success", lock["version"]),
        "source_version": lock["version"],
        "source_sha": lock["source_sha"],
        "image_index_digest": lock["index_digest"],
        "amd64_digest": lock["amd64_digest"],
        "launcher_protocol": lock["launcher_protocol"],
        "bundle_sha256": digest,
        "check_status": "available",
        "apply_status": (
            applied.get("body", {}).get("status")
            if applied.get("status") == 202
            else "connection_closed_for_restart"
        ),
        "final_job_status": job.get("status"),
        "core_bundle": True,
        "ui_bundle": True,
        "portal_api_verified": True,
        "restart_count_delta": final_restart_count - initial_restart_count,
        "supervisor_stable": True,
        "rollback_target_verified": True,
        "active_identity_verified": True,
        "restart_count": first_restart_count,
        "managed_key_verified": True,
        "stale_control_file_count": 0,
        "result": "passed",
    }


def wait_for_failed_activation(
    name: str,
    *,
    target_version: str,
    bundle_sha256: str,
    previous_restart_count: int,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    identity = f"{target_version}:{bundle_sha256}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current_restart_count = restart_count(name)
        if current_restart_count > previous_restart_count + 2:
            raise CanaryError(
                f"{name} entered a restart loop during activation rollback"
            )
        state = poll_runtime_state(name)
        if state is None:
            time.sleep(1)
            continue
        job = state.get("update-job.json", {})
        scheduler = state.get("update-scheduler.json", {})
        quarantines = (
            scheduler.get("quarantines", {}) if isinstance(scheduler, dict) else {}
        )
        quarantine = (
            quarantines.get(identity) if isinstance(quarantines, dict) else None
        )
        last_attempt = (
            scheduler.get("last_attempt") if isinstance(scheduler, dict) else None
        )
        if (
            current_restart_count == previous_restart_count + 2
            and "active.json" not in state
            and job.get("status") == "failed"
            and job.get("rollback_applied") is True
            and job.get("bundle_sha256") == bundle_sha256
            and isinstance(quarantine, dict)
            and quarantine.get("version") == target_version
            and quarantine.get("bundle_sha256") == bundle_sha256
            and isinstance(last_attempt, dict)
            and last_attempt.get("job_id") == job.get("job_id")
            and last_attempt.get("bundle_sha256") == bundle_sha256
            and health(name, "/healthz/live")
            and health(name, "/healthz/startup")
        ):
            return current_restart_count, state
        time.sleep(1)
    raise CanaryError(f"{name} did not converge to quarantined activation rollback")


def validate_failed_activation_state(
    state: dict[str, Any],
    *,
    target_version: str,
    bundle_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the exact failed identity and its durable rollback disposition."""

    if "active.json" in state:
        raise CanaryError("failed activation remained selected")
    artifacts = transition_artifacts(state)
    if artifacts:
        raise CanaryError(f"activation rollback left control artifacts: {artifacts}")
    if "official-recovery-mode.json" in state:
        raise CanaryError(
            "activation rollback entered image recovery instead of rollback"
        )

    rollback = state.get("rollback.json")
    if not isinstance(rollback, dict) or (
        rollback.get("target_version") != target_version
        or rollback.get("previous_active") is not None
    ):
        raise CanaryError("activation rollback did not preserve the exact image target")

    job = state.get("update-job.json")
    if not isinstance(job, dict):
        raise CanaryError("activation rollback did not preserve its failed job")
    job_id = str(job.get("job_id") or "")
    attempt_id = str(job.get("scheduler_attempt_id") or "")
    expected_job = {
        "operation": "apply",
        "status": "failed",
        "version": target_version,
        "bundle_sha256": bundle_sha256,
        "rollback_applied": True,
        "rolled_back_from": target_version,
        "rolled_back_to": "image",
    }
    mismatches = {
        key: (job.get(key), expected)
        for key, expected in expected_job.items()
        if job.get(key) != expected
    }
    if mismatches or not job_id or not attempt_id:
        raise CanaryError(
            f"activation rollback job did not bind the exact identity: {mismatches}"
        )

    scheduler = state.get("update-scheduler.json")
    if not isinstance(scheduler, dict):
        raise CanaryError("activation rollback did not preserve scheduler state")
    identity = f"{target_version}:{bundle_sha256}"
    quarantine = (scheduler.get("quarantines") or {}).get(identity)
    expected_quarantine = {
        "version": target_version,
        "bundle_sha256": bundle_sha256,
        "reason": "activation_failed",
    }
    if not isinstance(quarantine, dict) or any(
        quarantine.get(key) != expected for key, expected in expected_quarantine.items()
    ):
        raise CanaryError(
            "activation rollback quarantine did not bind the exact identity"
        )
    last_attempt = scheduler.get("last_attempt")
    expected_attempt = {
        "version": target_version,
        "bundle_sha256": bundle_sha256,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "phase": "failed",
        "terminal_job_status": "failed",
        "rollback_applied": True,
    }
    if not isinstance(last_attempt, dict) or any(
        last_attempt.get(key) != expected for key, expected in expected_attempt.items()
    ):
        raise CanaryError("activation rollback scheduler attempt did not match its job")
    if scheduler.get("maintenance_attention_code") != "update-activation-failed":
        raise CanaryError("activation rollback did not request administrator attention")
    if any(
        scheduler.get(key) is not None
        for key in (
            "scheduled_restart_at",
            "scheduled_release_version",
            "scheduled_release_sha256",
            "scheduled_attempt_id",
        )
    ):
        raise CanaryError("activation rollback left a scheduled reinstall behind")
    return job, scheduler


def run_activation_failure_canary(
    lock: dict[str, Any],
    *,
    component: str,
    repository: str,
    resources: DockerResources,
    artifacts: Path,
    canary_dir: Path,
    manifest: Path,
    bundle: Path,
    target_version: str,
    startup_timeout: int,
    stability_seconds: int,
) -> dict[str, Any]:
    reference = f"{repository}@{lock['index_digest']}"
    suffix = lock["version"].replace(".", "-")
    volume = resources.volume(f"failure-{suffix}-{component}")
    init_volume(reference, volume)
    canary_state = resources.state_dir(f"failure-{suffix}-{component}")
    Path(canary_state, ".canary-fault.json").write_text(
        json.dumps({"component": component}) + "\n",
        encoding="utf-8",
    )
    name = resources.container(f"failure-{suffix}-{component}")
    start_container(
        resources,
        name,
        reference,
        volume,
        secrets.token_urlsafe(48),
        canary_dir=canary_dir,
        canary_state=canary_state,
        artifacts=artifacts,
        manifest_name=manifest.name,
        bundle_name=bundle.name,
    )
    wait_for_health(name, startup_timeout)
    require_stable_children(name)
    before_restart = restart_count(name)
    checked = post_api(name, "/api/v1/update/check")
    if checked.get("status") != 200:
        raise CanaryError(f"failure canary check failed for {lock['version']}")
    applied = post_api(
        name,
        "/api/v1/update/apply",
        {"version": target_version},
    )
    if applied.get("status") not in {202, None}:
        raise CanaryError(f"failure canary apply failed for {lock['version']}")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    final_restart, state = wait_for_failed_activation(
        name,
        target_version=target_version,
        bundle_sha256=digest,
        previous_restart_count=before_restart,
        timeout_seconds=startup_timeout,
    )
    if final_restart - before_restart != 2:
        raise CanaryError(
            f"{lock['version']} rollback did not use exactly two restarts"
        )
    if read_canary_marker(str(canary_state), ".canary-fault-applied") != component:
        raise CanaryError(
            f"{lock['version']} did not apply the requested {component} fault"
        )
    if read_canary_marker(str(canary_state), ".canary-patch-status") != "patched":
        raise CanaryError(f"{lock['version']} did not install the hermetic fetch patch")
    if read_canary_marker(str(canary_state), ".canary-fetch-complete") != "bundle":
        raise CanaryError(f"{lock['version']} did not fetch the exact candidate bundle")
    manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
    expected_bundle_url = str(
        manifest_document.get("payload", {}).get("bundle_url") or ""
    )
    if (
        not expected_bundle_url
        or read_canary_marker(str(canary_state), ".canary-fetch-last")
        != expected_bundle_url
    ):
        raise CanaryError(f"{lock['version']} did not fetch the expected bundle URL")
    job, scheduler = validate_failed_activation_state(
        state,
        target_version=target_version,
        bundle_sha256=digest,
    )
    apps = running_app_dirs(name)
    if set(apps) != {"core", "ui"} or any(
        app_dir != "/app" for app_dir in apps.values()
    ):
        raise CanaryError(
            f"{lock['version']} rollback did not restore one image runtime"
        )
    supervisor = require_stable_children(name)
    time.sleep(stability_seconds)
    stable_state = runtime_state(name)
    if (
        restart_count(name) != final_restart
        or require_stable_children(name) != supervisor
        or running_app_dirs(name) != apps
        or not health(name, "/healthz/live")
        or not health(name, "/healthz/startup")
        or stable_state != state
    ):
        raise CanaryError(f"{lock['version']} rollback did not remain stable")
    return {
        "scenario": "activation_failure",
        "scenario_key": scenario_key("activation_failure", lock["version"], component),
        "source_version": lock["version"],
        "source_sha": lock["source_sha"],
        "image_index_digest": lock["index_digest"],
        "amd64_digest": lock["amd64_digest"],
        "launcher_protocol": lock["launcher_protocol"],
        "failed_component": component,
        "bundle_sha256": digest,
        "portal_api_verified": True,
        "restart_count_delta": final_restart - before_restart,
        "final_job_status": job.get("status"),
        "rollback_applied": job.get("rollback_applied") is True,
        "failed_identity_quarantined": (
            f"{target_version}:{digest}" in scheduler.get("quarantines", {})
        ),
        "image_runtime_restored": True,
        "fault_applied": True,
        "rollback_target_verified": True,
        "scheduler_attempt_verified": True,
        "supervisor_stable": True,
        "stale_control_file_count": 0,
        "result": "passed",
    }


def run_tamper_canary(
    lock: dict[str, Any],
    *,
    case: str,
    repository: str,
    resources: DockerResources,
    artifacts: Path,
    canary_dir: Path,
    manifest: Path,
    bundle: Path,
    target_version: str,
    startup_timeout: int,
    stability_seconds: int,
) -> dict[str, Any]:
    reference = f"{repository}@{lock['index_digest']}"
    suffix = lock["version"].replace(".", "-")
    volume = resources.volume(f"tamper-{suffix}-{case}")
    init_volume(reference, volume)
    canary_state = resources.state_dir(f"tamper-{suffix}-{case}")
    name = resources.container(f"tamper-{suffix}-{case}")
    start_container(
        resources,
        name,
        reference,
        volume,
        secrets.token_urlsafe(48),
        canary_dir=canary_dir,
        canary_state=canary_state,
        artifacts=artifacts,
        manifest_name=manifest.name,
        bundle_name=bundle.name,
        tamper_case=case,
    )
    wait_for_health(name, startup_timeout)
    initial_supervisor = require_stable_children(name)
    initial_apps = running_app_dirs(name)
    before_restart = restart_count(name)
    checked = post_api(name, "/api/v1/update/check")
    if case == "manifest":
        rejected = isinstance(checked.get("status"), int) and checked["status"] >= 400
    else:
        if checked.get("status") != 200:
            raise CanaryError(f"bundle tamper pre-check failed for {lock['version']}")
        applied = post_api(
            name,
            "/api/v1/update/apply",
            {"version": target_version},
        )
        rejected = isinstance(applied.get("status"), int) and applied["status"] >= 400
    state = runtime_state(name)
    if read_canary_marker(str(canary_state), ".canary-patch-status") != "patched":
        raise CanaryError(
            f"{lock['version']} tamper canary did not patch historical fetch"
        )
    if read_canary_marker(str(canary_state), ".canary-tamper-applied") != case:
        raise CanaryError(
            f"{lock['version']} did not apply the requested {case} tamper"
        )
    manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
    expected_url = (
        "https://channelwatch.coderluii.dev/updates/stable.json"
        if case == "manifest"
        else str(manifest_document.get("payload", {}).get("bundle_url") or "")
    )
    if (
        not expected_url
        or read_canary_marker(str(canary_state), ".canary-fetch-last") != expected_url
    ):
        raise CanaryError(f"{lock['version']} tamper canary fetched an unexpected URL")
    if (
        not rejected
        or "active.json" in state
        or transition_artifacts(state)
        or "official-recovery-mode.json" in state
        or f"v{target_version}" in state.get("release_directories", [])
        or restart_count(name) != before_restart
        or require_stable_children(name) != initial_supervisor
        or running_app_dirs(name) != initial_apps
    ):
        raise CanaryError(f"{lock['version']} accepted a tampered {case}")
    time.sleep(stability_seconds)
    stable_state = runtime_state(name)
    if (
        restart_count(name) != before_restart
        or require_stable_children(name) != initial_supervisor
        or running_app_dirs(name) != initial_apps
        or stable_state != state
        or not health(name, "/healthz/live")
        or not health(name, "/healthz/startup")
    ):
        raise CanaryError(
            f"{lock['version']} changed after rejecting a tampered {case}"
        )
    return {
        "scenario": "tamper_rejection",
        "scenario_key": scenario_key("tamper_rejection", lock["version"], case),
        "source_version": lock["version"],
        "source_sha": lock["source_sha"],
        "image_index_digest": lock["index_digest"],
        "amd64_digest": lock["amd64_digest"],
        "launcher_protocol": lock["launcher_protocol"],
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "tamper_case": case,
        "tamper_applied": True,
        "fetch_transport_verified": True,
        "rejected_before_selection": True,
        "active_unchanged": True,
        "candidate_release_absent": True,
        "supervisor_stable": True,
        "stale_control_file_count": 0,
        "restart_count_delta": 0,
        "result": "passed",
    }


def run_v099_recovery_canary(
    lock: dict[str, Any],
    *,
    repository: str,
    recovery_image: str,
    resources: DockerResources,
    artifacts: Path,
    probe: Path,
    manifest: Path,
    bundle: Path,
    target_version: str,
    public_keys: dict[str, str],
    startup_timeout: int,
    stability_seconds: int,
) -> dict[str, Any]:
    reference = f"{repository}@{lock['index_digest']}"
    inspect_remote_lock(repository, lock)
    inspect_local_image(reference, lock)
    volume = resources.volume("config-0-9-9")
    init_volume(reference, volume)
    storage_key = secrets.token_urlsafe(48)
    primed = prime_update(
        reference,
        volume,
        artifacts,
        probe,
        manifest,
        bundle,
        target_version,
        lock["version"],
        public_keys,
    )
    old_name = resources.container("app-0-9-9")
    start_container(resources, old_name, reference, volume, storage_key)
    wait_for_health(old_name, startup_timeout)
    old_supervisor, old_apps = wait_for_v099_false_success(old_name, startup_timeout)
    old_state = runtime_state(old_name)
    old_active = old_state.get("active.json", {})
    old_job = old_state.get("update-job.json", {})
    if (
        old_active.get("version") != target_version
        or old_job.get("operation") != "apply"
        or old_job.get("status") != "success"
        or old_supervisor.get("core", {}).get("status") != "FATAL"
        or old_supervisor.get("ui", {}).get("status") != "RUNNING"
        or old_apps != {"ui": "/app"}
    ):
        raise CanaryError(
            "v0.9.9 immutable false-success behavior changed unexpectedly"
        )
    run(["docker", "rm", "-f", old_name])
    resources.containers.discard(old_name)

    recovery_name = resources.container("recovery-0-9-9")
    start_container(
        resources,
        recovery_name,
        recovery_image,
        volume,
        storage_key,
    )
    wait_for_health(recovery_name, startup_timeout)
    recovery_state = runtime_state(recovery_name)
    preflight = get_json(recovery_name, "/api/v1/runtime/preflight")
    if preflight.get("status") != 200 or preflight.get("body") != {
        "status": "ready",
        "setup_required": False,
        "blockers": [],
        "warnings": [],
    }:
        raise CanaryError("v0.9.18 recovery image did not restore the managed runtime")
    recovery_active = recovery_state.get("active.json")
    recovery_job = recovery_state.get("update-job.json", {})
    components = recovery_job.get("startup_components")
    components = components if isinstance(components, dict) else {}
    recovery_apps = running_app_dirs(recovery_name)
    recovery_key = recovery_state.get("key", {})
    stale = transition_artifacts(recovery_state)
    if (
        recovery_active is not None
        or recovery_job.get("operation") != "image_refresh_recovery"
        or recovery_job.get("status") != "success"
        or recovery_job.get("image_pull_completed") is not True
        or recovery_job.get("startup_validation_pending") is not False
        or any(
            not isinstance(components.get(mode), dict)
            or components[mode].get("healthy") is not True
            for mode in ("core", "ui")
        )
        or set(recovery_apps) != {"core", "ui"}
        or any(app_dir != "/app" for app_dir in recovery_apps.values())
        or recovery_key
        != {"regular": True, "symlink": False, "links": 1, "size": 32, "mode": 384}
        or stale
    ):
        raise CanaryError("v0.9.18 recovery did not complete the image startup quorum")
    supervisor = require_stable_children(recovery_name)
    recovery_restarts = restart_count(recovery_name)
    time.sleep(stability_seconds)
    if (
        restart_count(recovery_name) != recovery_restarts
        or require_stable_children(recovery_name) != supervisor
        or running_app_dirs(recovery_name) != recovery_apps
        or runtime_state(recovery_name).get("update-job.json", {}).get("status")
        != "success"
        or not health(recovery_name, "/healthz/live")
        or not health(recovery_name, "/healthz/startup")
    ):
        raise CanaryError("v0.9.18 image-pull recovery did not remain stable")
    return {
        "scenario": "image_refresh_recovery",
        "scenario_key": scenario_key("image_refresh_recovery", "0.9.9"),
        "source_version": "0.9.9",
        "source_sha": lock["source_sha"],
        "image_index_digest": lock["index_digest"],
        "amd64_digest": lock["amd64_digest"],
        "launcher_protocol": 0,
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "check_status": primed["check_status"],
        "apply_status": primed["apply_status"],
        "immutable_false_success_observed": True,
        "legacy_core_launcher_failure_observed": True,
        "legacy_ui_image_runtime_verified": True,
        "image_refresh_required": True,
        "recovery_image_cleared_false_success": True,
        "recovery_job_status": "success",
        "recovery_quorum_verified": True,
        "recovery_image_runtime_verified": True,
        "managed_key_verified": True,
        "supervisor_stable": True,
        "stale_control_file_count": 0,
        "result": "passed_with_documented_image_only_limitation",
    }


def run_v010_recovery_canary(
    lock: dict[str, Any],
    *,
    repository: str,
    recovery_image: str,
    resources: DockerResources,
    artifacts: Path,
    canary_dir: Path,
    manifest: Path,
    bundle: Path,
    target_version: str,
    startup_timeout: int,
    stability_seconds: int,
) -> dict[str, Any]:
    """Prove v0.9.10 fails in its immutable entrypoint and recovers by image."""

    reference = f"{repository}@{lock['index_digest']}"
    inspect_remote_lock(repository, lock)
    inspect_local_image(reference, lock)
    volume = resources.volume("config-0-9-10")
    init_volume(reference, volume)
    canary_state = resources.state_dir("recovery-0-9-10")
    storage_key = secrets.token_urlsafe(48)
    old_name = resources.container("app-0-9-10")
    start_container(
        resources,
        old_name,
        reference,
        volume,
        storage_key,
        canary_dir=canary_dir,
        canary_state=canary_state,
        artifacts=artifacts,
        manifest_name=manifest.name,
        bundle_name=bundle.name,
    )
    wait_for_health(old_name, startup_timeout)
    require_stable_children(old_name)
    initial_restart_count = restart_count(old_name)
    checked = post_api(old_name, "/api/v1/update/check")
    if (
        checked.get("status") != 200
        or checked.get("body", {}).get("last_job", {}).get("status") != "available"
    ):
        raise CanaryError("v0.9.10 portal check did not stage the candidate")
    applied = post_api(
        old_name,
        "/api/v1/update/apply",
        {"version": target_version},
    )
    if applied.get("status") not in {202, None}:
        raise CanaryError("v0.9.10 portal apply was rejected before restart")
    observed_restart_count = wait_for_v010_entrypoint_failure(
        old_name,
        previous_restart_count=initial_restart_count,
        timeout_seconds=startup_timeout,
    )
    if read_canary_marker(str(canary_state), ".canary-patch-status") != "patched":
        raise CanaryError("v0.9.10 did not install the hermetic fetch patch")
    if read_canary_marker(str(canary_state), ".canary-fetch-complete") != "bundle":
        raise CanaryError("v0.9.10 did not fetch the exact candidate bundle")
    manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
    expected_bundle_url = str(
        manifest_document.get("payload", {}).get("bundle_url") or ""
    )
    if (
        not expected_bundle_url
        or read_canary_marker(str(canary_state), ".canary-fetch-last")
        != expected_bundle_url
    ):
        raise CanaryError("v0.9.10 did not fetch the expected candidate URL")

    run(["docker", "rm", "-f", old_name])
    resources.containers.discard(old_name)
    legacy_state = volume_runtime_state(reference, volume)
    legacy_active = legacy_state.get("active.json", {})
    legacy_job = legacy_state.get("update-job.json", {})
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    legacy_manifest = (
        legacy_active.get("manifest") if isinstance(legacy_active, dict) else None
    )
    legacy_manifest = legacy_manifest if isinstance(legacy_manifest, dict) else {}
    if (
        legacy_active.get("version") != target_version
        or legacy_manifest.get("bundle_sha256") != digest
        or legacy_job.get("operation") != "apply"
        or legacy_job.get("status") != "restarting"
        or legacy_job.get("version") != target_version
    ):
        raise CanaryError(
            "v0.9.10 entrypoint failure did not preserve the staged candidate "
            "identity: "
            f"active_version={legacy_active.get('version')!r}, "
            f"active_digest_matches={legacy_manifest.get('bundle_sha256') == digest}, "
            f"job_operation={legacy_job.get('operation')!r}, "
            f"job_status={legacy_job.get('status')!r}, "
            f"job_version={legacy_job.get('version')!r}"
        )

    recovery_name = resources.container("recovery-0-9-10")
    start_container(
        resources,
        recovery_name,
        recovery_image,
        volume,
        storage_key,
    )
    wait_for_health(recovery_name, startup_timeout)
    recovery_state = runtime_state(recovery_name)
    preflight = get_json(recovery_name, "/api/v1/runtime/preflight")
    if preflight.get("status") != 200 or preflight.get("body") != {
        "status": "ready",
        "setup_required": False,
        "blockers": [],
        "warnings": [],
    }:
        raise CanaryError("v0.9.18 did not recover the v0.9.10 configuration")
    recovery_active = recovery_state.get("active.json")
    recovery_job = recovery_state.get("update-job.json", {})
    components = recovery_job.get("startup_components")
    components = components if isinstance(components, dict) else {}
    recovery_apps = running_app_dirs(recovery_name)
    recovery_key = recovery_state.get("key", {})
    stale = transition_artifacts(recovery_state)
    if (
        recovery_active is not None
        or recovery_job.get("operation") != "image_refresh_recovery"
        or recovery_job.get("status") != "success"
        or recovery_job.get("image_pull_completed") is not True
        or recovery_job.get("startup_validation_pending") is not False
        or any(
            not isinstance(components.get(mode), dict)
            or components[mode].get("healthy") is not True
            for mode in ("core", "ui")
        )
        or set(recovery_apps) != {"core", "ui"}
        or any(app_dir != "/app" for app_dir in recovery_apps.values())
        or recovery_key
        != {"regular": True, "symlink": False, "links": 1, "size": 32, "mode": 384}
        or stale
    ):
        raise CanaryError("v0.9.10 image refresh did not complete recovery quorum")
    supervisor = require_stable_children(recovery_name)
    recovery_restarts = restart_count(recovery_name)
    time.sleep(stability_seconds)
    if (
        restart_count(recovery_name) != recovery_restarts
        or require_stable_children(recovery_name) != supervisor
        or running_app_dirs(recovery_name) != recovery_apps
        or runtime_state(recovery_name).get("update-job.json", {}).get("status")
        != "success"
        or not health(recovery_name, "/healthz/live")
        or not health(recovery_name, "/healthz/startup")
    ):
        raise CanaryError("v0.9.10 image-pull recovery did not remain stable")
    return {
        "scenario": "image_refresh_recovery",
        "scenario_key": scenario_key("image_refresh_recovery", "0.9.10"),
        "source_version": "0.9.10",
        "source_sha": lock["source_sha"],
        "image_index_digest": lock["index_digest"],
        "amd64_digest": lock["amd64_digest"],
        "launcher_protocol": 1,
        "bundle_sha256": digest,
        "check_status": "available",
        "apply_status": (
            applied.get("body", {}).get("status")
            if applied.get("status") == 202
            else "connection_closed_for_restart"
        ),
        "portal_api_verified": True,
        "immutable_entrypoint_failure_observed": True,
        "legacy_restart_loop_observed": True,
        "legacy_restart_count_at_least": 2,
        "legacy_staged_identity_preserved": True,
        "image_refresh_required": True,
        "recovery_image_cleared_failed_activation": True,
        "recovery_job_status": "success",
        "recovery_quorum_verified": True,
        "recovery_image_runtime_verified": True,
        "managed_key_verified": True,
        "supervisor_stable": True,
        "stale_control_file_count": 0,
        "restart_count": observed_restart_count,
        "result": "passed_with_documented_image_only_limitation",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--version", default="0.9.18")
    parser.add_argument("--repository", default="coderluii/channelwatch")
    parser.add_argument("--public-key", action="append")
    parser.add_argument("--recovery-image", required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--stability-seconds", type=int, default=20)
    parser.add_argument("--version-source", action="append", dest="versions")
    args = parser.parse_args()

    manifest = args.manifest.resolve()
    bundle = args.bundle.resolve()
    if not manifest.is_file() or not bundle.is_file():
        raise CanaryError("signed manifest and bundle are required")
    public_keys = parse_public_keys(args.public_key)
    locks = load_locks()
    inspect_recovery_image(args.recovery_image, args.version)
    selected = set(args.versions or EXPECTED_VERSIONS)
    unknown = selected - {str(lock["version"]) for lock in locks}
    if unknown:
        raise CanaryError(f"unknown historical versions requested: {sorted(unknown)}")

    prefix = f"cw-historical-{os.getpid()}-{secrets.token_hex(3)}"
    previous_handlers: dict[int, Any] = {}
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="channelwatch-image-canary-") as temp:
        resources = DockerResources(prefix, Path(temp) / "configs")
        atexit.register(resources.cleanup)

        def handle_signal(signum: int, _frame: Any) -> None:
            resources.cleanup()
            raise SystemExit(128 + signum)

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, handle_signal)
        try:
            probe = Path(temp) / "prime.py"
            write_probe(probe)
            canary_dir = Path(temp) / "canary"
            canary_dir.mkdir(mode=0o700)
            write_sitecustomize(canary_dir / "sitecustomize.py")
            (canary_dir / "public-keys.json").write_text(
                json.dumps(public_keys, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            artifacts = manifest.parent
            if bundle.parent != artifacts:
                raise CanaryError(
                    "manifest and bundle must share one artifact directory"
                )
            for lock in locks:
                if lock["version"] in selected and lock["support"] == "app_update":
                    results.append(
                        run_supported_canary(
                            lock,
                            repository=args.repository,
                            resources=resources,
                            artifacts=artifacts,
                            canary_dir=canary_dir,
                            probe=probe,
                            manifest=manifest,
                            bundle=bundle,
                            target_version=args.version,
                            public_keys=public_keys,
                            startup_timeout=args.startup_timeout,
                            stability_seconds=args.stability_seconds,
                        )
                    )
            by_version = {str(lock["version"]): lock for lock in locks}
            for source_version, component in FAILURE_CANARIES.items():
                if source_version in selected:
                    results.append(
                        run_activation_failure_canary(
                            by_version[source_version],
                            component=component,
                            repository=args.repository,
                            resources=resources,
                            artifacts=artifacts,
                            canary_dir=canary_dir,
                            manifest=manifest,
                            bundle=bundle,
                            target_version=args.version,
                            startup_timeout=args.startup_timeout,
                            stability_seconds=args.stability_seconds,
                        )
                    )
            for source_version in TAMPER_CANARY_VERSIONS:
                if source_version in selected:
                    for case in ("manifest", "bundle"):
                        results.append(
                            run_tamper_canary(
                                by_version[source_version],
                                case=case,
                                repository=args.repository,
                                resources=resources,
                                artifacts=artifacts,
                                canary_dir=canary_dir,
                                manifest=manifest,
                                bundle=bundle,
                                target_version=args.version,
                                startup_timeout=args.startup_timeout,
                                stability_seconds=args.stability_seconds,
                            )
                        )
            if "0.9.9" in selected:
                v099 = next(lock for lock in locks if lock["version"] == "0.9.9")
                results.append(
                    run_v099_recovery_canary(
                        v099,
                        repository=args.repository,
                        recovery_image=args.recovery_image,
                        resources=resources,
                        artifacts=artifacts,
                        probe=probe,
                        manifest=manifest,
                        bundle=bundle,
                        target_version=args.version,
                        public_keys=public_keys,
                        startup_timeout=args.startup_timeout,
                        stability_seconds=args.stability_seconds,
                    )
                )
            if "0.9.10" in selected:
                v010 = next(lock for lock in locks if lock["version"] == "0.9.10")
                results.append(
                    run_v010_recovery_canary(
                        v010,
                        repository=args.repository,
                        recovery_image=args.recovery_image,
                        resources=resources,
                        artifacts=artifacts,
                        canary_dir=canary_dir,
                        manifest=manifest,
                        bundle=bundle,
                        target_version=args.version,
                        startup_timeout=args.startup_timeout,
                        stability_seconds=args.stability_seconds,
                    )
                )
        finally:
            resources.cleanup()
            atexit.unregister(resources.cleanup)
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)

    validate_scenario_rows(results)
    output = {
        "schema": 2,
        "target_version": args.version,
        "platform": PLATFORM,
        "results": results,
        "passed": all(
            str(item.get("result", "")).startswith("passed") for item in results
        ),
    }
    args.evidence_json.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_json.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not output["passed"]:
        raise CanaryError("historical image canary matrix did not pass")
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CanaryError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"historical image canary failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
