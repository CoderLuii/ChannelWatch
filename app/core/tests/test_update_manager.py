import base64
import io
import json
import os
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import update_center
from core.update_center import (
    LOCK_STALE_SECONDS,
    RUNTIME_ABI,
    UpdateBundleError,
    UpdateLockedError,
    UpdateManager,
    UpdateManifestError,
    UpdateOperationLock,
    UpdateRestartError,
    canonical_payload_bytes,
    fetch_bytes,
    sha256_hex,
    validate_bundle_archive,
)


class _SimulatedPowerLoss(BaseException):
    pass


_RESTART_REPLAY_PHASES = (
    "journal",
    "activation-state-removed",
    "control:rollback.json",
    "control:activation-pending.json",
    "control:activation-core-ready.json",
    "control:activation-ui-ready.json",
    "control:update-job.json",
    "control:active.json",
    "control:fsynced",
)


def _assert_schema_2_journal_blocks_and_replays(
    runtime_dir: Path, monkeypatch, *, operation: str, phase: str = "commit"
) -> None:
    from core import runtime_launcher

    journal_path = runtime_dir / "restart-required.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert set(journal) == update_center.RESTART_JOURNAL_FIELDS
    assert journal["schema"] == 2
    assert journal["operation"] == operation
    assert journal["phase"] == phase
    assert set(journal["control"]) == set(update_center.RESTART_CONTROL_FILES)

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(runtime_launcher, "request_container_restart", lambda: None)
    monkeypatch.setattr(
        runtime_launcher,
        "selected_app_dir",
        lambda: pytest.fail("a journaled transition must block pinned app launch"),
    )
    assert runtime_launcher.main(["core"]) == 1

    # Replay is deliberately idempotent and is compatible with the
    # image-stable launcher/entrypoint consumer contract.
    runtime_launcher.apply_restart_journal()
    for name, expected in journal["control"].items():
        path = runtime_dir / name
        if expected is None:
            assert not path.exists()
        else:
            assert json.loads(path.read_text(encoding="utf-8")) == expected


def _acknowledge_entrypoint_handoff(manager: UpdateManager) -> None:
    """Model the new container replaying and pinning the journaled selection."""

    journal = manager.apply_restart_journal()
    manager._clear_restart_journal(journal)


def _complete_selected_activation(manager: UpdateManager) -> dict:
    """Model entrypoint acknowledgement and both healthy startup markers."""

    _acknowledge_entrypoint_handoff(manager)
    active = json.loads(manager.active_path.read_text(encoding="utf-8"))
    for component in ("core", "ui"):
        manager.record_startup_success(
            component=component,
            running_version=str(active["version"]),
            activation_id=str(active["activation_id"]),
            healthy=True,
        )
    return active


def _foreign_restart_journal(
    manager: UpdateManager,
    *,
    job_id: str = "foreign-job",
    control: dict | None = None,
) -> dict:
    selected_control = control or manager._read_control_state()
    foreign_job = {
        "job_id": job_id,
        "operation": "apply",
        "status": "failed",
        "message": "foreign generation owns restart",
    }
    selected_control = {
        **selected_control,
        "update-job.json": foreign_job,
    }
    active = selected_control.get("active.json")
    return manager._build_restart_journal(
        reason="activation_rollback",
        operation="activation_rollback",
        phase="commit",
        job_id=job_id,
        source_active=active if isinstance(active, dict) else None,
        control=selected_control,
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


def _manifest(
    private, bundle: bytes, version: str = "0.9.10", *, image_required: bool = False
) -> bytes:
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
            "value": base64.b64encode(
                private.sign(canonical_payload_bytes(payload))
            ).decode("ascii"),
        },
    }
    return json.dumps(manifest).encode("utf-8")


def test_fetch_bytes_rejects_an_untrusted_redirect_before_following(monkeypatch):
    class RedirectingOpener:
        def __init__(self, redirect_handler):
            self.redirect_handler = redirect_handler

        def open(self, request, timeout):
            trusted_request = self.redirect_handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://github.com/CoderLuii/ChannelWatch/releases/download/v0.9.10/update.zip",
            )
            assert trusted_request is not None
            self.redirect_handler.redirect_request(
                trusted_request,
                None,
                302,
                "Found",
                {},
                "https://attacker.example/update.zip",
            )
            pytest.fail("untrusted redirect should not be followed")

    def build_opener(*handlers):
        redirect_handler = next(
            handler
            for handler in handlers
            if isinstance(handler, urllib.request.HTTPRedirectHandler)
        )
        return RedirectingOpener(redirect_handler)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    with pytest.raises(UpdateManifestError, match="host is not trusted"):
        fetch_bytes(
            "https://channelwatch.coderluii.dev/updates/stable.json",
            max_bytes=1024,
        )


def test_validate_trusted_url_rejects_non_ascii_hosts_before_urllib_idna():
    with pytest.raises(UpdateManifestError, match="host must use ASCII"):
        update_center.validate_trusted_url(
            "https://channelw\N{LATIN SMALL LETTER A WITH RING ABOVE}tch.coderluii.dev/update.json"
        )


def test_fetch_bytes_rejects_an_untrusted_final_url_before_reading(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return "https://attacker.example/update.zip"

        def read(self, size):
            pytest.fail("untrusted response body should not be read")

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())

    with pytest.raises(UpdateManifestError, match="host is not trusted"):
        fetch_bytes(
            "https://channelwatch.coderluii.dev/updates/stable.json",
            max_bytes=1024,
        )


def test_fetch_bytes_accepts_github_release_asset_cdn_redirect(monkeypatch):
    class Response:
        def __init__(self):
            self.read_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return "https://release-assets.githubusercontent.com/release.zip"

        def read(self, size):
            self.read_count += 1
            return b"bundle" if self.read_count == 1 else b""

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())

    assert (
        fetch_bytes(
            "https://github.com/CoderLuii/ChannelWatch/releases/download/v0.9.16/channelwatch-app-v0.9.16.zip",
            max_bytes=1024,
        )
        == b"bundle"
    )


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


def test_status_quarantines_a_cached_release_older_than_the_active_app(
    tmp_path: Path,
):
    manager = UpdateManager(config_dir=tmp_path, current_version="1.0.0")
    manager._ensure_runtime()
    manager.latest_path.write_text(
        json.dumps(
            {
                "schema": 2,
                "payload": {
                    "version": "0.9.19",
                    "version_tag": "v0.9.19",
                    "delivery_mode": "image_required",
                    "highlights": ["Outdated release text"],
                },
            }
        ),
        encoding="utf-8",
    )

    status = manager.status()

    assert status["catalog_state"] == "stale_cache"
    assert status["cached_release_stale"] is True
    assert status["latest"] is None
    assert status["trusted_target"] is None
    assert status["update_available"] is False
    assert status["image_required"] is False
    assert status["operation_state"] == "idle"
    assert status["operation_busy"] is False


def test_manager_prefers_embedded_image_version_over_stale_environment(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    (image_dir / "channelwatch-image.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "runtime_abi": "channelwatch-runtime-v1",
                "settings_schema_version": 7,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.update_center.DEFAULT_IMAGE_APP_DIR", image_dir)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.17")

    manager = UpdateManager(config_dir=tmp_path / "config", current_version="1.0.8")

    assert manager.image_version == "1.0.0"
    assert manager.launcher_protocol == 3


def test_status_reports_an_equal_cached_release_as_current_not_a_target(
    tmp_path: Path,
):
    manager = UpdateManager(config_dir=tmp_path, current_version="1.0.0")
    manager._ensure_runtime()
    payload = {
        "version": "1.0.0",
        "version_tag": "v1.0.0",
        "delivery_mode": "image_required",
    }
    manager.latest_path.write_text(
        json.dumps({"schema": 2, "payload": payload}), encoding="utf-8"
    )

    status = manager.status()

    assert status["catalog_state"] == "current"
    assert status["cached_release_stale"] is False
    assert status["latest"] == payload
    assert status["trusted_target"] is None
    assert status["update_available"] is False
    assert status["operation_busy"] is False


def test_status_derives_busy_state_from_the_live_single_flight_lock(tmp_path: Path):
    manager = UpdateManager(config_dir=tmp_path, current_version="1.0.0")
    manager._ensure_runtime()

    with UpdateOperationLock(manager.lock_path):
        status = manager.status()

    assert status["catalog_state"] == "checking"
    assert status["operation_state"] == "checking"
    assert status["operation_busy"] is True
    assert manager.status()["operation_state"] == "idle"


def test_status_discards_a_dead_operation_lock(tmp_path: Path):
    manager = UpdateManager(config_dir=tmp_path, current_version="1.0.0")
    manager._ensure_runtime()
    manager.lock_path.write_text(
        json.dumps(
            {
                "pid": 2_000_000_000,
                "process_identity": "old-boot:old-namespace:old-start",
                "created_at": "2026-08-26T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    status = manager.status()

    assert status["operation_state"] == "idle"
    assert status["operation_busy"] is False
    assert not manager.lock_path.exists()


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
    assert (
        tmp_path / "channelwatch-runtime" / "releases" / "v0.9.10" / "core" / "main.py"
    ).exists()
    assert list((tmp_path / "backups").glob("pre-update.v0.9.10.*.zip"))


def test_apply_correlation_and_digest_survive_activation_success(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    digest = sha256_hex(bundle)
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
    )
    manager.check()

    job = manager.apply(
        job_id="scheduler-job-1",
        scheduler_attempt_id="window-attempt-1",
        expected_bundle_sha256=digest,
    )
    assert job["job_id"] == "scheduler-job-1"
    assert job["scheduler_attempt_id"] == "window-attempt-1"
    assert job["bundle_sha256"] == digest
    _complete_selected_activation(manager)

    completed = manager.status()["last_job"]
    assert completed["status"] == "success"
    assert completed["job_id"] == "scheduler-job-1"
    assert completed["scheduler_attempt_id"] == "window-attempt-1"
    assert completed["bundle_sha256"] == digest


def test_manual_rollback_to_image_finishes_after_core_and_ui_quorum(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    (runtime_dir / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "rollback-image",
                "operation": "rollback",
                "status": "restarting",
                "version": "0.9.19",
                "rolled_back_from": "0.9.19",
                "restart_required": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(image_dir))
    monkeypatch.setenv("CHANNELWATCH_IMAGE_APP_DIR", str(image_dir))
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.18",
        launcher_protocol=3,
    )

    manager.record_startup_success(
        component="core",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    validating = json.loads(manager.job_path.read_text(encoding="utf-8"))
    assert validating["status"] == "validating"
    assert validating["rollback_applied"] is True

    manager.record_startup_success(
        component="ui",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    completed = json.loads(manager.job_path.read_text(encoding="utf-8"))
    assert completed["status"] == "success"
    assert completed["restored_version"] == "0.9.18"
    assert completed["restart_required"] is False
    assert set(completed["startup_components"]) == {"core", "ui"}


def test_manual_rollback_to_bundle_rejects_stale_runtime_then_finishes(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    bundle_dir = runtime_dir / "releases" / "v0.9.18"
    (bundle_dir / "core").mkdir(parents=True)
    (bundle_dir / "ui" / "backend").mkdir(parents=True)
    (bundle_dir / "core" / "main.py").write_text("# core\n", encoding="utf-8")
    (bundle_dir / "ui" / "backend" / "main.py").write_text(
        "# ui\n", encoding="utf-8"
    )
    active = {
        "version": "0.9.18",
        "path": str(bundle_dir),
        "runtime_abi": RUNTIME_ABI,
        "settings_schema_version": 7,
    }
    (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")
    initial_job = {
        "job_id": "rollback-bundle",
        "operation": "rollback",
        "status": "restarting",
        "version": "0.9.19",
        "rolled_back_from": "0.9.19",
        "restart_required": True,
    }
    (runtime_dir / "update-job.json").write_text(
        json.dumps(initial_job), encoding="utf-8"
    )
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.17",
        launcher_protocol=3,
    )

    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(tmp_path / "stale-runtime"))
    manager.record_startup_success(
        component="core",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    assert json.loads(manager.job_path.read_text(encoding="utf-8")) == initial_job

    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(bundle_dir))
    for component in ("core", "ui"):
        manager.record_startup_success(
            component=component,
            running_version="0.9.18",
            activation_id="",
            healthy=True,
        )
    completed = json.loads(manager.job_path.read_text(encoding="utf-8"))
    assert completed["status"] == "success"
    assert completed["restored_version"] == "0.9.18"


def test_apply_rejects_scheduler_digest_changed_after_check(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
    )
    manager.check()

    with pytest.raises(UpdateManifestError, match="digest changed"):
        manager.apply(
            job_id="scheduler-job-1",
            scheduler_attempt_id="window-attempt-1",
            expected_bundle_sha256="f" * 64,
        )

    assert not manager.active_path.exists()


def test_apply_correlation_and_digest_survive_activation_rollback(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    digest = sha256_hex(bundle)
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
    )
    manager.check()
    manager.apply(
        job_id="scheduler-job-2",
        scheduler_attempt_id="window-attempt-2",
        expected_bundle_sha256=digest,
    )
    _acknowledge_entrypoint_handoff(manager)

    failed = manager.record_activation_failure_and_rollback("simulated failure")

    assert failed["status"] == "failed"
    assert failed["rollback_applied"] is True
    assert failed["job_id"] == "scheduler-job-2"
    assert failed["scheduler_attempt_id"] == "window-attempt-2"
    assert failed["bundle_sha256"] == digest


@pytest.mark.parametrize("crash_phase", _RESTART_REPLAY_PHASES)
def test_apply_power_loss_at_every_restart_transaction_boundary_is_replayable(
    tmp_path: Path, monkeypatch, crash_phase: str
):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: pytest.fail("restart must follow complete replay"),
    )
    manager.check()

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", crash)
    with pytest.raises(_SimulatedPowerLoss, match=crash_phase):
        manager.apply()

    _assert_schema_2_journal_blocks_and_replays(
        tmp_path / "channelwatch-runtime", monkeypatch, operation="apply"
    )


@pytest.mark.parametrize("crash_phase", _RESTART_REPLAY_PHASES)
def test_manual_rollback_power_loss_at_every_boundary_is_replayable(
    tmp_path: Path, monkeypatch, crash_phase: str
):
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
    _complete_selected_activation(manager)

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", crash)
    with pytest.raises(_SimulatedPowerLoss, match=crash_phase):
        manager.rollback()

    _assert_schema_2_journal_blocks_and_replays(
        tmp_path / "channelwatch-runtime",
        monkeypatch,
        operation="manual_rollback",
    )


@pytest.mark.parametrize("crash_phase", _RESTART_REPLAY_PHASES)
def test_activation_rollback_power_loss_at_every_boundary_is_replayable(
    tmp_path: Path, monkeypatch, crash_phase: str
):
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
    _acknowledge_entrypoint_handoff(manager)

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", crash)
    with pytest.raises(_SimulatedPowerLoss, match=crash_phase):
        manager.record_activation_failure_and_rollback("simulated startup failure")

    _assert_schema_2_journal_blocks_and_replays(
        tmp_path / "channelwatch-runtime",
        monkeypatch,
        operation="activation_rollback",
    )


@pytest.mark.parametrize("crash_phase", _RESTART_REPLAY_PHASES)
def test_apply_restart_rejection_reversal_is_crash_replayable(
    tmp_path: Path, monkeypatch, crash_phase: str
):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: False,
    )
    manager.check()

    def crash(phase: str) -> None:
        journal = update_center.load_json(manager.restart_required_path, {})
        if journal.get("phase") == "abort" and phase == crash_phase:
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", crash)
    with pytest.raises(_SimulatedPowerLoss, match=crash_phase):
        manager.apply()

    runtime_dir = tmp_path / "channelwatch-runtime"
    _assert_schema_2_journal_blocks_and_replays(
        runtime_dir, monkeypatch, operation="apply", phase="abort"
    )
    journal = json.loads((runtime_dir / "restart-required.json").read_text())
    assert journal["phase"] == "abort"
    assert journal["control"]["active.json"] is None
    assert journal["control"]["update-job.json"]["status"] == "failed"


@pytest.mark.parametrize("crash_phase", _RESTART_REPLAY_PHASES)
def test_manual_rollback_restart_rejection_reversal_is_crash_replayable(
    tmp_path: Path, monkeypatch, crash_phase: str
):
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
    _complete_selected_activation(manager)
    selected = json.loads((manager.active_path).read_text())
    manager.restart_callable = lambda: False

    def crash(phase: str) -> None:
        journal = update_center.load_json(manager.restart_required_path, {})
        if journal.get("phase") == "abort" and phase == crash_phase:
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", crash)
    with pytest.raises(_SimulatedPowerLoss, match=crash_phase):
        manager.rollback()

    runtime_dir = tmp_path / "channelwatch-runtime"
    _assert_schema_2_journal_blocks_and_replays(
        runtime_dir,
        monkeypatch,
        operation="manual_rollback",
        phase="abort",
    )
    journal = json.loads((runtime_dir / "restart-required.json").read_text())
    assert journal["phase"] == "abort"
    assert journal["control"]["active.json"] == selected
    assert journal["control"]["update-job.json"]["status"] == "failed"


def test_apply_restart_failure_restores_previous_runtime_control_state(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        backup_callable=lambda config_dir: b"backup-bytes",
        restart_callable=lambda: False,
    )
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(parents=True)
    old_rollback = {"previous_active": {"version": "0.9.8", "path": "old"}}
    (runtime_dir / "rollback.json").write_text(json.dumps(old_rollback))

    manager.check()
    job = manager.apply()

    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert not (runtime_dir / "active.json").exists()
    assert not (runtime_dir / "activation-pending.json").exists()
    assert json.loads((runtime_dir / "rollback.json").read_text()) == old_rollback


def test_apply_restart_exception_restores_previous_runtime_selection(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    manager.check()
    job = manager.apply()

    assert job["status"] == "failed"
    assert "offline" in job["error"]
    assert not (tmp_path / "channelwatch-runtime" / "active.json").exists()


def test_apply_production_restart_adapter_restores_previous_runtime_selection(
    tmp_path: Path, monkeypatch
):
    import ui.backend.main as ui_main
    from core import runtime_launcher

    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=ui_main._schedule_container_restart_for_update,
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: (_ for _ in ()).throw(RuntimeError("supervisor unavailable")),
    )

    manager.check()
    job = manager.apply()

    runtime_dir = tmp_path / "channelwatch-runtime"
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert "supervisor unavailable" not in str(job)
    assert not (runtime_dir / "active.json").exists()
    assert not (runtime_dir / "activation-pending.json").exists()


@pytest.mark.parametrize("callback_outcome", ["false", "raise"])
def test_protocol_three_apply_keeps_consumed_handoff_when_callback_reply_is_lost(
    tmp_path: Path, monkeypatch, callback_outcome: str
):
    from core import runtime_launcher

    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    runtime_dir = tmp_path / "channelwatch-runtime"
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        launcher_protocol=3,
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    def consume_then_lose_reply() -> bool:
        assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
            old_processes={
                "core": {"pid": 101, "start": 1_700_000_001},
                "ui": {"pid": 102, "start": 1_700_000_002},
            }
        )
        assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
        if callback_outcome == "raise":
            raise RuntimeError("restart acknowledgement connection closed")
        return False

    manager.restart_callable = consume_then_lose_reply
    manager.check()
    job = manager.apply()

    assert job["status"] == "restarting"
    assert job["version"] == "0.9.19"
    assert not manager.restart_required_path.exists()
    assert not (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).exists()
    assert json.loads(manager.active_path.read_text())["version"] == "0.9.19"
    assert json.loads(manager.activation_pending_path.read_text())["job_id"] == (
        job["job_id"]
    )
    assert json.loads(manager.job_path.read_text())["status"] == "restarting"


@pytest.mark.parametrize("callback_outcome", ["false", "raise"])
def test_protocol_three_apply_keeps_published_handoff_when_reply_is_lost(
    tmp_path: Path, monkeypatch, callback_outcome: str
):
    from core import runtime_launcher

    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    runtime_dir = tmp_path / "channelwatch-runtime"
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        launcher_protocol=3,
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    def publish_then_lose_reply() -> bool:
        assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
            old_processes={
                "core": {"pid": 101, "start": 1_700_000_001},
                "ui": {"pid": 102, "start": 1_700_000_002},
            }
        )
        if callback_outcome == "raise":
            raise RuntimeError("restart acknowledgement connection closed")
        return False

    manager.restart_callable = publish_then_lose_reply
    manager.check()
    job = manager.apply()

    assert job["status"] == "restarting"
    assert manager.restart_required_path.is_file()
    assert manager.protocol_three_handoff_path.is_file()
    assert json.loads(manager.restart_required_path.read_text())["phase"] == "commit"
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
    assert not manager.restart_required_path.exists()
    assert not manager.protocol_three_handoff_path.exists()


def test_protocol_three_abort_rechecks_marker_published_after_reconciliation(
    tmp_path: Path, monkeypatch
):
    from core import runtime_launcher

    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    runtime_dir = tmp_path / "channelwatch-runtime"
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        launcher_protocol=3,
        restart_callable=lambda: False,
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    original_result = manager._protocol_three_handoff_result
    reconciliation_calls = 0

    def publish_at_abort_boundary(
        expected_journal,
        *,
        wait_for_active_helper=False,
    ):
        del wait_for_active_helper
        nonlocal reconciliation_calls
        reconciliation_calls += 1
        if reconciliation_calls == 1:
            assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
                old_processes={
                    "core": {"pid": 101, "start": 1_700_000_001},
                    "ui": {"pid": 102, "start": 1_700_000_002},
                },
                expected_journal=expected_journal,
            )
            return None
        return original_result(expected_journal)

    monkeypatch.setattr(
        manager,
        "_protocol_three_handoff_result",
        publish_at_abort_boundary,
    )
    manager.check()
    job = manager.apply()

    assert reconciliation_calls == 2
    assert job["status"] == "restarting"
    assert json.loads(manager.restart_required_path.read_text())["phase"] == "commit"
    assert manager.protocol_three_handoff_path.is_file()
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
    assert not manager.restart_required_path.exists()
    assert not manager.protocol_three_handoff_path.exists()


def test_protocol_three_duplicate_callback_waits_for_active_helper_marker(
    tmp_path: Path, monkeypatch
):
    from core import runtime_launcher
    from core import update_center as update_center_module

    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    runtime_dir = tmp_path / "channelwatch-runtime"
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        launcher_protocol=3,
        restart_callable=lambda: False,
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        manager,
        "_protocol_three_restart_helper_active",
        lambda: True,
    )
    wait_calls = 0

    def publish_during_reconciliation(_delay: float) -> None:
        nonlocal wait_calls
        wait_calls += 1
        if not manager.protocol_three_handoff_path.exists():
            assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
                old_processes={
                    "core": {"pid": 101, "start": 1_700_000_001},
                    "ui": {"pid": 102, "start": 1_700_000_002},
                }
            )

    monkeypatch.setattr(
        update_center_module,
        "_protocol_three_sleep",
        publish_during_reconciliation,
    )
    manager.check()
    job = manager.apply()

    assert wait_calls >= 1
    assert job["status"] == "restarting"
    assert json.loads(manager.restart_required_path.read_text())["phase"] == "commit"
    assert manager.protocol_three_handoff_path.is_file()
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
    assert not manager.restart_required_path.exists()
    assert not manager.protocol_three_handoff_path.exists()


def test_protocol_three_inactive_helper_branch_rechecks_consumed_result(
    tmp_path: Path, monkeypatch
):
    from core import runtime_launcher

    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    runtime_dir = tmp_path / "channelwatch-runtime"
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        launcher_protocol=3,
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    def publish_then_lose_reply() -> bool:
        assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
            old_processes={
                "core": {"pid": 101, "start": 1_700_000_001},
                "ui": {"pid": 102, "start": 1_700_000_002},
            }
        )
        return False

    manager.restart_callable = publish_then_lose_reply
    original_once = manager._protocol_three_handoff_result_once
    once_calls = 0

    def miss_before_child_consumption(expected_journal):
        nonlocal once_calls
        once_calls += 1
        if once_calls == 1:
            return None
        return original_once(expected_journal)

    def helper_releases_after_child_consumes() -> bool:
        assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
        return False

    monkeypatch.setattr(
        manager,
        "_protocol_three_handoff_result_once",
        miss_before_child_consumption,
    )
    monkeypatch.setattr(
        manager,
        "_protocol_three_restart_helper_active",
        helper_releases_after_child_consumes,
    )
    monkeypatch.setattr(
        update_center,
        "PROTOCOL_THREE_RECONCILE_GRACE_SECONDS",
        0.0,
    )
    manager.check()
    job = manager.apply()

    assert once_calls == 2
    assert job["status"] == "restarting"
    assert not manager.restart_required_path.exists()
    assert not manager.protocol_three_handoff_path.exists()
    assert json.loads(manager.active_path.read_text())["version"] == "0.9.19"


@pytest.mark.parametrize("marker_kind", ["symlink", "fifo", "oversized"])
def test_update_manager_protocol_three_marker_reader_rejects_untrusted_files(
    tmp_path: Path,
    marker_kind: str,
):
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        launcher_protocol=3,
    )
    manager.runtime_dir.mkdir(parents=True, exist_ok=True)
    expected = manager._build_restart_journal(
        reason="runtime_transition",
        operation="apply",
        phase="commit",
        job_id="strict-marker-job",
        source_active=None,
        control={name: None for name in update_center.RESTART_CONTROL_FILES},
    )
    marker_path = manager.protocol_three_handoff_path
    external = tmp_path / "external-marker"
    external.write_text("external marker must not be read or changed", encoding="utf-8")
    original = external.read_bytes()
    if marker_kind == "symlink":
        marker_path.symlink_to(external)
    elif marker_kind == "fifo":
        os.mkfifo(marker_path)
    else:
        marker_path.write_bytes(b"x" * (256 * 1024 + 1))

    with pytest.raises(UpdateLockedError, match="trusted bounded regular file"):
        manager._protocol_three_handoff_matches_locked(expected)

    assert external.read_bytes() == original


@pytest.mark.parametrize("journal_kind", ["symlink", "fifo", "oversized"])
def test_update_manager_restart_journal_reader_rejects_untrusted_files(
    tmp_path: Path,
    journal_kind: str,
):
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.18")
    manager.runtime_dir.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external-journal"
    external.write_text(
        "external journal must not be read or changed", encoding="utf-8"
    )
    original = external.read_bytes()
    if journal_kind == "symlink":
        manager.restart_required_path.symlink_to(external)
    elif journal_kind == "fifo":
        os.mkfifo(manager.restart_required_path)
    else:
        manager.restart_required_path.write_bytes(b"x" * (256 * 1024 + 1))

    with pytest.raises(UpdateLockedError, match="trusted bounded regular file"):
        manager._load_restart_journal_strict()

    assert external.read_bytes() == original


def test_strict_runtime_reader_rejects_name_replacement_before_open(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "runtime-control.json"
    replacement = tmp_path / "replacement-control.json"
    target.write_text('{"owner":"original"}', encoding="utf-8")
    replacement.write_text('{"owner":"replacement"}', encoding="utf-8")
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args):
        nonlocal swapped
        if Path(path) == target and not swapped:
            swapped = True
            target.unlink()
            replacement.replace(target)
        return real_open(path, flags, *args)

    monkeypatch.setattr(update_center.os, "open", swap_before_open)

    with pytest.raises(UpdateLockedError, match="changed before it was opened"):
        update_center._read_runtime_json_strict(target, label="test control")

    assert swapped is True


@pytest.mark.parametrize("callback_outcome", ["false", "raise"])
def test_protocol_three_apply_returns_terminal_rollback_after_lost_callback_reply(
    tmp_path: Path, monkeypatch, callback_outcome: str
):
    from core import runtime_launcher

    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    runtime_dir = tmp_path / "channelwatch-runtime"
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        launcher_protocol=3,
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    identities = {
        "core": {"pid": 101, "start": 1_700_000_001},
        "ui": {"pid": 102, "start": 1_700_000_002},
    }

    def consume_fail_and_lose_reply() -> bool:
        assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
            old_processes=identities
        )
        assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
        active = json.loads(manager.active_path.read_text())
        pending = json.loads(manager.activation_pending_path.read_text())
        rollback = json.loads(manager.rollback_path.read_text())
        failed_job = manager._prepare_job(
            {
                "job_id": pending["job_id"],
                "operation": "apply",
                "status": "failed",
                "version": active["version"],
                "bundle_sha256": pending.get("bundle_sha256"),
                "message": "Update activation failed and was rolled back.",
                "error": "synthetic activation failure",
                "rollback_applied": True,
                "rolled_back_from": active["version"],
                "rolled_back_to": "image",
            }
        )
        rollback_control = {
            "active.json": None,
            "rollback.json": rollback,
            "activation-pending.json": None,
            "activation-core-ready.json": None,
            "activation-ui-ready.json": None,
            "update-job.json": failed_job,
        }
        rollback_journal = manager._build_restart_journal(
            reason="activation_rollback",
            operation="activation_rollback",
            phase="commit",
            job_id=pending["job_id"],
            source_active=active,
            control=rollback_control,
        )
        manager._write_restart_journal(rollback_journal)
        manager.apply_restart_journal(rollback_journal)
        assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
            old_processes={
                "core": {"pid": 201, "start": 1_700_000_011},
                "ui": {"pid": 202, "start": 1_700_000_012},
            }
        )
        assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
        if callback_outcome == "raise":
            raise RuntimeError("restart acknowledgement connection closed")
        return False

    manager.restart_callable = consume_fail_and_lose_reply
    manager.check()
    job = manager.apply()

    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert job["rolled_back_from"] == "0.9.19"
    assert not manager.restart_required_path.exists()
    assert not manager.active_path.exists()
    assert not manager.activation_pending_path.exists()
    assert json.loads(manager.job_path.read_text()) == job


def test_apply_state_write_failure_does_not_leave_unvalidated_active_selection(
    tmp_path: Path, monkeypatch
):
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

    from core import update_center

    original_atomic_write_json = update_center.atomic_write_json

    def fail_active_write(path, payload, *args, **kwargs):
        if Path(path) == manager.active_path:
            raise OSError("simulated active selection write failure")
        return original_atomic_write_json(path, payload, *args, **kwargs)

    monkeypatch.setattr(update_center, "atomic_write_json", fail_active_write)

    with pytest.raises(OSError, match="selection write failure"):
        manager.apply()

    runtime_dir = tmp_path / "channelwatch-runtime"
    assert not (runtime_dir / "active.json").exists()
    assert (runtime_dir / "activation-pending.json").exists()
    journal = json.loads((runtime_dir / "restart-required.json").read_text())
    assert journal["schema"] == 2
    assert journal["operation"] == "apply"
    assert journal["phase"] == "commit"
    monkeypatch.setattr(update_center, "atomic_write_json", original_atomic_write_json)
    manager.apply_restart_journal()
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.10"
    assert (runtime_dir / "rollback.json").exists()


def test_rollback_restart_failure_restores_current_runtime_selection(tmp_path: Path):
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
    _complete_selected_activation(manager)
    manager.restart_callable = lambda: False

    job = manager.rollback()
    active = json.loads((tmp_path / "channelwatch-runtime" / "active.json").read_text())

    assert job["status"] == "failed"
    assert job["rollback_applied"] is False
    assert active["version"] == "0.9.10"
    assert not (tmp_path / "channelwatch-runtime" / "activation-pending.json").exists()


def test_rollback_production_restart_adapter_restores_current_runtime_selection(
    tmp_path: Path, monkeypatch
):
    import ui.backend.main as ui_main
    from core import runtime_launcher

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
    _complete_selected_activation(manager)
    manager.restart_callable = ui_main._schedule_container_restart_for_update
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: (_ for _ in ()).throw(RuntimeError("supervisor unavailable")),
    )

    job = manager.rollback()
    runtime_dir = tmp_path / "channelwatch-runtime"
    active = json.loads((runtime_dir / "active.json").read_text())

    assert job["status"] == "failed"
    assert job["rollback_applied"] is False
    assert "supervisor unavailable" not in str(job)
    assert active["version"] == "0.9.10"
    assert not (runtime_dir / "activation-pending.json").exists()


def test_image_runtime_update_advertises_rollback_until_it_is_applied(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        backup_callable=lambda config_dir: b"backup-bytes",
        restart_callable=lambda: True,
    )

    manager.check()
    manager.apply()
    _complete_selected_activation(manager)

    assert manager.status()["rollback_available"] is True

    manager.rollback()

    assert manager.status()["rollback_available"] is False


def test_bundle_validation_rejects_unsafe_member():
    bad_bundle = _bundle(extra={"docs/internal.md": "private process notes"})

    with pytest.raises(UpdateBundleError, match="unsupported member"):
        validate_bundle_archive(
            bad_bundle,
            expected_version="0.9.10",
            expected_runtime_abi=RUNTIME_ABI,
            expected_settings_schema_version=7,
        )


def test_bundle_validation_allows_required_top_level_legal_files():
    bundle = _bundle(
        extra={
            "LICENSE": "MIT license text",
            "NOTICE": "project notice",
            "THIRD_PARTY_LICENSES.md": "third-party inventory",
            "licenses/copyleft/CORRESPONDING_SOURCE.md": "source map",
            "licenses/copyleft/GCC-exception-3.1.txt": "GCC exception",
            "licenses/copyleft/GPL-1.0-only.txt": "GPL 1.0",
            "licenses/copyleft/GPL-2.0-only.txt": "GPL 2.0",
            "licenses/copyleft/GPL-3.0-only.txt": "GPL 3.0",
            "licenses/copyleft/LGPL-2.1-only.txt": "LGPL 2.1",
        }
    )

    metadata = validate_bundle_archive(
        bundle,
        expected_version="0.9.10",
        expected_runtime_abi=RUNTIME_ABI,
        expected_settings_schema_version=7,
    )

    assert metadata["version"] == "0.9.10"


def test_bundle_validation_rejects_unrecognized_top_level_legal_file():
    bad_bundle = _bundle(extra={"COPYING": "unexpected legal file"})

    with pytest.raises(UpdateBundleError, match="unsupported member"):
        validate_bundle_archive(
            bad_bundle,
            expected_version="0.9.10",
            expected_runtime_abi=RUNTIME_ABI,
            expected_settings_schema_version=7,
        )


def test_bundle_validation_rejects_unrecognized_nested_legal_file():
    bad_bundle = _bundle(extra={"licenses/copyleft/UNKNOWN.txt": "unexpected"})

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


def test_stale_single_flight_lock_is_discarded(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 123}')
    stale_at = time.time() - LOCK_STALE_SECONDS - 10
    os.utime(lock_path, (stale_at, stale_at))
    monkeypatch.setattr("core.update_center.is_process_running", lambda _pid: False)

    with UpdateOperationLock(lock_path):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_old_single_flight_lock_is_kept_while_same_owner_is_running(
    tmp_path: Path, monkeypatch
):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 456, "process_identity": "boot-a:12345"}')
    stale_at = time.time() - LOCK_STALE_SECONDS - 10
    os.utime(lock_path, (stale_at, stale_at))
    monkeypatch.setattr("core.update_center.is_process_running", lambda pid: pid == 456)
    monkeypatch.setattr(
        "core.update_center.get_process_identity", lambda pid: "boot-a:12345"
    )

    with pytest.raises(UpdateLockedError), UpdateOperationLock(lock_path):
        pass

    assert lock_path.exists()


@pytest.mark.parametrize("stale", [False, True])
def test_lock_is_discarded_when_pid_was_reused(tmp_path: Path, monkeypatch, stale):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 456, "process_identity": "old-boot:111"}')
    if stale:
        stale_at = time.time() - LOCK_STALE_SECONDS - 10
        os.utime(lock_path, (stale_at, stale_at))
    monkeypatch.setattr("core.update_center.is_process_running", lambda pid: pid == 456)
    monkeypatch.setattr(
        "core.update_center.get_process_identity", lambda _pid: "new-boot:222"
    )

    with UpdateOperationLock(lock_path):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_fresh_lock_is_discarded_when_owner_is_dead(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 456, "process_identity": "boot-a:12345"}')
    monkeypatch.setattr("core.update_center.is_process_running", lambda _pid: False)
    monkeypatch.setattr("core.update_center.get_process_identity", lambda _pid: None)

    with UpdateOperationLock(lock_path):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_fresh_live_lock_is_kept_when_process_identity_is_temporarily_unavailable(
    tmp_path: Path, monkeypatch
):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 456, "process_identity": "boot-a:12345"}')
    monkeypatch.setattr("core.update_center.is_process_running", lambda _pid: True)
    monkeypatch.setattr("core.update_center.get_process_identity", lambda _pid: None)

    with pytest.raises(UpdateLockedError), UpdateOperationLock(lock_path):
        pass

    assert lock_path.exists()


@pytest.mark.parametrize(
    ("pid", "error", "expected"),
    [
        (0, None, False),
        (123, ProcessLookupError(), False),
        (123, PermissionError(), True),
        (123, OSError("kernel failure"), False),
        (123, None, True),
    ],
)
def test_process_liveness_handles_platform_probe_outcomes(
    pid, error, expected, monkeypatch
):
    calls: list[tuple[int, int]] = []

    def probe(probed_pid: int, sig: int) -> None:
        calls.append((probed_pid, sig))
        if error is not None:
            raise error

    monkeypatch.setattr(update_center.os, "kill", probe)

    assert update_center.is_process_running(pid) is expected
    assert calls == ([] if pid <= 0 else [(pid, 0)])


def test_process_identity_uses_boot_namespace_and_start_time(monkeypatch):
    tokens = ["S", *("0" for _ in range(18)), "98765"]

    class ProcPath:
        def __init__(self, value):
            self.value = str(value)

        def read_text(self, *, encoding):
            assert encoding == "utf-8"
            if self.value.endswith("boot_id"):
                return "boot-generation\n"
            return f"123 (worker with spaces) {' '.join(tokens)}"

    monkeypatch.setattr(update_center, "Path", ProcPath)
    monkeypatch.setattr(
        update_center.os,
        "stat",
        lambda path: type("Stat", (), {"st_ino": 4567})(),
    )

    assert update_center.get_process_identity(123) == "boot-generation:4567:98765"
    assert update_center.get_process_identity(0) is None


def test_process_identity_returns_none_when_proc_metadata_is_incomplete(monkeypatch):
    class IncompleteProcPath:
        def __init__(self, value):
            self.value = value

        def read_text(self, *, encoding):
            return "boot" if str(self.value).endswith("boot_id") else "123 (worker) S"

    monkeypatch.setattr(update_center, "Path", IncompleteProcPath)

    assert update_center.get_process_identity(123) is None


@pytest.mark.parametrize("payload", ["[]", "{broken-json"])
def test_fresh_incomplete_lock_is_never_deleted(payload, tmp_path: Path):
    lock_path = tmp_path / "channelwatch-runtime" / "update.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(payload)

    with pytest.raises(UpdateLockedError), UpdateOperationLock(lock_path):
        pass

    assert lock_path.exists()


def test_activation_component_and_claim_generation_are_validated(tmp_path: Path):
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    with pytest.raises(ValueError, match="Unknown runtime component"):
        manager.activation_ready_path("worker")

    expected = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": "/bundle/a",
    }
    assert manager._claim_pending_activation(expected, claimant="test") is None

    manager.runtime_dir.mkdir(parents=True, exist_ok=True)
    manager.activation_pending_path.write_text(
        json.dumps({**expected, "activation_id": "generation-b"})
    )
    assert manager._claim_pending_activation(expected, claimant="test") is None
    forensic_claim = manager.runtime_dir / "activation-test-generation-a.json"
    assert json.loads(forensic_claim.read_text())["activation_id"] == "generation-b"


def test_new_manager_recovers_interrupted_claim_before_recording_readiness(
    tmp_path: Path,
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    runtime_dir.mkdir()
    active = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": str(bundle_dir),
    }
    pending = {**active, "job_id": "job-a"}
    (runtime_dir / "active.json").write_text(json.dumps(active))
    (runtime_dir / "activation-pending.json").write_text(json.dumps(pending))

    first_process = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    claim = first_process._claim_pending_activation(pending, claimant="crashed-process")
    assert claim is not None
    assert not first_process.activation_pending_path.exists()

    restarted = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    restarted.record_startup_success(
        component="core",
        running_version="0.9.16",
        activation_id="generation-a",
        healthy=True,
    )

    assert restarted.activation_pending_path.exists()
    assert restarted.activation_ready_path("core").exists()
    restarted.record_startup_success(
        component="ui",
        running_version="0.9.16",
        activation_id="generation-a",
        healthy=True,
    )
    assert json.loads(restarted.job_path.read_text())["status"] == "success"
    assert not list(runtime_dir.glob("activation-*.json"))


def test_two_update_managers_recover_claim_without_temp_file_collision(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    runtime_dir.mkdir()
    active = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": str(bundle_dir),
    }
    pending = {**active, "job_id": "job-a"}
    (runtime_dir / "active.json").write_text(json.dumps(active))
    claim_path = runtime_dir / "activation-crashed-generation-a.json"
    claim_path.write_text(json.dumps(pending))

    barrier = threading.Barrier(2)
    real_link = os.link

    def synchronized_link(source, target):
        if Path(source) == claim_path:
            barrier.wait(timeout=2)
        return real_link(source, target)

    monkeypatch.setattr(update_center.os, "link", synchronized_link)
    results: list[bool] = []
    errors: list[BaseException] = []

    def recover() -> None:
        try:
            manager = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
            results.append(manager._recover_pending_activation(active))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert results == [True, True]
    assert json.loads((runtime_dir / "activation-pending.json").read_text()) == pending


def test_late_update_manager_recoverer_cannot_recreate_completed_pending(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    runtime_dir.mkdir()
    active = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": str(bundle_dir),
    }
    pending = {**active, "job_id": "job-a"}
    claim_path = runtime_dir / "activation-crashed-generation-a.json"
    claim_path.write_text(json.dumps(pending))
    pending_path = runtime_dir / "activation-pending.json"

    late_entered = threading.Event()
    release_late = threading.Event()
    real_link = os.link

    def controlled_link(source, target):
        if threading.current_thread().name == "late-manager":
            late_entered.set()
            assert release_late.wait(timeout=2)
        return real_link(source, target)

    monkeypatch.setattr(update_center.os, "link", controlled_link)
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    late_results: list[bool] = []
    late = threading.Thread(
        target=lambda: late_results.append(manager._recover_pending_activation(active)),
        name="late-manager",
    )
    late.start()
    assert late_entered.wait(timeout=2)

    assert manager._recover_pending_activation(active) is True
    pending_path.unlink()
    release_late.set()
    late.join(timeout=5)

    assert not late.is_alive()
    assert late_results == [False]
    assert not pending_path.exists()


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
    _acknowledge_entrypoint_handoff(manager)
    active = json.loads((tmp_path / "channelwatch-runtime" / "active.json").read_text())

    manager.record_startup_success(
        component="core",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )
    intermediate = json.loads(
        (tmp_path / "channelwatch-runtime" / "update-job.json").read_text()
    )
    assert intermediate["status"] == "restarting"
    manager.record_startup_success(
        component="ui",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )

    job = json.loads(
        (tmp_path / "channelwatch-runtime" / "update-job.json").read_text()
    )
    assert job["status"] == "success"
    assert job["validated_at"]


def test_concurrent_component_readiness_completes_one_activation(tmp_path: Path):
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
    _acknowledge_entrypoint_handoff(manager)
    runtime_dir = tmp_path / "channelwatch-runtime"
    active = json.loads((runtime_dir / "active.json").read_text())
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def record(component: str) -> None:
        try:
            barrier.wait(timeout=2)
            UpdateManager(
                config_dir=tmp_path,
                current_version="0.9.9",
                public_keys=public,
            ).record_startup_success(
                component=component,
                running_version="0.9.10",
                activation_id=active["activation_id"],
                healthy=True,
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=record, args=(component,))
        for component in ("core", "ui")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert not any(thread.is_alive() for thread in threads)
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["status"] == "success"
    )
    assert not (runtime_dir / "activation-pending.json").exists()


def test_deadline_claim_winner_cannot_be_overwritten_by_startup_success(
    tmp_path: Path, monkeypatch
):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
        healthcheck_callable=lambda: True,
    )
    manager.check()
    manager.apply()
    _acknowledge_entrypoint_handoff(manager)
    runtime_dir = tmp_path / "channelwatch-runtime"
    active = json.loads((runtime_dir / "active.json").read_text())

    manager.record_startup_success(
        component="core",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )

    original_claim = manager._claim_pending_activation

    def deadline_wins_before_completion(phase: str) -> None:
        if phase == "activation:before-success-lock":
            pending = update_center.load_json(manager.activation_pending_path, None)
            assert isinstance(pending, dict)
            deadline_claim = original_claim(pending, claimant="failed-watchdog")
            assert deadline_claim is not None
            manager.record_activation_failure_and_rollback(
                "deadline claimant won",
                job_id=str(pending.get("job_id") or "") or None,
            )

    monkeypatch.setattr(
        manager, "_restart_transition_checkpoint", deadline_wins_before_completion
    )
    manager.record_startup_success(
        component="ui",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )

    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert not (runtime_dir / "active.json").exists()
    assert not (runtime_dir / "activation-pending.json").exists()


def test_startup_success_write_failure_restores_canonical_pending(
    tmp_path: Path, monkeypatch
):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
        healthcheck_callable=lambda: True,
    )
    manager.check()
    manager.apply()
    _acknowledge_entrypoint_handoff(manager)
    runtime_dir = tmp_path / "channelwatch-runtime"
    active = json.loads((runtime_dir / "active.json").read_text())
    manager.record_startup_success(
        component="core",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )

    original_write_job = manager._write_job

    def fail_success_job(payload):
        if payload.get("status") == "success":
            raise OSError("job storage unavailable")
        return original_write_job(payload)

    monkeypatch.setattr(manager, "_write_job", fail_success_job)
    with pytest.raises(OSError, match="job storage unavailable"):
        manager.record_startup_success(
            component="ui",
            running_version="0.9.10",
            activation_id=active["activation_id"],
            healthy=True,
        )

    pending = json.loads((runtime_dir / "activation-pending.json").read_text())
    assert pending["activation_id"] == active["activation_id"]
    assert not list(runtime_dir.glob("activation-completed-*.json"))
    assert json.loads((runtime_dir / "update-job.json").read_text())["status"] == (
        "restarting"
    )


def test_apply_cannot_interleave_with_startup_success_completion(
    tmp_path: Path, monkeypatch
):
    private, public = _key_pair()
    first_bundle = _bundle("0.9.10")
    first_manifest = _manifest(private, first_bundle, "0.9.10")
    first = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: (
            first_bundle if url.endswith(".zip") else first_manifest
        ),
        restart_callable=lambda: True,
        healthcheck_callable=lambda: True,
    )
    first.check()
    first.apply()
    _acknowledge_entrypoint_handoff(first)
    runtime_dir = tmp_path / "channelwatch-runtime"
    first_active = json.loads((runtime_dir / "active.json").read_text())
    first.record_startup_success(
        component="core",
        running_version="0.9.10",
        activation_id=first_active["activation_id"],
        healthy=True,
    )

    second_bundle = _bundle("0.9.11")
    second_manifest = _manifest(private, second_bundle, "0.9.11")
    second = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.10",
        public_keys=public,
        fetcher=lambda url, max_bytes: (
            second_bundle if url.endswith(".zip") else second_manifest
        ),
        restart_callable=lambda: True,
    )
    second.check()

    original_write_job = first._write_job
    interleaved_apply_rejected = False

    def attempt_second_apply_while_completion_owns_state(payload):
        nonlocal interleaved_apply_rejected
        if payload.get("status") == "success":
            with pytest.raises(UpdateLockedError):
                second.apply()
            interleaved_apply_rejected = True
        return original_write_job(payload)

    monkeypatch.setattr(
        first,
        "_write_job",
        attempt_second_apply_while_completion_owns_state,
    )
    first.record_startup_success(
        component="ui",
        running_version="0.9.10",
        activation_id=first_active["activation_id"],
        healthy=True,
    )

    final_active = json.loads((runtime_dir / "active.json").read_text())
    final_job = json.loads((runtime_dir / "update-job.json").read_text())
    assert interleaved_apply_rejected is True
    assert final_active["activation_id"] == first_active["activation_id"]
    assert final_active["version"] == "0.9.10"
    assert final_job["status"] == "success"
    assert final_job["version"] == "0.9.10"
    assert not (runtime_dir / "activation-pending.json").exists()


def test_apply_recovers_and_blocks_a_durable_activation_claim(tmp_path: Path):
    private, public = _key_pair()
    first_bundle = _bundle("0.9.10")
    first_manifest = _manifest(private, first_bundle, "0.9.10")
    first = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: (
            first_bundle if url.endswith(".zip") else first_manifest
        ),
        restart_callable=lambda: True,
    )
    first.check()
    first.apply()
    _acknowledge_entrypoint_handoff(first)
    runtime_dir = tmp_path / "channelwatch-runtime"
    pending = json.loads((runtime_dir / "activation-pending.json").read_text())
    claim = first._claim_pending_activation(
        pending,
        claimant="completed-watchdog",
    )
    assert claim is not None
    assert not (runtime_dir / "activation-pending.json").exists()

    second_bundle = _bundle("0.9.11")
    second_manifest = _manifest(private, second_bundle, "0.9.11")
    second = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.10",
        public_keys=public,
        fetcher=lambda url, max_bytes: (
            second_bundle if url.endswith(".zip") else second_manifest
        ),
        restart_callable=lambda: True,
    )
    second.check()

    with pytest.raises(
        UpdateLockedError,
        match="previous update is still waiting for startup validation",
    ):
        second.apply()

    recovered = json.loads((runtime_dir / "activation-pending.json").read_text())
    active = json.loads((runtime_dir / "active.json").read_text())
    assert recovered["activation_id"] == pending["activation_id"]
    assert active["activation_id"] == pending["activation_id"]
    assert not claim.exists()


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
    _acknowledge_entrypoint_handoff(manager)
    active = json.loads((tmp_path / "channelwatch-runtime" / "active.json").read_text())

    manager.record_startup_success(
        component="core",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )
    manager.record_startup_success(
        component="ui",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )

    runtime_dir = tmp_path / "channelwatch-runtime"
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert job["rolled_back_to"] == "image"
    assert restarts == [True, True]
    assert not (runtime_dir / "active.json").exists()


def test_startup_success_rejects_stale_activation_generation(tmp_path: Path):
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
    )
    manager.check()
    manager.apply()
    _acknowledge_entrypoint_handoff(manager)

    manager.record_startup_success(
        component="core",
        running_version="0.9.10",
        activation_id="stale-generation",
        healthy=True,
    )

    runtime_dir = tmp_path / "channelwatch-runtime"
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert restarts == [True, True]
    assert not (runtime_dir / "active.json").exists()


def test_unhealthy_component_cannot_complete_activation(tmp_path: Path):
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
    )
    manager.check()
    manager.apply()
    _acknowledge_entrypoint_handoff(manager)
    active = json.loads((tmp_path / "channelwatch-runtime" / "active.json").read_text())

    manager.record_startup_success(
        component="ui",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=False,
    )

    job = json.loads(
        (tmp_path / "channelwatch-runtime" / "update-job.json").read_text()
    )
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert restarts == [True, True]


def test_activation_rollback_records_and_raises_when_restart_is_rejected(
    tmp_path: Path,
):
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
    _acknowledge_entrypoint_handoff(manager)
    active = json.loads((tmp_path / "channelwatch-runtime" / "active.json").read_text())
    manager.restart_callable = lambda: False

    with pytest.raises(UpdateRestartError, match="rollback completed"):
        manager.record_startup_success(
            component="ui",
            running_version="0.9.10",
            activation_id=active["activation_id"],
            healthy=False,
        )

    runtime_dir = tmp_path / "channelwatch-runtime"
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert job["restart_required"] is True
    assert job["restart_started"] is False
    assert job["restart_error"] == (
        "The coordinated restart callback did not accept the request."
    )
    assert not (runtime_dir / "active.json").exists()
    restart_required = json.loads((runtime_dir / "restart-required.json").read_text())
    assert restart_required["schema"] == 2
    assert restart_required["reason"] == "activation_rollback"
    assert restart_required["operation"] == "activation_rollback"
    assert restart_required["phase"] == "commit"
    assert restart_required["job_id"] == job["job_id"]
    assert restart_required["source_active"]["version"] == "0.9.10"
    assert restart_required["control"]["active.json"] is None
    assert restart_required["control"]["update-job.json"] == job


def test_activation_rollback_records_and_raises_when_restart_callback_raises(
    tmp_path: Path,
):
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
    _acknowledge_entrypoint_handoff(manager)
    active = json.loads((tmp_path / "channelwatch-runtime" / "active.json").read_text())
    restart_failure = "supervisor unavailable: " + ("x" * 2500)
    manager.restart_callable = lambda: (_ for _ in ()).throw(
        RuntimeError(restart_failure)
    )

    with pytest.raises(UpdateRestartError, match="rollback completed"):
        manager.record_startup_success(
            component="core",
            running_version="0.9.10",
            activation_id=active["activation_id"],
            healthy=False,
        )

    runtime_dir = tmp_path / "channelwatch-runtime"
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert job["restart_required"] is True
    assert job["restart_started"] is False
    assert job["restart_error"] == restart_failure[:2000]
    assert len(job["restart_error"]) == 2000
    assert not (runtime_dir / "active.json").exists()
    assert (runtime_dir / "restart-required.json").is_file()


def test_activation_rollback_is_not_committed_if_restart_sentinel_write_fails(
    tmp_path: Path, monkeypatch
):
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
    runtime_dir = tmp_path / "channelwatch-runtime"
    active = json.loads((runtime_dir / "active.json").read_text())
    # Simulate the image-stable entrypoint consuming the apply handoff before
    # the selected bundle reports startup health.
    _acknowledge_entrypoint_handoff(manager)
    original_link = update_center.os.link

    def fail_sentinel(source, destination):
        if Path(destination) == manager.restart_required_path:
            raise OSError("sentinel storage unavailable")
        return original_link(source, destination)

    monkeypatch.setattr(update_center.os, "link", fail_sentinel)

    with pytest.raises(OSError, match="sentinel storage unavailable"):
        manager.record_startup_success(
            component="ui",
            running_version="0.9.10",
            activation_id=active["activation_id"],
            healthy=False,
        )

    restored_active = json.loads((runtime_dir / "active.json").read_text())
    assert restored_active["activation_id"] == active["activation_id"]
    assert (runtime_dir / "activation-pending.json").is_file()
    assert not manager.restart_required_path.exists()


def test_core_ready_propagates_terminal_update_restart_failure(
    tmp_path: Path, monkeypatch
):
    import core.main as main_mod

    calls = []

    class FailingManager:
        def record_startup_success(self, **kwargs):
            calls.append(kwargs)
            raise UpdateRestartError("restart handoff failed")

    monkeypatch.setattr(
        update_center, "UpdateManager", lambda **_kwargs: FailingManager()
    )

    with pytest.raises(UpdateRestartError, match="restart handoff failed"):
        main_mod._record_update_core_ready(str(tmp_path))

    assert calls == [
        {
            "component": "core",
            "running_version": main_mod.__version__,
            "activation_id": os.environ.get("CHANNELWATCH_ACTIVATION_ID", ""),
            "healthy": True,
        }
    ]


def test_update_check_during_restart_preserves_pending_activation(tmp_path: Path):
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

    runtime_dir = tmp_path / "channelwatch-runtime"
    active = json.loads((runtime_dir / "active.json").read_text())
    pending_path = runtime_dir / "activation-pending.json"
    pending_before = pending_path.read_bytes()
    job_path = runtime_dir / "update-job.json"
    job_before = job_path.read_bytes()

    with pytest.raises(UpdateLockedError, match="next container entrypoint"):
        manager.check()
    assert pending_path.read_bytes() == pending_before
    assert job_path.read_bytes() == job_before

    # A new container entrypoint replays and acknowledges the handoff before
    # either newly pinned child can publish activation readiness.
    _acknowledge_entrypoint_handoff(manager)

    for component in ("core", "ui"):
        manager.record_startup_success(
            component=component,
            running_version="0.9.10",
            activation_id=active["activation_id"],
            healthy=True,
        )

    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert job["operation"] == "apply"
    assert job["status"] == "success"
    assert not pending_path.exists()


def test_second_apply_is_blocked_while_activation_is_pending(tmp_path: Path):
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
    _acknowledge_entrypoint_handoff(manager)

    with pytest.raises(UpdateLockedError, match="startup validation"):
        manager.apply()


def test_unacknowledged_restart_journal_blocks_all_transition_writers(
    tmp_path: Path,
):
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(parents=True)
    manager.restart_required_path.mkdir()

    with pytest.raises(UpdateLockedError, match="next container entrypoint"):
        manager.apply()
    with pytest.raises(UpdateLockedError, match="next container entrypoint"):
        manager.rollback()
    with pytest.raises(UpdateLockedError, match="next container entrypoint"):
        manager.record_activation_failure_and_rollback("must not replace journal")

    assert manager.restart_required_path.is_dir()


def test_unacknowledged_restart_journal_blocks_old_process_readiness(
    tmp_path: Path,
):
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(parents=True)
    active = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": str(runtime_dir / "releases" / "v0.9.16"),
    }
    pending = {**active, "job_id": "job-a"}
    update_center.atomic_write_json(manager.active_path, active)
    update_center.atomic_write_json(manager.activation_pending_path, pending)
    manager.restart_required_path.mkdir()

    manager.record_startup_success(
        component="core",
        running_version="0.9.16",
        activation_id="generation-a",
        healthy=True,
    )

    assert update_center.load_json(manager.activation_pending_path, None) == pending
    assert not manager.activation_ready_path("core").exists()
    assert manager.restart_required_path.is_dir()


@pytest.mark.parametrize("writer", ["apply", "manual_rollback", "activation_rollback"])
def test_initial_journal_publication_never_clobbers_an_interleaved_foreign_owner(
    tmp_path: Path, monkeypatch, writer: str
):
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
    if writer != "apply":
        manager.apply()
        if writer == "manual_rollback":
            _complete_selected_activation(manager)
        else:
            _acknowledge_entrypoint_handoff(manager)

    foreign = _foreign_restart_journal(manager)

    def publish_foreign(phase: str) -> None:
        if phase == "journal:before-create":
            update_center.atomic_write_json(manager.restart_required_path, foreign)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", publish_foreign)
    action = {
        "apply": manager.apply,
        "manual_rollback": manager.rollback,
        "activation_rollback": lambda: manager.record_activation_failure_and_rollback(
            "simulated failure"
        ),
    }[writer]

    with pytest.raises(UpdateLockedError, match="won publication"):
        action()

    assert update_center.load_json(manager.restart_required_path, None) == foreign


@pytest.mark.parametrize("writer", ["apply", "manual_rollback"])
def test_abort_replacement_never_clobbers_an_interleaved_foreign_owner(
    tmp_path: Path, monkeypatch, writer: str
):
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
    if writer == "manual_rollback":
        manager.apply()
        _complete_selected_activation(manager)
    manager.restart_callable = lambda: False
    foreign_holder: list[dict] = []

    def replace_with_foreign(phase: str) -> None:
        if phase == "journal:before-replace":
            foreign = _foreign_restart_journal(manager, job_id="foreign-replace")
            update_center.atomic_write_json(manager.restart_required_path, foreign)
            foreign_holder.append(foreign)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", replace_with_foreign)
    action = manager.apply if writer == "apply" else manager.rollback

    with pytest.raises(UpdateLockedError, match="another generation"):
        action()

    assert foreign_holder
    assert (
        update_center.load_json(manager.restart_required_path, None)
        == foreign_holder[0]
    )


def test_expected_clear_never_deletes_an_interleaved_foreign_journal(
    tmp_path: Path, monkeypatch
):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: False,
    )
    manager.check()
    foreign_holder: list[dict] = []

    def replace_before_clear(phase: str) -> None:
        if phase == "journal:before-clear":
            foreign = _foreign_restart_journal(manager, job_id="foreign-clear")
            update_center.atomic_write_json(manager.restart_required_path, foreign)
            foreign_holder.append(foreign)

    monkeypatch.setattr(manager, "_restart_transition_checkpoint", replace_before_clear)

    with pytest.raises(UpdateLockedError, match="another generation"):
        manager.apply()

    assert foreign_holder
    assert (
        update_center.load_json(manager.restart_required_path, None)
        == foreign_holder[0]
    )


def test_stale_local_journal_cannot_replay_under_a_foreign_owner(
    tmp_path: Path,
):
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(parents=True)
    seed_active = {"version": "seed", "path": "/seed"}
    update_center.atomic_write_json(manager.active_path, seed_active)
    stale_control = manager._read_control_state()
    stale_control["active.json"] = {"version": "stale", "path": "/stale"}
    stale = _foreign_restart_journal(manager, job_id="stale", control=stale_control)
    manager._write_restart_journal(stale)
    foreign = _foreign_restart_journal(manager, job_id="foreign")
    update_center.atomic_write_json(manager.restart_required_path, foreign)

    with pytest.raises(UpdateLockedError, match="another generation"):
        manager.apply_restart_journal(stale)

    assert update_center.load_json(manager.active_path, None) == seed_active
    assert update_center.load_json(manager.restart_required_path, None) == foreign


def test_healthcheck_foreign_journal_prevents_stale_success_completion(
    tmp_path: Path,
):
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
    _acknowledge_entrypoint_handoff(manager)
    active = update_center.load_json(manager.active_path, None)
    assert isinstance(active, dict)
    manager.record_startup_success(
        component="core",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )
    foreign_holder: list[dict] = []

    def publish_foreign_health_outcome() -> bool:
        control = manager._read_control_state()
        control.update(
            {
                "active.json": None,
                "activation-pending.json": None,
                "activation-core-ready.json": None,
                "activation-ui-ready.json": None,
            }
        )
        foreign = _foreign_restart_journal(
            manager, job_id="foreign-health", control=control
        )
        manager._write_restart_journal(foreign)
        manager.apply_restart_journal(foreign)
        foreign_holder.append(foreign)
        return True

    manager.healthcheck_callable = publish_foreign_health_outcome
    manager.record_startup_success(
        component="ui",
        running_version="0.9.10",
        activation_id=active["activation_id"],
        healthy=True,
    )

    assert foreign_holder
    foreign = foreign_holder[0]
    assert update_center.load_json(manager.restart_required_path, None) == foreign
    assert update_center.load_json(manager.active_path, None) is None
    assert update_center.load_json(manager.activation_pending_path, None) is None
    assert (
        update_center.load_json(manager.job_path, None)
        == foreign["control"]["update-job.json"]
    )


def test_activation_restart_failure_never_rewrites_a_foreign_callback_journal(
    tmp_path: Path,
):
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
    _acknowledge_entrypoint_handoff(manager)
    active = update_center.load_json(manager.active_path, None)
    assert isinstance(active, dict)
    foreign_holder: list[dict] = []

    def replace_during_restart_callback() -> bool:
        foreign = _foreign_restart_journal(manager, job_id="foreign-callback")
        update_center.atomic_write_json(manager.restart_required_path, foreign)
        foreign_holder.append(foreign)
        return False

    manager.restart_callable = replace_during_restart_callback
    with pytest.raises(UpdateRestartError, match="another generation"):
        manager.record_startup_success(
            component="ui",
            running_version="0.9.10",
            activation_id=active["activation_id"],
            healthy=False,
        )

    assert foreign_holder
    assert (
        update_center.load_json(manager.restart_required_path, None)
        == foreign_holder[0]
    )


def test_manual_rollback_rejects_canonical_pending_activation(tmp_path: Path):
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
    _acknowledge_entrypoint_handoff(manager)

    with pytest.raises(UpdateLockedError, match="pending startup validation"):
        manager.rollback()


def test_check_cannot_write_controls_after_fetch_publishes_a_journal(
    tmp_path: Path,
):
    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    foreign_holder: list[dict] = []
    manager: UpdateManager

    def publish_during_fetch(_url: str, _max_bytes: int) -> bytes:
        foreign = _foreign_restart_journal(manager, job_id="foreign-check")
        manager._write_restart_journal(foreign)
        foreign_holder.append(foreign)
        return manifest

    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.10",
        public_keys=public,
        fetcher=publish_during_fetch,
    )
    manager._write_job(
        {
            "job_id": "seed-job",
            "operation": "apply",
            "status": "restarting",
            "version": "0.9.10",
        }
    )
    job_before = manager.job_path.read_bytes()

    with pytest.raises(UpdateLockedError, match="next container entrypoint"):
        manager.check()

    assert foreign_holder
    assert (
        update_center.load_json(manager.restart_required_path, None)
        == foreign_holder[0]
    )
    assert manager.job_path.read_bytes() == job_before
    assert not manager.latest_path.exists()


def test_next_journal_write_removes_only_regular_abandoned_candidates(
    tmp_path: Path,
):
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.15")
    manager.runtime_dir.mkdir(parents=True)
    abandoned = manager.runtime_dir / ".restart-required.json.candidate-abandoned"
    unsafe = manager.runtime_dir / ".restart-required.json.candidate-unsafe"
    abandoned.write_text("orphaned valid-writer inode", encoding="utf-8")
    unsafe.mkdir()

    journal = _foreign_restart_journal(manager, job_id="candidate-cleanup")
    manager._write_restart_journal(journal)

    assert not abandoned.exists()
    assert unsafe.is_dir()
    assert update_center.load_json(manager.restart_required_path, None) == journal
