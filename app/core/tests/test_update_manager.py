import base64
import io
import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.update_center import (
    LOCK_STALE_SECONDS,
    RUNTIME_ABI,
    UpdateBundleError,
    UpdateLockedError,
    UpdateManager,
    UpdateOperationLock,
    canonical_payload_bytes,
    sha256_hex,
    validate_bundle_archive,
)


def _key_pair():
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return private, {"test-key": public}


def _bundle(version: str = "0.9.10", *, extra: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("core/main.py", "print('core')\n")
        zf.writestr("ui/backend/main.py", "print('ui')\n")
        zf.writestr(
            "channelwatch-bundle.json",
            json.dumps(
                {
                    "version": version,
                    "runtime_abi": RUNTIME_ABI,
                    "settings_schema_version": 7,
                }
            ),
        )
        for name, value in (extra or {}).items():
            zf.writestr(name, value)
    return buf.getvalue()


def _manifest(private, bundle: bytes, version: str = "0.9.10", *, image_required: bool = False) -> bytes:
    digest = bytes.fromhex(sha256_hex(bundle))
    payload = {
        "version": version,
        "version_tag": f"v{version}",
        "channel": "stable",
        "runtime_abi": RUNTIME_ABI,
        "settings_schema_version": 7,
        "image_required": image_required,
        "release_url": f"https://github.com/CoderLuii/ChannelWatch/releases/tag/v{version}",
        "bundle_url": f"https://github.com/CoderLuii/ChannelWatch/releases/download/v{version}/channelwatch-app-v{version}.zip",
        "bundle_sha256": digest.hex(),
        "bundle_signature": base64.b64encode(private.sign(digest)).decode("ascii"),
        "key_id": "test-key",
        "highlights": ["Test update"],
    }
    manifest = {
        "schema": 1,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": "test-key",
            "value": base64.b64encode(private.sign(canonical_payload_bytes(payload))).decode("ascii"),
        },
    }
    return json.dumps(manifest).encode("utf-8")


def test_check_marks_image_required_release(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle, image_required=True)

    def fetcher(url: str, max_bytes: int) -> bytes:
        assert "channelwatch.coderluii.dev" in url
        return manifest

    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=fetcher,
    )

    status = manager.check()

    assert status["update_available"] is True
    assert status["image_required"] is True
    assert status["last_job"]["status"] == "image_required"


def test_apply_verified_bundle_records_backup_and_active_bundle(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)

    def fetcher(url: str, max_bytes: int) -> bytes:
        return bundle if url.endswith(".zip") else manifest

    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=fetcher,
        backup_callable=lambda config_dir: b"backup-bytes",
        restart_callable=lambda: True,
    )

    manager.check()
    job = manager.apply()

    active = json.loads((tmp_path / "channelwatch-runtime" / "active.json").read_text())
    assert job["status"] == "restarting"
    assert active["version"] == "0.9.10"
    assert (tmp_path / "channelwatch-runtime" / "releases" / "v0.9.10" / "core" / "main.py").exists()
    assert list((tmp_path / "backups").glob("pre-update.v0.9.10.*.zip"))


def test_bundle_validation_rejects_unsafe_member():
    bad_bundle = _bundle(extra={"docs/internal.md": "private process notes"})

    with pytest.raises(UpdateBundleError, match="unsupported member"):
        validate_bundle_archive(
            bad_bundle,
            expected_version="0.9.10",
            expected_runtime_abi=RUNTIME_ABI,
            expected_settings_schema_version=7,
        )


def test_single_flight_lock_blocks_parallel_operations(tmp_path: Path):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"

    with UpdateOperationLock(lock_path):
        with pytest.raises(UpdateLockedError):
            with UpdateOperationLock(lock_path):
                pass

    assert not lock_path.exists()


def test_stale_single_flight_lock_is_discarded(tmp_path: Path):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 123}')
    stale_at = time.time() - LOCK_STALE_SECONDS - 10
    os.utime(lock_path, (stale_at, stale_at))

    with UpdateOperationLock(lock_path):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_startup_success_marks_restarting_job_success(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
    )
    manager.check()
    manager.apply()

    manager.record_startup_success()

    job = json.loads((tmp_path / "channelwatch-runtime" / "update-job.json").read_text())
    assert job["status"] == "success"
    assert job["validated_at"]


def test_health_validation_failure_rolls_back_active_bundle(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    restarts: list[bool] = []
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: not restarts.append(True),
        healthcheck_callable=lambda: False,
    )
    manager.check()
    manager.apply()

    manager.record_startup_success()

    runtime_dir = tmp_path / "channelwatch-runtime"
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert job["rolled_back_to"] == "image"
    assert restarts == [True, True]
    assert not (runtime_dir / "active.json").exists()
