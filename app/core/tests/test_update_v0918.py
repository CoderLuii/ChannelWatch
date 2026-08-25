import base64
import importlib.util
import io
import json
import sys
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.tests.test_update_manager import _bundle, _key_pair, _manifest
from core.update_catalog import (
    DeliveryMode,
    LauncherProtocol,
    normalize_catalog,
    select_catalog_release,
)
from core.update_center import (
    RUNTIME_ABI,
    UpdateLockedError,
    UpdateManager,
    UpdateManifestError,
    UpdateRestartError,
    canonical_payload_bytes,
    guard_legacy_launcher_before_start,
    normalize_manifest,
    read_update_document_bytes,
    validate_trusted_url,
    verify_ed25519_signature,
)
from core.update_policy import (
    OfficialRecoveryUpdateService,
    UpdateAutomationService,
    UpdatePolicy,
    UpdatePolicyStorageError,
    UpdatePolicyStore,
    format_timestamp,
    maintenance_opportunity,
    parse_timestamp,
    record_failed_activation_quarantine,
)


def _legacy_bridge_bundle(version: str = "0.9.18") -> bytes:
    """Bundle enough of the current runtime to execute its extracted guard.

    The ordinary update-manager fixture intentionally contains inert entrypoint
    stubs. The legacy bridge test must instead prove that the code an old image
    actually extracts can run the v0.9.18 launcher guard without importing the
    working tree.
    """

    app_dir = Path(__file__).resolve().parents[2]
    members = (
        "core/__init__.py",
        "core/main.py",
        "core/update_catalog.py",
        "core/update_center.py",
        "core/helpers/atomic_io.py",
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            archive.writestr(member, (app_dir / member).read_bytes())
        archive.writestr("core/helpers/__init__.py", "# bridge helpers package\n")
        archive.writestr("ui/backend/main.py", "# bridge UI entrypoint\n")
        archive.writestr(
            "channelwatch-bundle.json",
            json.dumps(
                {
                    "version": version,
                    "runtime_abi": RUNTIME_ABI,
                    "settings_schema_version": 7,
                }
            ),
        )
    return buffer.getvalue()


def _catalog(
    private: Ed25519PrivateKey,
    bundle: bytes,
    *,
    version: str = "0.9.19",
    sources: list[str] | None = None,
    launchers: list[int] | None = None,
    automatic_install_allowed: object = True,
) -> bytes:
    import hashlib

    digest = hashlib.sha256(bundle).digest()
    release = {
        "version": version,
        "version_tag": f"v{version}",
        "delivery_mode": "app_update_with_image_refresh",
        "runtime_abi": RUNTIME_ABI,
        "settings_schema_version": 7,
        "bundle_url": (
            "https://github.com/CoderLuii/ChannelWatch/releases/download/"
            f"v{version}/channelwatch-app-v{version}.zip"
        ),
        "release_url": (
            f"https://github.com/CoderLuii/ChannelWatch/releases/tag/v{version}"
        ),
        "bundle_sha256": digest.hex(),
        "bundle_signature": base64.b64encode(private.sign(digest)).decode("ascii"),
        "key_id": "test-key",
        "updater_protocol": 2,
        "automatic_install_allowed": automatic_install_allowed,
        "automatic_install_after": "2026-08-25T00:00:00Z",
        "recovery_compatible": True,
        "compatible_source_application_versions": sources or ["0.9.18"],
        "compatible_runtime_abis": [RUNTIME_ABI],
        "compatible_settings_schema_versions": [7],
        "compatible_launcher_protocols": launchers or [1, 2, 3],
        "recommended_image_version": version,
        "revocation_state": "active",
        "publication_time": "2026-08-24T00:00:00Z",
        "highlights": ["Test catalog update"],
    }
    payload = {
        "channel": "stable",
        "published_at": "2026-08-24T00:00:00Z",
        "releases": [release],
    }
    return json.dumps(
        {
            "schema": 2,
            "payload": payload,
            "signature": {
                "alg": "ed25519",
                "key_id": "test-key",
                "value": base64.b64encode(
                    private.sign(canonical_payload_bytes(payload))
                ).decode("ascii"),
            },
        }
    ).encode()


def _normalized_catalog(data: bytes, public: dict[str, str]) -> dict:
    return normalize_catalog(
        json.loads(data),
        public_keys=public,
        verify_signature=verify_ed25519_signature,
        canonical_payload=canonical_payload_bytes,
        validate_url=validate_trusted_url,
    )


def test_schema_two_catalog_preserves_canonical_contract_and_selects_protocol_one():
    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    catalog = _normalized_catalog(_catalog(private, bundle), public)

    release = catalog["payload"]["releases"][0]
    assert release["automatic_install_allowed"] is True
    assert release["compatible_source_application_versions"] == ["0.9.18"]
    assert release["compatible_launcher_protocols"] == [1, 2, 3]
    assert release["recommended_image_version"] == "0.9.19"
    assert release["revocation_state"] == "active"
    selection = select_catalog_release(
        catalog,
        current_version="0.9.18",
        runtime_abi=RUNTIME_ABI,
        settings_schema_version=7,
        launcher_protocol=LauncherProtocol.LEGACY_ADOPT,
    )
    assert selection.release == release


def test_schema_two_catalog_rejects_truthy_string_boolean():
    private, public = _key_pair()
    with pytest.raises(ValueError, match="automatic_install_allowed must be a boolean"):
        _normalized_catalog(
            _catalog(
                private,
                _bundle("0.9.19"),
                automatic_install_allowed="false",
            ),
            public,
        )


def test_schema_two_catalog_skips_unsupported_updater_protocol():
    private, public = _key_pair()
    raw = json.loads(_catalog(private, _bundle("0.9.19")))
    raw["payload"]["releases"][0]["updater_protocol"] = 999
    raw["signature"]["value"] = base64.b64encode(
        private.sign(canonical_payload_bytes(raw["payload"]))
    ).decode("ascii")
    catalog = _normalized_catalog(json.dumps(raw).encode(), public)

    selection = select_catalog_release(
        catalog,
        current_version="0.9.18",
        runtime_abi=RUNTIME_ABI,
        settings_schema_version=7,
        launcher_protocol=LauncherProtocol.RECOVERY_CAPABLE,
    )
    assert selection.release is None
    assert selection.reason == "no-compatible-release"


def test_recovery_catalog_skips_newer_release_not_marked_recovery_compatible():
    private, public = _key_pair()
    recovery_catalog = json.loads(
        _catalog(private, _bundle("0.9.19"), version="0.9.19")
    )
    ordinary_catalog = json.loads(
        _catalog(private, _bundle("0.9.20"), version="0.9.20")
    )
    recovery_release = recovery_catalog["payload"]["releases"][0]
    ordinary_release = ordinary_catalog["payload"]["releases"][0]
    ordinary_release["recovery_compatible"] = False
    payload = {
        "channel": "stable",
        "published_at": "2026-08-24T00:00:00Z",
        "releases": [ordinary_release, recovery_release],
    }
    raw = {
        "schema": 2,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": "test-key",
            "value": base64.b64encode(
                private.sign(canonical_payload_bytes(payload))
            ).decode("ascii"),
        },
    }
    catalog = _normalized_catalog(json.dumps(raw).encode(), public)

    ordinary = select_catalog_release(
        catalog,
        current_version="0.9.18",
        runtime_abi=RUNTIME_ABI,
        settings_schema_version=7,
        launcher_protocol=LauncherProtocol.RECOVERY_CAPABLE,
    )
    recovery = select_catalog_release(
        catalog,
        current_version="0.9.18",
        runtime_abi=RUNTIME_ABI,
        settings_schema_version=7,
        launcher_protocol=LauncherProtocol.RECOVERY_CAPABLE,
        recovery=True,
    )
    assert ordinary.release["version"] == "0.9.20"
    assert recovery.release["version"] == "0.9.19"


def test_catalog_selects_retained_intermediate_when_latest_is_incompatible():
    private, public = _key_pair()
    intermediate_document = json.loads(
        _catalog(private, _bundle("0.9.19"), version="0.9.19")
    )
    latest_document = json.loads(
        _catalog(private, _bundle("0.9.20"), version="0.9.20")
    )
    intermediate = intermediate_document["payload"]["releases"][0]
    latest = latest_document["payload"]["releases"][0]
    intermediate["compatible_source_application_versions"] = ["0.9.18"]
    latest["compatible_source_application_versions"] = ["0.9.19"]
    payload = {
        "channel": "stable",
        "published_at": "2026-08-24T00:00:00Z",
        "releases": [latest, intermediate],
    }
    raw = {
        "schema": 2,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": "test-key",
            "value": base64.b64encode(
                private.sign(canonical_payload_bytes(payload))
            ).decode("ascii"),
        },
    }
    catalog = _normalized_catalog(json.dumps(raw).encode(), public)

    selected = select_catalog_release(
        catalog,
        current_version="0.9.18",
        runtime_abi=RUNTIME_ABI,
        settings_schema_version=7,
        launcher_protocol=LauncherProtocol.RECOVERY_CAPABLE,
    )

    assert selected.release["version"] == "0.9.19"
    assert selected.considered_versions == ("0.9.20", "0.9.19")


def test_schema_two_catalog_rejects_automatic_install_before_24_hour_delay():
    private, public = _key_pair()
    raw = json.loads(_catalog(private, _bundle("0.9.19")))
    raw["payload"]["releases"][0]["automatic_install_after"] = "2026-08-24T23:59:59Z"
    raw["signature"]["value"] = base64.b64encode(
        private.sign(canonical_payload_bytes(raw["payload"]))
    ).decode("ascii")

    with pytest.raises(ValueError, match="at least 24 hours"):
        _normalized_catalog(json.dumps(raw).encode(), public)


def test_release_verifier_validates_schema_two_catalog_and_bundle():
    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    catalog_bytes = _catalog(private, bundle)
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "release" / "verify-update-assets.py"
    spec = importlib.util.spec_from_file_location("verify_v2_assets", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    verified = module.verify_update_catalog(
        catalog_bytes,
        bundle,
        public_keys=public,
        expected_version="0.9.19",
        expected_delivery_mode="app_update_with_image_refresh",
        expected_runtime_abi=RUNTIME_ABI,
        expected_settings_schema_version=7,
        expected_recommended_image_version="0.9.19",
    )
    assert verified["schema"] == 2


def test_update_manager_checks_and_applies_schema_two_catalog(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    catalog_bytes = _catalog(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.18",
        launcher_protocol=3,
        public_keys=public,
        fetcher=lambda url, _limit: bundle if url.endswith(".zip") else catalog_bytes,
        restart_callable=lambda: True,
    )

    checked = manager.check()
    assert checked["update_available"] is True
    assert checked["delivery_mode"] == "app_update_with_image_refresh"
    assert manager.apply("0.9.19")["status"] == "restarting"


@pytest.mark.parametrize(
    ("tag", "journal_replayed"),
    [
        ("v0.9.11", False),
        ("v0.9.12", False),
        ("v0.9.13", False),
        ("v0.9.14", False),
        ("v0.9.15", False),
        ("v0.9.16", True),
        ("v0.9.17", True),
    ],
)
def test_legacy_tag_source_trust_paths_accept_v0918_bridge_layout(
    tmp_path: Path, tag: str, journal_replayed: bool
):
    private, public = _key_pair()
    bundle = _legacy_bridge_bundle("0.9.18")
    raw = json.loads(_manifest(private, bundle, "0.9.18"))
    raw["payload"].update(
        {
            "delivery_mode": "app_update_with_image_refresh",
            "minimum_image_version": "0.9.11",
            "updater_protocol": 2,
            "recommended_image_version": "0.9.18",
        }
    )
    raw["signature"]["value"] = base64.b64encode(
        private.sign(canonical_payload_bytes(raw["payload"]))
    ).decode("ascii")
    manifest_path = tmp_path / "manifest.json"
    bundle_path = tmp_path / "bundle.zip"
    manifest_path.write_text(json.dumps(raw))
    bundle_path.write_bytes(bundle)

    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "release" / "verify-legacy-update-bridge.py"
    spec = importlib.util.spec_from_file_location("verify_legacy_bridge", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify_tag(
        tag,
        manifest_path=manifest_path,
        bundle_path=bundle_path,
        expected_version="0.9.18",
        public_keys=public,
    )
    assert result["source_acceptance"] == "verified"
    assert result["check_status"] == "available"
    assert result["apply_status"] == "restarting"
    assert result["applied_active_version"] == "0.9.18"
    assert result["active_version"] == "0.9.18"
    assert "guard_applied" not in result
    assert result["journal_replayed"] is journal_replayed


def test_published_v099_and_v010_are_never_approved_as_bundle_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "release" / "verify-legacy-update-bridge.py"
    spec = importlib.util.spec_from_file_location("verify_v099_exception", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "v0.9.9" not in module.DEFAULT_TAGS
    assert "v0.9.10" not in module.DEFAULT_TAGS
    assert module.DEFAULT_TAGS == tuple(f"v0.9.{patch}" for patch in range(11, 18))
    for tag in ("v0.9.9", "v0.9.10"):
        exception = module.image_pull_only_exception(tag)
        assert exception == {
            "tag": tag,
            "support": "image_pull_only",
            "required_image_version": "0.9.18",
            "preserve_config": True,
            "in_app_update_supported": False,
            "published_image_guard_reachable": False,
            "reason": "published_image_cannot_activate_bridge_bundle",
        }
        with pytest.raises(RuntimeError, match="image-pull-only"):
            module.verify_tag(
                tag,
                manifest_path=tmp_path / "unused-manifest.json",
                bundle_path=tmp_path / "unused-bundle.zip",
                expected_version="0.9.18",
            )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify-legacy-update-bridge.py",
            "--manifest",
            str(tmp_path / "missing-manifest.json"),
            "--bundle",
            str(tmp_path / "missing-bundle.zip"),
            "--tag",
            "v0.9.9",
        ],
    )
    with pytest.raises(RuntimeError, match="image-pull-only"):
        module.main()


def test_schema_one_bridge_rejects_truthy_string_boolean():
    private, public = _key_pair()
    raw = json.loads(_manifest(private, _bundle()))
    raw["payload"]["image_required"] = "false"
    raw["signature"]["value"] = base64.b64encode(
        private.sign(canonical_payload_bytes(raw["payload"]))
    ).decode("ascii")
    with pytest.raises(UpdateManifestError, match="explicit boolean"):
        normalize_manifest(raw, public)


def test_recovery_mode_rejects_schema_one_not_explicitly_recovery_compatible():
    private, public = _key_pair()
    raw = json.loads(_manifest(private, _bundle(), "0.9.18"))
    raw["payload"]["recovery_compatible"] = False
    raw["signature"]["value"] = base64.b64encode(
        private.sign(canonical_payload_bytes(raw["payload"]))
    ).decode("ascii")

    with pytest.raises(UpdateManifestError, match="not approved for recovery"):
        read_update_document_bytes(
            json.dumps(raw).encode(),
            public_keys=public,
            current_version="0.9.17",
            runtime_abi=RUNTIME_ABI,
            settings_schema_version=7,
            launcher_protocol=2,
            recovery=True,
        )


def test_policy_default_file_is_exact_and_first_check_is_five_minutes(tmp_path: Path):
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    store = UpdatePolicyStore(tmp_path, clock=lambda: now)

    assert store.get() == UpdatePolicy()
    assert json.loads(store.policy_path.read_text()) == {
        "schema": 1,
        "mode": "automatic",
        "channel": "stable",
        "maintenance_window_start": "03:00",
        "maintenance_window_minutes": 120,
        "timezone_source": "channelwatch",
    }
    first_state = store.get_state()
    assert parse_timestamp(first_state["next_check_at"]) == now + timedelta(minutes=5)
    assert 0 <= first_state["stable_install_jitter_minutes"] < 120
    assert (
        UpdatePolicyStore(tmp_path, clock=lambda: now).get_state()[
            "stable_install_jitter_minutes"
        ]
        == first_state["stable_install_jitter_minutes"]
    )


@pytest.mark.parametrize("filename", ["update-policy.json", "update-scheduler.json"])
def test_policy_store_rejects_symlink_without_modifying_target(
    tmp_path: Path, filename: str
):
    runtime = tmp_path / "channelwatch-runtime"
    runtime.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"do_not":"change"}')
    (runtime / filename).symlink_to(outside)
    store = UpdatePolicyStore(tmp_path)

    with pytest.raises(UpdatePolicyStorageError, match="unsafe"):
        store.get() if filename == "update-policy.json" else store.get_state()
    assert outside.read_text() == '{"do_not":"change"}'


def test_policy_store_preserves_corrupt_policy(tmp_path: Path):
    runtime = tmp_path / "channelwatch-runtime"
    runtime.mkdir()
    policy_path = runtime / "update-policy.json"
    policy_path.write_text("not json")
    store = UpdatePolicyStore(tmp_path)

    with pytest.raises(UpdatePolicyStorageError, match="preserved"):
        store.get()
    assert policy_path.read_text() == "not json"


def test_policy_store_preserves_semantically_corrupt_scheduler_state(
    tmp_path: Path,
):
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    store = UpdatePolicyStore(tmp_path, clock=lambda: now)
    state = store.get_state()
    state["postponed_until"] = "not-a-timestamp"
    store.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(UpdatePolicyStorageError, match="preserved"):
        store.get_state()
    assert "not-a-timestamp" in store.state_path.read_text(encoding="utf-8")


def test_maintenance_opportunity_handles_dst_gap_and_repeated_hour():
    zone = ZoneInfo("America/New_York")
    gap_policy = UpdatePolicy(maintenance_window_start="02:00")
    gap = maintenance_opportunity(
        gap_policy,
        datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc),
        jitter_minutes=0,
        zone=zone,
    )
    assert gap.scheduled_at.hour == 3
    assert gap.scheduled_at.fold == 0

    repeated_policy = UpdatePolicy(maintenance_window_start="01:00")
    repeated = maintenance_opportunity(
        repeated_policy,
        datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc),
        jitter_minutes=30,
        zone=zone,
    )
    assert repeated.scheduled_at.hour == 1
    assert repeated.scheduled_at.fold == 0
    assert repeated.attempt_id.endswith("05:30:00Z")

    short_repeated_window = UpdatePolicy(
        maintenance_window_start="01:00", maintenance_window_minutes=30
    )
    first_fold = maintenance_opportunity(
        short_repeated_window,
        datetime(2026, 11, 1, 5, 10, tzinfo=timezone.utc),
        jitter_minutes=0,
        zone=zone,
    )
    second_fold = maintenance_opportunity(
        short_repeated_window,
        datetime(2026, 11, 1, 6, 10, tzinfo=timezone.utc),
        jitter_minutes=0,
        zone=zone,
    )
    assert first_fold.local_date == "2026-11-01"
    assert second_fold.local_date == "2026-11-02"


class _FakeManager:
    def __init__(self, payload: dict):
        self.payload = payload
        self.applies = 0
        self.checks = 0
        self.pending = False
        self.last_job: dict | None = None

    def check(self, **_kwargs):
        self.checks += 1
        return {"latest": self.payload, "update_available": True}

    def status(self):
        return {"last_job": self.last_job}

    def runtime_transition_pending(self):
        return self.pending

    def apply(self, version: str, **kwargs):
        self.applies += 1
        self.last_job = {
            "job_id": kwargs.get("job_id", "job-1"),
            "operation": "apply",
            "status": "restarting",
            "version": version,
            "bundle_sha256": kwargs.get(
                "expected_bundle_sha256", self.payload["bundle_sha256"]
            ),
            "scheduler_attempt_id": kwargs.get("scheduler_attempt_id"),
            "message": "Restarting",
        }
        return dict(self.last_job)


def _automatic_payload() -> dict:
    return {
        "version": "0.9.19",
        "bundle_sha256": "a" * 64,
        "delivery_mode": DeliveryMode.APP_UPDATE.value,
        "automatic_install_allowed": True,
        "automatic_install_after": "2026-01-01T00:00:00Z",
        "revocation_state": "active",
    }


def test_scheduler_honors_lock_preflight_drain_and_daily_attempt(tmp_path: Path):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    lock_events: list[str] = []

    @contextmanager
    def maintenance_lock():
        lock_events.append("enter")
        yield
        lock_events.append("exit")

    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
        maintenance_lock=maintenance_lock,
        install_preflight=lambda _payload: {
            "free_space_ok": True,
            "private_backup_ok": True,
            "maintenance_transactions_ok": True,
        },
        drain_notification_queue=lambda timeout: timeout == 20.0,
    )
    service.store.get_state()
    service.store.put_state(
        {
            "stable_install_jitter_minutes": 0,
            "next_check_at": "2026-08-24T03:00:00Z",
        }
    )

    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    policy_view = service.get_policy_view()
    assert policy_view["postpone_available"] is True
    assert policy_view["scheduled_release_sha256"] == "a" * 64
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == "install-started"
    assert manager.applies == 1
    assert lock_events == ["enter", "exit"]
    assert service.run_once(force_check=True)["status"] == "activation-pending"
    assert manager.applies == 1


@pytest.mark.parametrize(
    "delivery_mode",
    (
        DeliveryMode.APP_UPDATE_WITH_IMAGE_REFRESH.value,
        DeliveryMode.IMAGE_REQUIRED.value,
    ),
)
def test_scheduler_never_applies_or_quarantines_manager_image_required_release(
    tmp_path: Path,
    delivery_mode: str,
):
    payload = {**_automatic_payload(), "delivery_mode": delivery_mode}

    class ImageRequiredManager(_FakeManager):
        def check(self, **_kwargs):
            self.checks += 1
            return {
                "latest": self.payload,
                "update_available": True,
                "image_required": True,
            }

    manager = ImageRequiredManager(payload)
    now = datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: now,
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    result = service.run_once(force_check=True)

    assert result["status"] == "container-image-required"
    assert manager.applies == 0
    state = service.store.get_state()
    assert state["last_attempt"] is None
    assert state["quarantines"] == {}
    assert state["scheduled_restart_at"] is None
    assert state["scheduled_release_version"] is None
    assert state["scheduled_release_sha256"] is None
    assert state["scheduled_attempt_id"] is None
    assert state["maintenance_attention_code"] == "container-image-required"


def _start_automatic_activation(
    service: UpdateAutomationService,
    manager: _FakeManager,
    times: list[datetime],
) -> None:
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})
    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == "install-started"
    assert manager.applies == 1


def test_scheduler_reconciles_activation_success_before_new_catalog_check(
    tmp_path: Path,
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    _start_automatic_activation(service, manager, times)
    pending = service.store.get_state()
    assert pending["last_success_at"] is None
    assert pending["last_attempt"]["phase"] == "activation_pending"
    checks_before = manager.checks

    assert manager.last_job is not None
    manager.last_job["status"] = "success"
    restarted_service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    result = restarted_service.run_once(force_check=True)
    assert result["status"] == "activation-succeeded"
    assert manager.checks == checks_before
    completed = restarted_service.store.get_state()
    assert completed["last_success_at"] == format_timestamp(times[0])
    assert completed["last_attempt"]["phase"] == "success"
    assert completed["quarantines"] == {}


def test_scheduler_quarantines_failed_activation_and_suppresses_next_day(
    tmp_path: Path,
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    payload = _automatic_payload()
    manager = _FakeManager(payload)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    _start_automatic_activation(service, manager, times)
    checks_before = manager.checks
    assert manager.last_job is not None
    manager.last_job.update(
        {
            "status": "failed",
            "rollback_applied": True,
            "message": "Activation failed; previous runtime restored.",
        }
    )

    restarted_service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    assert restarted_service.run_once(force_check=True)["status"] == "activation-failed"
    assert manager.checks == checks_before
    identity = f"{payload['version']}:{payload['bundle_sha256']}"
    failed = restarted_service.store.get_state()
    assert identity in failed["quarantines"]
    assert failed["last_attempt"]["phase"] == "failed"
    assert failed["last_attempt"]["rollback_applied"] is True

    times[0] = datetime(2026, 8, 25, 3, 30, tzinfo=timezone.utc)
    assert restarted_service.run_once(force_check=True)["status"] == "release-quarantined"
    assert manager.applies == 1


def test_scheduler_preserves_pending_activation_across_process_restart(
    tmp_path: Path,
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    _start_automatic_activation(service, manager, times)
    assert manager.last_job is not None
    manager.last_job["status"] = "validating"
    checks_before = manager.checks

    restarted_service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    assert restarted_service.run_once(force_check=True)["status"] == "activation-pending"
    assert manager.checks == checks_before
    assert manager.applies == 1
    assert (
        restarted_service.store.get_state()["last_attempt"]["job_id"]
        == manager.last_job["job_id"]
    )


def test_scheduler_foreign_job_keeps_pending_attempt_fail_closed(tmp_path: Path):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    payload = _automatic_payload()
    manager = _FakeManager(payload)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    _start_automatic_activation(service, manager, times)
    checks_before = manager.checks
    manager.last_job = {
        "job_id": "foreign-job",
        "operation": "apply",
        "status": "success",
        "version": payload["version"],
    }

    restarted_service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    result = restarted_service.run_once(force_check=True)
    assert result["status"] == "activation-outcome-ambiguous"
    assert manager.checks == checks_before
    state = restarted_service.store.get_state()
    assert state["last_attempt"]["phase"] == "activation_pending"
    assert state["quarantines"] == {}
    assert state["maintenance_attention_code"] == "activation-outcome-ambiguous"


def test_scheduler_reconciles_durable_job_after_crash_before_apply_returns(
    tmp_path: Path,
):
    now = datetime(2026, 8, 24, 3, 35, tzinfo=timezone.utc)
    payload = _automatic_payload()
    manager = _FakeManager(payload)
    manager.last_job = {
        "job_id": "durable-apply-job",
        "operation": "apply",
        "status": "restarting",
        "version": payload["version"],
        "bundle_sha256": payload["bundle_sha256"],
        "scheduler_attempt_id": "2026-08-24@2026-08-24T03:00:00Z",
    }
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: now,
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state(
        {
            "last_attempt": {
                "version": payload["version"],
                "bundle_sha256": payload["bundle_sha256"],
                "attempted_at": format_timestamp(now),
                "attempt_id": "2026-08-24@2026-08-24T03:00:00Z",
                "job_id": "durable-apply-job",
                "phase": "apply_started",
                "automatic": True,
                "clear_hold_on_success": False,
            }
        }
    )

    assert service.run_once(force_check=True)["status"] == "activation-pending"
    pending = service.store.get_state()["last_attempt"]
    assert pending["job_id"] == "durable-apply-job"
    assert manager.checks == 0
    assert manager.applies == 0

    manager.last_job["status"] = "success"
    assert service.run_once(force_check=True)["status"] == "activation-succeeded"
    assert manager.checks == 0


@pytest.mark.parametrize(
    ("terminal_status", "expected_status"),
    (("success", "activation-succeeded"), ("failed", "activation-failed")),
)
def test_apply_exception_after_durable_handoff_reconciles_exact_terminal_outcome(
    tmp_path: Path,
    terminal_status: str,
    expected_status: str,
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    payload = _automatic_payload()

    class HandoffThenRaiseManager(_FakeManager):
        def apply(self, version: str, **kwargs):
            self.applies += 1
            self.last_job = {
                "job_id": kwargs["job_id"],
                "operation": "apply",
                "status": "restarting",
                "version": version,
                "bundle_sha256": kwargs["expected_bundle_sha256"],
                "scheduler_attempt_id": kwargs["scheduler_attempt_id"],
            }
            raise OSError("response lost after durable activation handoff")

    manager = HandoffThenRaiseManager(payload)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})
    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == "activation-pending"
    assert service.store.get_state()["last_attempt"]["phase"] == "activation_pending"

    assert manager.last_job is not None
    manager.last_job["status"] = terminal_status
    if terminal_status == "failed":
        manager.last_job["rollback_applied"] = True
    assert service.run_once(force_check=True)["status"] == expected_status
    state = service.store.get_state()
    identity = f"{payload['version']}:{payload['bundle_sha256']}"
    assert (identity in state["quarantines"]) is (terminal_status == "failed")
    assert manager.applies == 1


@pytest.mark.parametrize(
    "interrupted_status",
    sorted(("backing_up", "downloading", "verifying", "applying")),
)
def test_interrupted_pre_handoff_apply_does_not_stay_pending_forever(
    tmp_path: Path, interrupted_status: str
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    payload = _automatic_payload()

    class InterruptedManager(_FakeManager):
        def apply(self, version: str, **kwargs):
            self.applies += 1
            self.last_job = {
                "job_id": kwargs["job_id"],
                "operation": "apply",
                "status": interrupted_status,
                "version": version,
                "bundle_sha256": kwargs["expected_bundle_sha256"],
                "scheduler_attempt_id": kwargs["scheduler_attempt_id"],
            }
            raise OSError("process stopped during extraction")

    manager = InterruptedManager(payload)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})
    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == "apply-interrupted"
    state = service.store.get_state()
    assert state["last_attempt"]["phase"] == "interrupted"
    assert state["next_check_at"] == "2026-08-24T03:50:00Z"
    assert state["quarantines"] == {}


def test_automatic_manual_retry_apply_and_rollback_hold_share_one_operation_lock(
    tmp_path: Path,
):
    manager = _FakeManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc),
        timezone_provider=lambda: timezone.utc,
    )
    assert service._run_lock.acquire(blocking=False)
    try:
        assert service.run_once(force_check=True)["status"] == "busy"
        with pytest.raises(UpdateLockedError, match="already in progress"):
            service.apply_release(version="0.9.19")
        with pytest.raises(UpdateLockedError, match="already in progress"):
            service.retry_release(
                version="0.9.19",
                bundle_sha256="a" * 64,
            )
        with pytest.raises(UpdateLockedError, match="already in progress"):
            service.rollback_release()
        with pytest.raises(UpdateLockedError, match="already in progress"):
            service.record_rollback_hold(
                version="0.9.19",
                bundle_sha256="a" * 64,
            )
    finally:
        service._run_lock.release()


def test_manual_apply_tracks_exact_identity_for_post_restart_reconciliation(
    tmp_path: Path,
):
    payload = _automatic_payload()
    manager = _FakeManager(payload)
    now = datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: now,
        timezone_provider=lambda: timezone.utc,
    )

    result = service.apply_release(version=payload["version"])

    assert result["status"] == "restarting"
    state = service.store.get_state()
    attempt = state["last_attempt"]
    assert attempt["phase"] == "activation_pending"
    assert attempt["automatic"] is False
    assert attempt["version"] == payload["version"]
    assert attempt["bundle_sha256"] == payload["bundle_sha256"]
    assert result["job_id"] == attempt["job_id"]
    assert result["scheduler_attempt_id"] == attempt["attempt_id"]


def test_manual_apply_activation_failure_is_quarantined_by_exact_digest(
    tmp_path: Path,
):
    payload = _automatic_payload()

    class FailedManager(_FakeManager):
        def apply(self, version: str, **kwargs):
            result = super().apply(version, **kwargs)
            result.update(
                {
                    "status": "failed",
                    "rollback_applied": True,
                    "message": "Activation failed.",
                }
            )
            self.last_job = dict(result)
            return result

    manager = FailedManager(payload)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc),
        timezone_provider=lambda: timezone.utc,
    )

    result = service.apply_release(version=payload["version"])

    assert result["status"] == "failed"
    identity = f"{payload['version']}:{payload['bundle_sha256']}"
    state = service.store.get_state()
    assert state["last_attempt"]["phase"] == "failed"
    assert state["quarantines"][identity]["reason"] == "Activation failed."


def test_launcher_failure_quarantine_bootstraps_old_portal_attempt(
    tmp_path: Path,
):
    digest = "b" * 64
    recorded = record_failed_activation_quarantine(
        tmp_path,
        pending={
            "job_id": "legacy-job",
            "version": "0.9.18",
            "started_at": "2026-08-24T03:30:00Z",
        },
        active={
            "version": "0.9.18",
            "manifest": {"bundle_sha256": digest},
        },
        job={
            "job_id": "legacy-job",
            "operation": "apply",
            "status": "failed",
            "version": "0.9.18",
            "bundle_sha256": digest,
            "rollback_applied": True,
            "rolled_back_from": "0.9.18",
            "rolled_back_to": "image",
        },
        clock=lambda: datetime(2026, 8, 24, 3, 31, tzinfo=timezone.utc),
    )

    assert recorded is True
    state = UpdatePolicyStore(tmp_path).get_state()
    identity = f"0.9.18:{digest}"
    assert state["last_attempt"]["attempt_id"] == "activation@legacy-job"
    assert state["last_attempt"]["phase"] == "failed"
    assert state["quarantines"][identity]["reason"] == "activation_failed"


@pytest.mark.parametrize(
    "invalid_job",
    [
        {},
        {"operation": "check", "status": "failed"},
        {"operation": "apply", "status": "success"},
        {
            "operation": "apply",
            "status": "failed",
            "rollback_applied": False,
        },
    ],
)
def test_launcher_failure_quarantine_rejects_unproven_rollback(
    tmp_path: Path, invalid_job: dict[str, object]
):
    digest = "b" * 64
    job = {
        "job_id": "legacy-job",
        "version": "0.9.18",
        "bundle_sha256": digest,
        "rolled_back_from": "0.9.18",
        "rolled_back_to": "image",
        **invalid_job,
    }

    assert (
        record_failed_activation_quarantine(
            tmp_path,
            pending={"job_id": "legacy-job", "version": "0.9.18"},
            active={
                "version": "0.9.18",
                "manifest": {"bundle_sha256": digest},
            },
            job=job,
        )
        is False
    )
    assert UpdatePolicyStore(tmp_path).get_state()["quarantines"] == {}


def test_launcher_failure_quarantine_preserves_foreign_scheduler_attempt(
    tmp_path: Path,
):
    store = UpdatePolicyStore(tmp_path)
    foreign = {
        "attempt_id": "future-attempt",
        "job_id": "future-job",
        "version": "0.9.19",
        "bundle_sha256": "c" * 64,
        "attempted_at": "2026-08-24T03:30:00Z",
        "phase": "activation_pending",
        "automatic": True,
        "clear_hold_on_success": False,
    }
    store.transform_state(lambda state: {**state, "last_attempt": foreign})
    digest = "b" * 64

    assert record_failed_activation_quarantine(
        tmp_path,
        pending={"job_id": "legacy-job", "version": "0.9.18"},
        active={
            "version": "0.9.18",
            "manifest": {"bundle_sha256": digest},
        },
        job={
            "job_id": "legacy-job",
            "operation": "apply",
            "status": "failed",
            "version": "0.9.18",
            "bundle_sha256": digest,
            "rollback_applied": True,
            "rolled_back_from": "0.9.18",
            "rolled_back_to": "image",
        },
    )

    state = store.get_state()
    assert state["last_attempt"] == foreign
    assert f"0.9.18:{digest}" in state["quarantines"]
    assert state["maintenance_attention_code"] == (
        "update-activation-failed-concurrent-attempt"
    )


def test_rollback_and_reinstall_hold_commit_under_same_operation_lock(tmp_path: Path):
    payload = _automatic_payload()
    rollback_observations: list[bool] = []

    class RollbackManager(_FakeManager):
        def status(self):
            return {
                "last_job": self.last_job,
                "active_bundle": {
                    "version": payload["version"],
                    "manifest": {"bundle_sha256": payload["bundle_sha256"]},
                },
            }

        def rollback(self):
            identity = f"{payload['version']}:{payload['bundle_sha256']}"
            rollback_observations.append(
                identity in service.store.get_state()["rollback_holds"]
            )
            return {"status": "restarting", "operation": "rollback"}

    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: RollbackManager(payload),
        clock=lambda: datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc),
        timezone_provider=lambda: timezone.utc,
    )

    assert service.rollback_release()["status"] == "restarting"
    identity = f"{payload['version']}:{payload['bundle_sha256']}"
    assert rollback_observations == [True]
    assert identity in service.store.get_state()["rollback_holds"]


def test_rollback_exception_preserves_precommitted_reinstall_hold(tmp_path: Path):
    payload = _automatic_payload()

    class FailingRollbackManager(_FakeManager):
        def status(self):
            return {
                "last_job": self.last_job,
                "active_bundle": {
                    "version": payload["version"],
                    "manifest": {"bundle_sha256": payload["bundle_sha256"]},
                },
            }

        def rollback(self):
            raise RuntimeError("simulated process loss after rollback began")

    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: FailingRollbackManager(payload),
        clock=lambda: datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc),
        timezone_provider=lambda: timezone.utc,
    )

    with pytest.raises(RuntimeError, match="simulated process loss"):
        service.rollback_release()

    identity = f"{payload['version']}:{payload['bundle_sha256']}"
    assert identity in service.store.get_state()["rollback_holds"]


@pytest.mark.parametrize("preexisting_hold", [False, True])
def test_explicitly_rejected_rollback_clears_only_new_hold(
    tmp_path: Path, preexisting_hold: bool
):
    payload = _automatic_payload()

    class RejectedRollbackManager(_FakeManager):
        def status(self):
            return {
                "last_job": self.last_job,
                "active_bundle": {
                    "version": payload["version"],
                    "manifest": {"bundle_sha256": payload["bundle_sha256"]},
                },
            }

        def rollback(self):
            return {
                "status": "failed",
                "operation": "rollback",
                "rollback_applied": False,
            }

    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: RejectedRollbackManager(payload),
        clock=lambda: datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc),
        timezone_provider=lambda: timezone.utc,
    )
    if preexisting_hold:
        service.record_rollback_hold(
            version=payload["version"],
            bundle_sha256=payload["bundle_sha256"],
        )

    assert service.rollback_release()["status"] == "failed"
    identity = f"{payload['version']}:{payload['bundle_sha256']}"
    assert (identity in service.store.get_state()["rollback_holds"]) is preexisting_hold


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("operation", "rollback"),
        ("version", "9.9.9"),
        ("bundle_sha256", "b" * 64),
        ("scheduler_attempt_id", "foreign-attempt"),
    ),
)
def test_scheduler_rejects_same_job_id_with_wrong_release_identity(
    tmp_path: Path,
    field: str,
    replacement: str,
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    _start_automatic_activation(service, manager, times)
    assert manager.last_job is not None
    manager.last_job[field] = replacement
    manager.last_job["status"] = "success"

    assert service.run_once(force_check=True)["status"] == "activation-outcome-ambiguous"
    assert service.store.get_state()["last_attempt"]["phase"] == "activation_pending"
    assert manager.applies == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("phase", "unknown"),
        ("bundle_sha256", "not-a-digest"),
        ("job_id", ""),
        ("automatic", "yes"),
        ("completed_at", "2026-08-24T03:30:00Z"),
    ),
)
def test_scheduler_rejects_corrupt_nested_attempt_state(
    tmp_path: Path,
    field: str,
    replacement: object,
):
    store = UpdatePolicyStore(tmp_path)
    state = store.get_state()
    attempt = {
        "version": "0.9.19",
        "bundle_sha256": "a" * 64,
        "attempted_at": "2026-08-24T03:30:00Z",
        "attempt_id": "attempt-1",
        "job_id": "job-1",
        "phase": "activation_pending",
        "automatic": True,
        "clear_hold_on_success": False,
    }
    attempt[field] = replacement
    state["last_attempt"] = attempt
    store.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(UpdatePolicyStorageError, match="attempt"):
        store.get_state()


def test_scheduler_defers_when_notification_queue_does_not_drain(tmp_path: Path):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
        install_preflight=lambda _payload: {
            "free_space_ok": True,
            "private_backup_ok": True,
            "maintenance_transactions_ok": True,
        },
        drain_notification_queue=lambda _timeout: False,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == "notification-queue-busy"
    assert parse_timestamp(service.store.get_state()["next_check_at"]) == (
        times[0] + timedelta(minutes=15)
    )
    times[0] += timedelta(minutes=15)
    assert (
        service.run_once(force_check=True)["status"]
        == "notification-queue-deferred"
    )
    state = service.store.get_state()
    assert state["deferred_attempt_id"] == "2026-08-24@2026-08-24T03:00:00Z"
    assert state["next_check_at"] == "2026-08-25T03:00:00Z"
    view = service.get_policy_view()
    assert view["attention_required"] is True
    assert view["attention_code"] == "notification-queue-busy"
    assert "next maintenance window" in view["last_error"]
    assert service.run_once(force_check=True)["status"] == "maintenance-attempt-deferred"
    assert manager.applies == 0


def test_exhausted_network_retries_resume_at_next_daily_window(tmp_path: Path):
    now = datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: _FakeManager(_automatic_payload()),
        clock=lambda: now,
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    expected = (
        "2026-08-24T03:45:00Z",
        "2026-08-24T04:30:00Z",
        "2026-08-24T09:30:00Z",
        "2026-08-25T03:00:00Z",
    )
    observed = tuple(
        service._record_failure(error="offline", now=now)["next_check_at"]
        for _ in expected
    )

    assert observed == expected


@pytest.mark.parametrize(
    ("preflight", "expected_status"),
    [
        (
            {
                "free_space_ok": False,
                "private_backup_ok": True,
                "maintenance_transactions_ok": True,
            },
            "insufficient-free-space",
        ),
        (
            {
                "free_space_ok": True,
                "private_backup_ok": False,
                "maintenance_transactions_ok": True,
            },
            "private-backup-unavailable",
        ),
        (
            {
                "free_space_ok": True,
                "private_backup_ok": True,
                "maintenance_transactions_ok": False,
            },
            "unresolved-maintenance-transaction",
        ),
    ],
)
def test_scheduler_preflight_failure_defers_next_window_with_durable_attention(
    tmp_path: Path, preflight: dict[str, bool], expected_status: str
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
        install_preflight=lambda _payload: preflight,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == expected_status
    state = service.store.get_state()
    assert state["next_check_at"] == "2026-08-25T03:00:00Z"
    assert state["deferred_attempt_id"] == "2026-08-24@2026-08-24T03:00:00Z"
    assert service.get_policy_view()["attention_code"] == expected_status
    assert service.run_once(force_check=True)["status"] == "maintenance-attempt-deferred"
    assert service.get_policy_view()["attention_code"] == expected_status
    assert manager.applies == 0


def test_scheduler_releases_notification_hold_after_apply_attempt(tmp_path: Path):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    events: list[str] = []
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
        drain_notification_queue=lambda _timeout: events.append("drain") or True,
        resume_notification_queue=lambda: events.append("resume"),
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == "install-started"
    assert events == ["drain", "resume"]


def test_retryable_apply_failure_does_not_consume_daily_install_attempt(
    tmp_path: Path,
):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]

    class RetryManager(_FakeManager):
        def apply(self, version: str, **kwargs):
            self.applies += 1
            if self.applies == 1:
                raise OSError("temporary download failure")
            self.last_job = {
                "job_id": kwargs["job_id"],
                "operation": "apply",
                "status": "restarting",
                "version": version,
                "bundle_sha256": kwargs["expected_bundle_sha256"],
                "scheduler_attempt_id": kwargs["scheduler_attempt_id"],
                "message": "Restarting",
            }
            return dict(self.last_job)

    manager = RetryManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    times[0] += timedelta(minutes=5)
    assert service.run_once(force_check=True)["status"] == "retry-scheduled"
    failed = service.store.get_state()
    assert failed["last_install_attempt_id"] is None
    assert failed["last_install_local_date"] is None
    assert failed["next_check_at"] == "2026-08-24T03:50:00Z"

    times[0] += timedelta(minutes=15)
    assert service.run_once()["status"] == "install-started"
    assert manager.applies == 2
    installed = service.store.get_state()
    assert installed["last_install_local_date"] == "2026-08-24"


def test_manual_retry_keeps_exact_holds_until_activation_succeeds(tmp_path: Path):
    payload = _automatic_payload()

    class RetryManager(_FakeManager):
        def __init__(self, selected: dict):
            super().__init__(selected)
            self.result: dict | None = None
            self.failure: Exception | None = OSError("download interrupted")

        def apply(self, version: str, **kwargs):
            self.applies += 1
            if self.failure is not None:
                raise self.failure
            assert self.result is not None
            self.last_job = {
                **self.result,
                "job_id": kwargs["job_id"],
                "bundle_sha256": kwargs["expected_bundle_sha256"],
                "scheduler_attempt_id": kwargs["scheduler_attempt_id"],
            }
            return dict(self.last_job)

    manager = RetryManager(payload)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc),
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.record_rollback_hold(
        version=payload["version"],
        bundle_sha256=payload["bundle_sha256"],
    )
    state = service.store.get_state()
    identity = f"{payload['version']}:{payload['bundle_sha256']}"
    quarantines = dict(state["quarantines"])
    quarantines[identity] = {
        "version": payload["version"],
        "bundle_sha256": payload["bundle_sha256"],
        "reason": "activation_failed",
        "created_at": "2026-08-24T03:00:00Z",
    }
    service.store.put_state({"quarantines": quarantines})

    with pytest.raises(OSError, match="download interrupted"):
        service.retry_release(
            version=payload["version"],
            bundle_sha256=payload["bundle_sha256"],
        )
    failed = service.store.get_state()
    assert identity in failed["quarantines"]
    assert identity in failed["rollback_holds"]

    manager.failure = None
    manager.result = {
        "job_id": "manual-retry",
        "operation": "apply",
        "status": "restarting",
        "version": payload["version"],
    }
    result = service.retry_release(
        version=payload["version"],
        bundle_sha256=payload["bundle_sha256"],
    )
    assert result["status"] == "restarting"
    accepted = service.store.get_state()
    assert identity in accepted["quarantines"]
    assert identity in accepted["rollback_holds"]

    assert manager.last_job is not None
    manager.last_job["status"] = "success"
    assert service.run_once(force_check=True)["status"] == "activation-succeeded"
    activated = service.store.get_state()
    assert identity not in activated["quarantines"]
    assert identity not in activated["rollback_holds"]


def test_recovery_scheduler_selects_only_recovery_compatible_official_release(
    tmp_path: Path,
):
    now = datetime(2026, 8, 26, 3, 30, tzinfo=timezone.utc)
    recovery_payload = {
        **_automatic_payload(),
        "version": "0.9.19",
        "bundle_sha256": "b" * 64,
        "recovery_compatible": True,
    }
    ordinary_payload = {
        **_automatic_payload(),
        "version": "0.9.20",
        "bundle_sha256": "c" * 64,
        "recovery_compatible": False,
    }

    class RecoveryAwareManager(_FakeManager):
        def __init__(self):
            super().__init__(ordinary_payload)
            self.check_modes: list[bool] = []
            self.apply_modes: list[tuple[str, bool]] = []

        def check(self, *, recovery: bool = False):
            self.check_modes.append(recovery)
            selected = recovery_payload if recovery else ordinary_payload
            return {"latest": selected, "update_available": True}

        def apply(self, version: str, *, recovery: bool = False, **kwargs):
            self.apply_modes.append((version, recovery))
            self.applies += 1
            self.last_job = {
                "job_id": kwargs["job_id"],
                "operation": "apply",
                "status": "restarting",
                "version": version,
                "bundle_sha256": kwargs["expected_bundle_sha256"],
                "scheduler_attempt_id": kwargs["scheduler_attempt_id"],
                "message": "Restarting",
            }
            return dict(self.last_job)

    manager = RecoveryAwareManager()
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: now,
        timezone_provider=lambda: timezone.utc,
        recovery_state_provider=lambda: True,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    assert service.run_once(force_check=True)["status"] == "restart-countdown"
    service.store.put_state({"scheduled_restart_at": "2026-08-26T03:25:00Z"})
    assert service.run_once(force_check=True)["status"] == "install-started"
    assert manager.check_modes == [True, True]
    assert manager.apply_modes == [("0.9.19", True)]
    assert all(version != "0.9.20" for version, _mode in manager.apply_modes)


def test_recovery_scheduler_never_reapplies_exact_failed_asset(tmp_path: Path):
    now = datetime(2026, 8, 26, 3, 30, tzinfo=timezone.utc)
    failed_payload = {
        **_automatic_payload(),
        "bundle_sha256": "d" * 64,
        "recovery_compatible": True,
    }
    manager = _FakeManager(failed_payload)
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "official-recovery-mode.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "failed_version": failed_payload["version"],
                "failed_bundle_sha256": failed_payload["bundle_sha256"],
            }
        ),
        encoding="utf-8",
    )
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: now,
        timezone_provider=lambda: timezone.utc,
        recovery_state_provider=lambda: True,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})

    result = service.run_once(force_check=True)
    assert result["status"] == "recovery-waiting-newer-release"
    assert manager.applies == 0
    state = service.store.get_state()
    assert state["scheduled_restart_at"] is None
    assert "newer signed release" in state["last_error"]


def test_dirty_draft_gets_one_24_hour_postpone_per_release_attempt(tmp_path: Path):
    times = [datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)]
    manager = _FakeManager(_automatic_payload())
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
        clock=lambda: times[0],
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})
    assert service.run_once(force_check=True)["status"] == "restart-countdown"

    state = service.postpone(reason="dirty_report_draft")
    assert parse_timestamp(state["postponed_until"]) == times[0] + timedelta(hours=24)
    assert service.get_policy_view()["postpone_available"] is False
    with pytest.raises(ValueError, match="already used|no restart"):
        service.postpone(reason="dirty_report_draft")


def test_notify_only_policy_clears_a_pending_automatic_restart(tmp_path: Path):
    now = datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: _FakeManager(_automatic_payload()),
        clock=lambda: now,
        timezone_provider=lambda: timezone.utc,
    )
    service.store.get_state()
    service.store.put_state({"stable_install_jitter_minutes": 0})
    assert service.run_once(force_check=True)["status"] == "restart-countdown"

    service.put_policy({"mode": "notify_only"})
    view = service.get_policy_view()
    assert view["scheduled_restart_at"] is None
    assert view["postpone_available"] is False


def test_protocol_one_adopts_legacy_selection_and_completes_quorum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    release = tmp_path / "channelwatch-runtime" / "releases" / "v0.9.18"
    release.mkdir(parents=True)
    runtime = release.parents[1]
    (runtime / "active.json").write_text(
        json.dumps(
            {
                "version": "0.9.18",
                "path": str(release),
                "runtime_abi": RUNTIME_ABI,
                "settings_schema_version": 7,
            }
        )
    )
    (runtime / "rollback.json").write_text(
        json.dumps({"previous_active": None, "target_version": "0.9.18"})
    )
    (runtime / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "legacy-job",
                "operation": "apply",
                "status": "restarting",
                "version": "0.9.18",
            }
        )
    )
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(release))
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.15")
    monkeypatch.setattr(
        UpdateManager, "_start_adopted_activation_watchdog", lambda *_args: None
    )
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.18")

    manager.record_startup_success(
        component="core",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    active = json.loads((runtime / "active.json").read_text())
    assert active["activation_protocol"] == 1
    assert active["activation_id"]
    assert (runtime / "activation-pending.json").is_file()

    manager.record_startup_success(
        component="ui",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    assert not (runtime / "activation-pending.json").exists()
    assert json.loads((runtime / "update-job.json").read_text())["status"] == "success"


def _write_protocol_one_pending_failure_state(
    tmp_path: Path, *, digest: str = "d" * 64
) -> tuple[Path, Path]:
    release = tmp_path / "channelwatch-runtime" / "releases" / "v0.9.18"
    release.mkdir(parents=True)
    runtime = release.parents[1]
    active = {
        "version": "0.9.18",
        "path": str(release),
        "runtime_abi": RUNTIME_ABI,
        "settings_schema_version": 7,
        "activation_id": "legacy-generation",
        "activation_protocol": 1,
        "manifest": {"bundle_sha256": digest},
    }
    pending = {
        "job_id": "legacy-job",
        "version": "0.9.18",
        "activation_id": "legacy-generation",
        "path": str(release),
        "scheduler_attempt_id": "activation@legacy-job",
        "bundle_sha256": digest,
        "started_at": "2026-08-24T01:00:00Z",
        "deadline_at": "2026-08-24T01:02:00Z",
        "adopted_launcher_protocol": 1,
    }
    (runtime / "active.json").write_text(json.dumps(active), encoding="utf-8")
    (runtime / "activation-pending.json").write_text(
        json.dumps(pending), encoding="utf-8"
    )
    (runtime / "rollback.json").write_text(
        json.dumps({"previous_active": None, "target_version": "0.9.18"}),
        encoding="utf-8",
    )
    (runtime / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "legacy-job",
                "operation": "apply",
                "status": "validating",
                "version": "0.9.18",
                "scheduler_attempt_id": "activation@legacy-job",
                "bundle_sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    return runtime, release


def test_protocol_one_unhealthy_component_rolls_back_without_schema_two_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    digest = "d" * 64
    runtime, release = _write_protocol_one_pending_failure_state(
        tmp_path, digest=digest
    )
    restarts: list[bool] = []
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(release))
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.15",
        launcher_protocol=1,
        restart_callable=lambda: restarts.append(True) or True,
    )

    manager.record_startup_success(
        component="core",
        running_version="0.9.18",
        activation_id="legacy-generation",
        healthy=False,
    )

    assert restarts == [True]
    assert not (runtime / "active.json").exists()
    assert not manager.restart_required_path.exists()
    assert not list(runtime.glob("activation-*.json"))
    failed = json.loads((runtime / "update-job.json").read_text())
    assert failed["job_id"] == "legacy-job"
    assert failed["bundle_sha256"] == digest
    assert failed["scheduler_attempt_id"] == "activation@legacy-job"
    scheduler = json.loads((runtime / "update-scheduler.json").read_text())
    assert f"0.9.18:{digest}" in scheduler["quarantines"]


def test_protocol_one_healthcheck_failure_uses_direct_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime, release = _write_protocol_one_pending_failure_state(tmp_path)
    active = json.loads((runtime / "active.json").read_text())
    marker = {
        "component": "core",
        "version": "0.9.18",
        "activation_id": "legacy-generation",
        "path": str(release),
        "healthy": True,
        "ready_at": "2026-08-24T01:00:30Z",
    }
    (runtime / "activation-core-ready.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    restarts: list[bool] = []
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(release))
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.15",
        launcher_protocol=1,
        restart_callable=lambda: restarts.append(True) or True,
        healthcheck_callable=lambda: False,
    )

    manager.record_startup_success(
        component="ui",
        running_version="0.9.18",
        activation_id=str(active["activation_id"]),
        healthy=True,
    )

    assert restarts == [True]
    assert not (runtime / "active.json").exists()
    assert not manager.restart_required_path.exists()
    assert not list(runtime.glob("activation-*.json"))
    assert json.loads((runtime / "update-job.json").read_text())[
        "rollback_applied"
    ] is True


def test_protocol_one_rejected_restart_preserves_exact_failed_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    digest = "e" * 64
    runtime, release = _write_protocol_one_pending_failure_state(
        tmp_path, digest=digest
    )
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(release))
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.15",
        launcher_protocol=1,
        restart_callable=lambda: False,
    )

    with pytest.raises(UpdateRestartError, match="legacy container restart"):
        manager.record_startup_success(
            component="ui",
            running_version="0.9.18",
            activation_id="legacy-generation",
            healthy=False,
        )

    failed = json.loads((runtime / "update-job.json").read_text())
    assert failed["job_id"] == "legacy-job"
    assert failed["bundle_sha256"] == digest
    assert failed["scheduler_attempt_id"] == "activation@legacy-job"
    assert failed["restart_required"] is True
    assert failed["restart_started"] is False


def test_image_refresh_recovery_requires_healthy_core_and_ui_quorum(tmp_path: Path):
    runtime = tmp_path / "channelwatch-runtime"
    runtime.mkdir()
    (runtime / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "image-refresh-recovery-test",
                "operation": "image_refresh_recovery",
                "status": "validating",
                "version": "0.9.18",
                "legacy_pointer_deactivated": True,
                "startup_validation_id": "image-start-generation",
                "startup_validation_pending": True,
                "startup_components": {},
                "image_pull_completed": False,
                "restart_required": False,
            }
        )
    )
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.18",
    )

    manager.record_startup_success(
        component="core",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    after_core = json.loads((runtime / "update-job.json").read_text())
    assert after_core["status"] == "validating"
    assert after_core["image_pull_completed"] is False
    assert after_core["startup_validation_pending"] is True
    assert after_core["startup_components"]["core"]["healthy"] is True
    assert "ui" not in after_core["startup_components"]

    manager.record_startup_success(
        component="ui",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    completed = json.loads((runtime / "update-job.json").read_text())
    assert completed["status"] == "success"
    assert completed["image_pull_completed"] is True
    assert completed["startup_validation_pending"] is False
    assert completed["startup_components"]["core"]["healthy"] is True
    assert completed["startup_components"]["ui"]["healthy"] is True
    assert completed["validated_at"]


def test_image_refresh_recovery_never_claims_success_after_unhealthy_child(
    tmp_path: Path,
):
    runtime = tmp_path / "channelwatch-runtime"
    runtime.mkdir()
    (runtime / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "image-refresh-recovery-test",
                "operation": "image_refresh_recovery",
                "status": "validating",
                "version": "0.9.18",
                "legacy_pointer_deactivated": True,
                "startup_validation_id": "image-start-generation",
                "startup_validation_pending": True,
                "startup_components": {},
                "image_pull_completed": False,
                "restart_required": False,
            }
        )
    )
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.18")

    manager.record_startup_success(
        component="ui",
        running_version="0.9.18",
        activation_id="",
        healthy=False,
    )
    failed = json.loads((runtime / "update-job.json").read_text())
    assert failed["status"] == "failed"
    assert failed["image_pull_completed"] is False
    assert failed["startup_validation_pending"] is False
    assert failed["failed_component"] == "ui"

    manager.record_startup_success(
        component="core",
        running_version="0.9.18",
        activation_id="",
        healthy=True,
    )
    assert json.loads((runtime / "update-job.json").read_text()) == failed


def test_protocol_one_future_apply_avoids_unreadable_schema_two_journal(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.15",
        launcher_protocol=1,
        public_keys=public,
        fetcher=lambda url, _limit: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
    )

    manager.check()
    job = manager.apply()

    assert job["status"] == "restarting"
    assert not manager.restart_required_path.exists()
    assert json.loads(manager.active_path.read_text())["activation_protocol"] == 1
    assert manager.activation_pending_path.is_file()


def test_protocol_one_manual_rollback_restores_selection_when_restart_fails(
    tmp_path: Path,
):
    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.15",
        launcher_protocol=1,
        public_keys=public,
        fetcher=lambda url, _limit: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
    )
    manager.check()
    manager.apply()
    for component in ("core", "ui"):
        manager.record_startup_success(
            component=component,
            running_version="0.9.19",
            activation_id="",
            healthy=True,
        )
    selected_before = json.loads(manager.active_path.read_text(encoding="utf-8"))
    manager.restart_callable = lambda: False

    job = manager.rollback()

    assert job["status"] == "failed"
    assert job["rollback_applied"] is False
    assert json.loads(manager.active_path.read_text(encoding="utf-8")) == selected_before
    assert not manager.restart_required_path.exists()
    assert not manager.activation_pending_path.exists()


def test_v0917_cached_latest_is_refreshed_before_v0918_apply(tmp_path: Path):
    private, public = _key_pair()
    bundle = _bundle("0.9.19")
    manifest = _manifest(private, bundle, "0.9.19")
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.18",
        launcher_protocol=3,
        public_keys=public,
        fetcher=lambda url, _limit: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
    )
    manager._ensure_runtime()
    manager.latest_path.write_text(
        json.dumps({"schema": 1, "payload": {"version": "0.9.17"}})
    )

    assert manager.apply()["version"] == "0.9.19"
    assert json.loads(manager.latest_path.read_text())["payload"]["version"] == "0.9.19"


def test_legacy_launcher_guard_restores_image_without_exposing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = tmp_path / "channelwatch-runtime"
    release = runtime / "releases" / "v0.9.18"
    release.mkdir(parents=True)
    (runtime / "active.json").write_text(
        json.dumps({"version": "0.9.18", "path": str(release)})
    )
    (runtime / "rollback.json").write_text(json.dumps({"previous_active": None}))
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.9")
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(release))
    restarted: list[bool] = []

    status = guard_legacy_launcher_before_start(
        config_dir=tmp_path,
        running_version="0.9.18",
        restart_callable=lambda: restarted.append(True) or True,
    )

    assert status["allowed"] is False
    assert restarted == [True]
    assert not (runtime / "active.json").exists()
    assert "path" not in status
    job = json.loads((runtime / "update-job.json").read_text(encoding="utf-8"))
    assert job["minimum_image_version"] == "0.9.18"
    assert "preserve /config" in job["message"]
    assert "do not retry" in job["message"]


def test_recovery_status_filters_active_runtime_path(tmp_path: Path):
    service = OfficialRecoveryUpdateService(
        config_dir=tmp_path,
        current_version="0.9.18",
        runtime_abi=RUNTIME_ABI,
        settings_schema_version=7,
    )
    service.manager._ensure_runtime()
    service.manager.active_path.write_text(
        json.dumps({"version": "0.9.19", "path": "/private/runtime/path"})
    )

    status = service.status()
    assert "active_bundle" not in status
    assert "/private/runtime/path" not in json.dumps(status)


def test_recovery_service_waits_for_a_newer_exact_signed_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service = OfficialRecoveryUpdateService(
        config_dir=tmp_path,
        current_version="0.9.18",
        runtime_abi=RUNTIME_ABI,
        settings_schema_version=7,
    )
    service.manager._ensure_runtime()
    failed = {
        "version": "0.9.19",
        "bundle_sha256": "d" * 64,
        "delivery_mode": "app_update",
    }
    (service.manager.runtime_dir / "official-recovery-mode.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "failed_version": "0.9.19",
                "failed_bundle_sha256": "d" * 64,
            }
        ),
        encoding="utf-8",
    )
    service.manager.latest_path.write_text(
        json.dumps({"schema": 2, "payload": failed}), encoding="utf-8"
    )

    status = service.status()
    assert status["update_available"] is False
    assert status["latest"] is None
    assert status["recovery_waiting_for_newer_release"] is True

    monkeypatch.setattr(
        service.manager,
        "check",
        lambda **_kwargs: {"latest": failed, "update_available": True},
    )
    apply_calls: list[str | None] = []
    monkeypatch.setattr(
        service.manager,
        "apply",
        lambda version, **_kwargs: apply_calls.append(version) or {},
    )
    with pytest.raises(UpdateManifestError, match="same release"):
        service.apply("0.9.19")
    assert apply_calls == []


def test_update_status_reports_only_a_valid_selected_app_bundle(tmp_path: Path):
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.18",
        image_version="0.9.17",
        launcher_protocol=2,
    )
    release = manager.releases_dir / "v0.9.18"
    (release / "core").mkdir(parents=True)
    (release / "ui" / "backend").mkdir(parents=True)
    (release / "core" / "main.py").write_text("", encoding="utf-8")
    (release / "ui" / "backend" / "main.py").write_text("", encoding="utf-8")
    manager.active_path.write_text(
        json.dumps(
            {
                "version": "0.9.18",
                "path": str(release),
                "runtime_abi": RUNTIME_ABI,
                "settings_schema_version": 7,
            }
        ),
        encoding="utf-8",
    )

    assert manager.status()["runtime_source"] == "app_bundle"

    active = json.loads(manager.active_path.read_text(encoding="utf-8"))
    active["path"] = str(tmp_path / "outside-runtime")
    manager.active_path.write_text(json.dumps(active), encoding="utf-8")
    assert manager.status()["runtime_source"] == "image"
