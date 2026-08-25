from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from core.helpers.atomic_io import _atomic_write_secret_bytes
from core.helpers.credential_maintenance import (
    reset_protected_credentials,
    rotate_managed_encryption_key,
)
from core.helpers.encryption import (
    bootstrap_encryption_key,
    decrypt_registered_credentials_with_diagnostics,
    decrypt_value,
    encrypt_value,
)
from core.helpers.key_manager import (
    MAX_SETTINGS_FILE_BYTES,
    MAX_STORED_KEY_BYTES,
    ManagedKeyUnavailableError,
    ensure_managed_key,
    inspect_key_recovery_status,
    install_recovered_raw_key,
    wait_for_managed_key_ready,
)
from core.helpers.maintenance_transaction import (
    configuration_maintenance_lock,
    recover_maintenance_transactions,
)
from core.helpers.protected_credentials import (
    disable_failed_protected_credential_owners,
    get_protected_credential_failures,
    preserve_failed_ciphertexts,
    publish_protected_credential_failures,
)


def _write_private(path, payload: bytes) -> None:
    path.write_bytes(payload)
    if os.name != "nt":
        path.chmod(0o600)


_HISTORICAL_ENVELOPE_FIXTURES = (
    ("0.9.5", "v095-test-wrapping-value-0123456789abcdef"),
    ("0.9.11", "v0911-test-wrapping-value-0123456789abcdef"),
    ("0.9.15", "v0915-test-wrapping-value-0123456789abcdef"),
)


def _build_v095_secret_envelope(logical_key: bytes, wrapping_value: str) -> bytes:
    """Build the exact envelope format shipped from v0.9.5 through v0.9.17."""

    wrapping_material = wrapping_value.strip().encode("utf-8")
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(wrapping_material).digest())
    token = Fernet(derived_key).encrypt(logical_key)
    return b"channelwatch-secret-v1\n" + token + b"\n"


def test_managed_secret_install_is_single_link_regular_0600_without_path_chmod(
    tmp_path,
):
    key_file = tmp_path / "encryption.key"
    with patch.object(
        Path,
        "chmod",
        side_effect=AssertionError("secret install must not chmod by pathname"),
    ):
        _atomic_write_secret_bytes(key_file, os.urandom(32))

    metadata = key_file.lstat()
    assert metadata.st_nlink == 1
    assert key_file.is_file() and not key_file.is_symlink()
    if os.name != "nt":
        assert metadata.st_mode & 0o777 == 0o600


def test_managed_secret_install_never_chmods_a_post_replace_symlink_target(
    tmp_path,
):
    key_file = tmp_path / "encryption.key"
    external = tmp_path / "external"
    external.write_bytes(b"outside")
    if os.name != "nt":
        external.chmod(0o644)
    original_mode = external.stat().st_mode & 0o777
    real_replace = os.replace

    def swap_after_replace(source, destination):
        real_replace(source, destination)
        Path(destination).unlink()
        Path(destination).symlink_to(external)

    with (
        patch("core.helpers.atomic_io.os.replace", side_effect=swap_after_replace),
        pytest.raises(PermissionError, match="could not be verified safely"),
    ):
        _atomic_write_secret_bytes(key_file, os.urandom(32))

    assert key_file.is_symlink()
    assert external.read_bytes() == b"outside"
    assert external.stat().st_mode & 0o777 == original_mode


@pytest.mark.parametrize(
    ("source_version", "legacy_storage_key"),
    _HISTORICAL_ENVELOPE_FIXTURES,
)
def test_exact_v095_legacy_envelope_migrates_same_logical_key(
    tmp_path,
    monkeypatch,
    source_version,
    legacy_storage_key,
):
    logical_key = bytes(range(32))
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", legacy_storage_key)
    key_file = tmp_path / "encryption.key"
    envelope = _build_v095_secret_envelope(logical_key, legacy_storage_key)
    _write_private(key_file, envelope)

    result = ensure_managed_key(key_file)

    assert result.key == logical_key, f"{source_version} logical key changed"
    assert result.migrated_legacy_envelope
    assert key_file.read_bytes() == logical_key

    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    assert ensure_managed_key(key_file).key == logical_key


@pytest.mark.parametrize(
    ("source_version", "legacy_storage_key"),
    _HISTORICAL_ENVELOPE_FIXTURES[1:],
)
@pytest.mark.parametrize(
    ("supplied_value", "expected_code"),
    (
        (None, "secret_storage_key_missing"),
        ("wrong-test-wrapping-value-0123456789abcdef", "secret_storage_key_mismatch"),
    ),
)
def test_historical_envelope_missing_or_wrong_input_is_preserved(
    tmp_path,
    monkeypatch,
    source_version,
    legacy_storage_key,
    supplied_value,
    expected_code,
):
    logical_key = bytes(range(32))
    key_file = tmp_path / "encryption.key"
    envelope = _build_v095_secret_envelope(logical_key, legacy_storage_key)
    _write_private(key_file, envelope)
    settings_file = tmp_path / "settings.json"
    settings_file.write_bytes(b'{"dvr_servers": []}\n')
    settings_before = settings_file.read_bytes()

    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)
    if supplied_value is None:
        monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    else:
        monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", supplied_value)

    with pytest.raises(ManagedKeyUnavailableError) as exc_info:
        ensure_managed_key(key_file, settings_file=settings_file)

    assert exc_info.value.code == expected_code
    assert key_file.read_bytes() == envelope, f"{source_version} envelope changed"
    assert settings_file.read_bytes() == settings_before
    status = inspect_key_recovery_status(key_file, settings_file=settings_file)
    assert status.state == "legacy_recovery_required"
    assert status.key_mode == "legacy_envelope"
    assert status.blocker == expected_code


def test_oversized_stored_key_is_rejected_before_reading_to_eof(tmp_path):
    key_file = tmp_path / "encryption.key"
    _write_private(key_file, b"x" * (MAX_STORED_KEY_BYTES + 1))

    with pytest.raises(ManagedKeyUnavailableError) as exc_info:
        ensure_managed_key(key_file)

    assert exc_info.value.code == "secret_storage_key_file_unreadable"
    assert key_file.stat().st_size == MAX_STORED_KEY_BYTES + 1


def test_oversized_settings_blocks_managed_key_creation(tmp_path):
    settings_file = tmp_path / "settings.json"
    prefix = b'{"dvr_servers":[]}'
    settings_file.write_bytes(
        prefix + (b" " * (MAX_SETTINGS_FILE_BYTES + 1 - len(prefix)))
    )
    key_file = tmp_path / "encryption.key"

    with pytest.raises(ManagedKeyUnavailableError) as exc_info:
        ensure_managed_key(key_file, settings_file=settings_file)

    assert exc_info.value.code == "secret_storage_key_file_unreadable"
    assert not key_file.exists()


def test_missing_key_with_ciphertext_is_never_replaced(tmp_path):
    orphaned_key = os.urandom(32)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "dvr_servers": [
                    {"id": "dvr-1", "api_key": encrypt_value("secret", orphaned_key)}
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManagedKeyUnavailableError) as exc_info:
        ensure_managed_key(tmp_path / "encryption.key", settings_file=settings_file)

    assert exc_info.value.code == "secret_storage_key_missing"
    assert not (tmp_path / "encryption.key").exists()

    status = inspect_key_recovery_status(
        tmp_path / "encryption.key",
        settings_file=settings_file,
    )
    assert status.state == "legacy_recovery_required"
    assert status.setup_required
    assert status.key_mode == "missing"
    assert status.blocker == "secret_storage_key_missing"


def test_recovery_status_maps_unwritable_storage_to_non_sensitive_blocker(tmp_path):
    with patch(
        "core.helpers.key_manager.managed_key_lock",
        side_effect=OSError("read-only filesystem with private details"),
    ):
        status = inspect_key_recovery_status(tmp_path / "encryption.key")

    assert status.state == "storage_unavailable"
    assert status.setup_required
    assert status.blocker == "secret_storage_key_file_unreadable"
    assert status.key_mode == "unreadable"
    assert "private details" not in repr(status)


def test_plaintext_first_boot_journals_key_and_encrypted_settings(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "dvr_servers": [{"id": "dvr-1", "api_key": "dvr-secret"}],
                "webhooks": [
                    {
                        "name": "local",
                        "url": "http://sink.test",
                        "secret": "hook-secret",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    key = bootstrap_encryption_key(
        tmp_path / "encryption.key",
        settings_file=settings_file,
    )

    stored = json.loads(settings_file.read_text(encoding="utf-8"))
    assert stored["dvr_servers"][0]["api_key"].startswith("fernet:")
    assert stored["webhooks"][0]["url"].startswith("fernet:")
    assert decrypt_value(stored["dvr_servers"][0]["api_key"], key) == "dvr-secret"
    assert not (tmp_path / ".channelwatch-transactions").exists()


def test_managed_key_lock_is_reentrant_for_outer_configuration_transaction(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"dvr_servers": [{"api_key": "plaintext-secret"}]}),
        encoding="utf-8",
    )

    with configuration_maintenance_lock(tmp_path):
        result = ensure_managed_key(
            tmp_path / "encryption.key",
            settings_file=settings_file,
            lock_timeout=0.05,
        )

    assert result.created
    assert len(result.key) == 32
    assert json.loads(settings_file.read_text(encoding="utf-8"))["dvr_servers"][0][
        "api_key"
    ].startswith("fernet:")


def test_in_process_key_lock_contention_honors_timeout(tmp_path):
    def contend():
        with configuration_maintenance_lock(tmp_path, timeout=0.05):
            return True

    with (
        configuration_maintenance_lock(tmp_path),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        future = pool.submit(contend)
        with pytest.raises(ManagedKeyUnavailableError, match="Timed out"):
            future.result(timeout=1)


def test_concurrent_first_boot_returns_one_key(tmp_path):
    key_file = tmp_path / "encryption.key"
    with ThreadPoolExecutor(max_workers=10) as pool:
        keys = list(pool.map(lambda _: bootstrap_encryption_key(key_file), range(50)))
    assert len(set(keys)) == 1
    assert key_file.read_bytes() == keys[0]


def test_existing_managed_key_prepares_private_persistent_lock(tmp_path):
    key_file = tmp_path / "encryption.key"
    key = os.urandom(32)
    _write_private(key_file, key)

    assert ensure_managed_key(key_file).key == key

    lock_file = tmp_path / ".encryption-key.lock"
    assert lock_file.is_file()
    if os.name != "nt":
        assert lock_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only flock semantics")
def test_existing_key_and_private_lock_work_after_read_only_remount(tmp_path):
    key_file = tmp_path / "encryption.key"
    lock_file = tmp_path / ".encryption-key.lock"
    key = os.urandom(32)
    _write_private(key_file, key)
    _write_private(lock_file, b"")
    real_open = os.open

    def force_read_only_lock_open(path, flags, *args):
        if Path(path) == lock_file and flags & os.O_RDWR:
            raise OSError("simulated read-only remount")
        return real_open(path, flags, *args)

    with patch(
        "core.helpers.key_manager.os.open", side_effect=force_read_only_lock_open
    ):
        assert ensure_managed_key(key_file).key == key


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only flock semantics")
def test_read_only_config_without_persistent_lock_fails_closed(tmp_path):
    key_file = tmp_path / "encryption.key"
    key = os.urandom(32)
    _write_private(key_file, key)
    lock_file = tmp_path / ".encryption-key.lock"
    real_open = os.open

    def force_read_only_lock_open(path, flags, *args):
        if Path(path) == lock_file and flags & os.O_RDWR:
            raise OSError("simulated read-only remount")
        return real_open(path, flags, *args)

    with (
        patch(
            "core.helpers.key_manager.os.open", side_effect=force_read_only_lock_open
        ),
        pytest.raises(ManagedKeyUnavailableError) as exc_info,
    ):
        ensure_managed_key(key_file)

    assert exc_info.value.code == "secret_storage_key_file_unreadable"


@pytest.mark.skipif(os.name == "nt", reason="POSIX link semantics")
def test_key_manager_refuses_symlink_and_hardlink(tmp_path):
    target = tmp_path / "target"
    _write_private(target, os.urandom(32))
    symlink = tmp_path / "symlink.key"
    symlink.symlink_to(target)
    with pytest.raises(ManagedKeyUnavailableError):
        ensure_managed_key(symlink)

    hardlink = tmp_path / "hardlink.key"
    os.link(target, hardlink)
    with pytest.raises(ManagedKeyUnavailableError):
        ensure_managed_key(hardlink)


def test_partial_corruption_isolated_and_no_fernet_token_returns(tmp_path):
    key_file = tmp_path / "encryption.key"
    key = os.urandom(32)
    _write_private(key_file, key)
    settings = {
        "dvr_servers": [
            {
                "id": "good",
                "enabled": True,
                "api_key": encrypt_value("good-secret", key),
            },
            {"id": "bad", "enabled": True, "api_key": "fernet:not-valid"},
        ],
        "webhooks": [
            {
                "name": "good",
                "enabled": True,
                "url": encrypt_value("http://good-sink.test", key),
                "secret": encrypt_value("good-hook", key),
            },
            {
                "name": "bad-secret",
                "enabled": True,
                "url": encrypt_value("http://sink.test", key),
                "secret": "fernet:not-valid",
            },
            {
                "name": "bad-url",
                "enabled": True,
                "url": "fernet:not-valid",
                "secret": encrypt_value("valid-secret", key),
            },
        ],
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    persisted_before = settings_file.read_bytes()

    result = decrypt_registered_credentials_with_diagnostics(settings, key_file)

    assert result.settings["dvr_servers"][0]["api_key"] == "good-secret"
    assert result.settings["dvr_servers"][0]["enabled"] is True
    assert result.settings["dvr_servers"][1]["api_key"] == ""
    assert result.settings["dvr_servers"][1]["enabled"] is False
    assert result.settings["webhooks"][0] == {
        "name": "good",
        "enabled": True,
        "url": "http://good-sink.test",
        "secret": "good-hook",
    }
    assert result.settings["webhooks"][1]["url"] == "http://sink.test"
    assert result.settings["webhooks"][1]["secret"] == ""
    assert result.settings["webhooks"][1]["enabled"] is False
    assert result.settings["webhooks"][2]["url"] == ""
    assert result.settings["webhooks"][2]["secret"] == "valid-secret"
    assert result.settings["webhooks"][2]["enabled"] is False
    assert result.failures == (
        "dvr_servers[1].api_key",
        "webhooks[1].secret",
        "webhooks[2].url",
    )
    assert get_protected_credential_failures() == result.failures
    assert "fernet:" not in json.dumps(result.settings)
    assert settings_file.read_bytes() == persisted_before
    assert settings["dvr_servers"][1]["enabled"] is True
    assert settings["webhooks"][1]["enabled"] is True

    recovery_status = inspect_key_recovery_status(key_file)
    assert recovery_status.state == "protected_credentials_need_attention"
    assert recovery_status.unreadable_credentials == result.failures


def test_failure_owner_disabling_ignores_unregistered_paths_and_does_not_mutate():
    settings = {
        "dvr_servers": [{"id": "one", "enabled": True, "api_key": ""}],
        "webhooks": [{"id": "hook", "enabled": True, "url": "", "secret": ""}],
        "unregistered": [{"enabled": True, "secret": ""}],
    }

    result = disable_failed_protected_credential_owners(
        settings,
        (
            "dvr_servers[0].api_key",
            "dvr_servers[99].api_key",
            "dvr_servers[-1].api_key",
            "webhooks[0].unknown",
            "unregistered[0].secret",
            "not-a-path",
        ),
    )

    assert result["dvr_servers"][0]["enabled"] is False
    assert result["webhooks"][0]["enabled"] is True
    assert result["unregistered"][0]["enabled"] is True
    assert settings["dvr_servers"][0]["enabled"] is True


def test_core_load_excludes_only_dvr_with_unreadable_registered_credential(tmp_path):
    from core.helpers.config import CoreSettings

    key = os.urandom(32)
    _write_private(tmp_path / "encryption.key", key)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "_version": 7,
                "dvr_servers": [
                    {
                        "id": "good",
                        "name": "Good DVR",
                        "host": "good-dvr.lan",
                        "port": 8089,
                        "enabled": True,
                        "api_key": encrypt_value("good-secret", key),
                    },
                    {
                        "id": "bad",
                        "name": "Bad DVR",
                        "host": "bad-dvr.lan",
                        "port": 8089,
                        "enabled": True,
                        "api_key": "fernet:not-valid",
                    },
                ],
                "webhooks": [
                    {
                        "id": "bad-hook",
                        "name": "Bad Hook",
                        "enabled": True,
                        "url": encrypt_value("http://sink.test", key),
                        "secret": "fernet:not-valid",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = CoreSettings(_config_root=tmp_path)

    assert [(item["id"], item["enabled"]) for item in loaded.dvr_servers] == [
        ("good", True),
        ("bad", False),
    ]
    assert [connection.id for connection in loaded.get_dvr_connections()] == ["good"]
    assert loaded.webhooks[0]["enabled"] is False
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["dvr_servers"][1]["api_key"] == "fernet:not-valid"
    assert persisted["dvr_servers"][1]["enabled"] is True
    assert persisted["webhooks"][0]["secret"] == "fernet:not-valid"
    assert persisted["webhooks"][0]["enabled"] is True


def test_failed_ciphertext_is_preserved_unless_plaintext_replaces_it():
    existing = {
        "dvr_servers": [
            {"id": "bad", "api_key": "fernet:original-dvr-token"},
        ],
        "webhooks": [
            {
                "name": "partial",
                "url": "fernet:original-url-token",
                "secret": "fernet:original-secret-token",
            }
        ],
    }
    incoming = {
        "dvr_servers": [{"id": "bad", "api_key": ""}],
        "webhooks": [
            {
                "name": "partial",
                "url": "replacement.example.test/hook",
                "secret": "****",
            }
        ],
    }

    merged = preserve_failed_ciphertexts(
        incoming,
        existing,
        (
            "dvr_servers[0].api_key",
            "webhooks[0].url",
            "webhooks[0].secret",
        ),
    )

    assert merged["dvr_servers"][0]["api_key"] == "fernet:original-dvr-token"
    assert merged["webhooks"][0]["secret"] == "fernet:original-secret-token"
    assert merged["webhooks"][0]["url"] == "replacement.example.test/hook"
    assert incoming["dvr_servers"][0]["api_key"] == ""


def test_failed_ciphertext_preservation_only_touches_registered_failed_path():
    existing = {
        "dvr_servers": [{"api_key": "fernet:old-token", "label": "old"}],
        "unregistered": [{"secret": "fernet:unregistered"}],
    }
    incoming = {
        "dvr_servers": [{"api_key": "", "label": "new"}],
        "unregistered": [{"secret": ""}],
    }

    merged = preserve_failed_ciphertexts(
        incoming,
        existing,
        ("webhooks[0].url", "unregistered[0].secret"),
    )

    assert merged == incoming


def test_protected_credential_failure_snapshot_is_deduplicated_and_replaced():
    publish_protected_credential_failures(
        ["dvr_servers[1].api_key", "dvr_servers[1].api_key", "webhooks[0].url"]
    )
    assert get_protected_credential_failures() == (
        "dvr_servers[1].api_key",
        "webhooks[0].url",
    )

    publish_protected_credential_failures([])
    assert get_protected_credential_failures() == ()


def test_raw_recovery_requires_proof_and_wakes_waiter(tmp_path):
    key = os.urandom(32)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {"dvr_servers": [{"id": "dvr-1", "api_key": encrypt_value("secret", key)}]}
        ),
        encoding="utf-8",
    )
    key_file = tmp_path / "encryption.key"

    async def exercise():
        shutdown = asyncio.Event()
        wake = asyncio.Event()
        waiter = asyncio.create_task(
            wait_for_managed_key_ready(
                shutdown,
                wake,
                key_file,
                settings_file=settings_file,
                initial_delay_seconds=30,
            )
        )
        await asyncio.sleep(0.05)
        with pytest.raises(ManagedKeyUnavailableError):
            install_recovered_raw_key(
                key_file,
                os.urandom(32),
                settings_file=settings_file,
            )
        install_recovered_raw_key(key_file, key, settings_file=settings_file)
        wake.set()
        result = await asyncio.wait_for(waiter, timeout=1)
        assert result is not None and result.key == key

    asyncio.run(exercise())


def test_reset_preserves_nonsecret_state_and_private_snapshot(tmp_path):
    key_file = tmp_path / "encryption.key"
    key = os.urandom(32)
    _write_private(key_file, key)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "tz": "UTC",
                "dvr_servers": [
                    {
                        "id": "dvr-1",
                        "host": "dvr.lan",
                        "enabled": True,
                        "api_key": encrypt_value("secret", key),
                    },
                    {
                        "id": "dvr-2",
                        "host": "dvr-without-key.lan",
                        "enabled": True,
                        "api_key": "",
                    },
                ],
                "webhooks": [
                    {
                        "name": "sink",
                        "enabled": True,
                        "url": encrypt_value("http://sink.test", key),
                        "secret": encrypt_value("hook", key),
                    },
                    {
                        "name": "unconfigured-sink",
                        "enabled": True,
                        "url": "",
                        "secret": "",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = reset_protected_credentials(tmp_path)

    restored = json.loads(settings_file.read_text(encoding="utf-8"))
    assert restored["tz"] == "UTC"
    assert restored["dvr_servers"][0]["host"] == "dvr.lan"
    assert restored["dvr_servers"][0]["api_key"] == ""
    assert restored["dvr_servers"][0]["enabled"] is False
    assert restored["dvr_servers"][1] == {
        "id": "dvr-2",
        "host": "dvr-without-key.lan",
        "enabled": True,
        "api_key": "",
    }
    assert restored["webhooks"][0]["url"] == ""
    assert restored["webhooks"][0]["secret"] == ""
    assert restored["webhooks"][0]["enabled"] is False
    assert restored["webhooks"][1] == {
        "name": "unconfigured-sink",
        "enabled": True,
        "url": "",
        "secret": "",
    }
    assert result.cleared_credentials == 3
    assert (result.recovery_snapshot / "encryption.key").read_bytes() == key
    if os.name != "nt":
        assert (tmp_path / "backups").stat().st_mode & 0o777 == 0o700
        assert (tmp_path / "backups" / "key-recovery").stat().st_mode & 0o777 == 0o700
        assert result.recovery_snapshot.stat().st_mode & 0o777 == 0o700
        for private_file in result.recovery_snapshot.iterdir():
            assert private_file.stat().st_mode & 0o777 == 0o600


def test_recovery_snapshot_directory_collision_never_reuses_existing_name(tmp_path):
    from core.helpers.credential_maintenance import _create_unique_private_directory

    recovery_dir = tmp_path / "key-recovery"
    recovery_dir.mkdir()
    collision = recovery_dir / "reset-fixed-collision"
    collision.mkdir()
    marker = collision / "original"
    marker.write_text("preserve", encoding="utf-8")

    with patch(
        "core.helpers.credential_maintenance._private_snapshot_token",
        side_effect=["collision", "unique"],
    ):
        reserved = _create_unique_private_directory(recovery_dir, "reset-fixed")

    assert reserved.name == "reset-fixed-unique"
    assert marker.read_text(encoding="utf-8") == "preserve"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_credential_reset_refuses_symlinked_recovery_directory(tmp_path):
    key_file = tmp_path / "encryption.key"
    _write_private(key_file, os.urandom(32))
    (tmp_path / "settings.json").write_text(
        json.dumps({"dvr_servers": []}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "backups").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PermissionError, match="unsafe private recovery directory"):
        reset_protected_credentials(tmp_path)

    assert list(outside.iterdir()) == []


def test_rotation_includes_webhook_fields(tmp_path):
    key_file = tmp_path / "encryption.key"
    key = os.urandom(32)
    _write_private(key_file, key)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "dvr_servers": [{"id": "dvr-1", "api_key": encrypt_value("dvr", key)}],
                "webhooks": [
                    {
                        "name": "sink",
                        "url": encrypt_value("http://sink.test", key),
                        "secret": encrypt_value("hook", key),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = rotate_managed_encryption_key(tmp_path)
    new_key = key_file.read_bytes()
    stored = json.loads(settings_file.read_text(encoding="utf-8"))

    assert result.rotated_credentials == 3
    assert new_key != key
    assert decrypt_value(stored["dvr_servers"][0]["api_key"], new_key) == "dvr"
    assert decrypt_value(stored["webhooks"][0]["url"], new_key) == "http://sink.test"
    assert decrypt_value(stored["webhooks"][0]["secret"], new_key) == "hook"


def test_rotation_recovers_older_transaction_before_deriving_new_generation(tmp_path):
    current_key = b"c" * 32
    pending_key = b"p" * 32
    _write_private(tmp_path / "encryption.key", current_key)
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "_version": 7,
                "dvr_servers": [
                    {"id": "dvr-1", "api_key": encrypt_value("current", current_key)}
                ],
                "webhooks": [],
            }
        )
    )
    pending_settings = {
        "_version": 7,
        "dvr_servers": [
            {"id": "dvr-1", "api_key": encrypt_value("pending", pending_key)}
        ],
        "webhooks": [],
    }
    transaction = tmp_path / ".channelwatch-transactions" / "older"
    (transaction / "old").mkdir(parents=True)
    (transaction / "new").mkdir()
    for filename, old_bytes, new_bytes in (
        (
            "settings.json",
            (tmp_path / "settings.json").read_bytes(),
            json.dumps(pending_settings).encode(),
        ),
        ("encryption.key", current_key, pending_key),
    ):
        _write_private(transaction / "old" / filename, old_bytes)
        _write_private(transaction / "new" / filename, new_bytes)
    _write_private(
        transaction / "journal.json",
        json.dumps(
            {
                "version": 1,
                "state": "committing",
                "files": ["encryption.key", "settings.json"],
                "absent_before": [],
            }
        ).encode(),
    )

    rotate_managed_encryption_key(tmp_path)

    rotated_key = (tmp_path / "encryption.key").read_bytes()
    rotated_settings = json.loads((tmp_path / "settings.json").read_text())
    assert decrypt_value(
        rotated_settings["dvr_servers"][0]["api_key"], rotated_key
    ) == "pending"
    assert recover_maintenance_transactions(tmp_path) == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_transaction_recovery_refuses_symlink_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".channelwatch-transactions").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(
        PermissionError, match="Unsafe maintenance transaction directory"
    ):
        recover_maintenance_transactions(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX link and special-file semantics")
def test_transaction_recovery_refuses_symlink_transaction_directory(tmp_path):
    root = tmp_path / ".channelwatch-transactions"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "attacker").symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        PermissionError, match="Unsafe maintenance transaction directory"
    ):
        recover_maintenance_transactions(tmp_path)


def _write_recovery_transaction(tmp_path, *, state="committing"):
    root = tmp_path / ".channelwatch-transactions"
    transaction = root / "transaction"
    (transaction / "old").mkdir(parents=True)
    (transaction / "new").mkdir()
    (transaction / "journal.json").write_text(
        json.dumps(
            {
                "version": 1,
                "state": state,
                "files": ["settings.json"],
                "absent_before": [],
            }
        ),
        encoding="utf-8",
    )
    return transaction


@pytest.mark.parametrize(
    ("state", "expected"),
    [("prepared", b"old-settings"), ("committing", b"new-settings")],
)
def test_transaction_recovery_rolls_back_or_forward_by_journal_state(
    tmp_path, state, expected
):
    transaction = _write_recovery_transaction(tmp_path, state=state)
    (transaction / "old" / "settings.json").write_bytes(b"old-settings")
    (transaction / "new" / "settings.json").write_bytes(b"new-settings")
    (tmp_path / "settings.json").write_bytes(b"partially-installed")

    assert recover_maintenance_transactions(tmp_path) == 1

    assert (tmp_path / "settings.json").read_bytes() == expected
    assert not (tmp_path / ".channelwatch-transactions").exists()


def test_transaction_recovery_is_idempotent_after_other_process_removes_root(
    tmp_path,
):
    transaction = _write_recovery_transaction(tmp_path, state="committing")
    (transaction / "old" / "settings.json").write_bytes(b"old-settings")
    (transaction / "new" / "settings.json").write_bytes(b"new-settings")
    (tmp_path / "settings.json").write_bytes(b"partially-installed")

    assert recover_maintenance_transactions(tmp_path) == 1
    assert recover_maintenance_transactions(tmp_path) == 0
    assert (tmp_path / "settings.json").read_bytes() == b"new-settings"


def test_credential_reset_recovers_older_transaction_before_reading_generation(
    tmp_path,
):
    current_key = b"c" * 32
    pending_key = b"p" * 32
    _write_private(tmp_path / "encryption.key", current_key)
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "_version": 7,
                "dvr_servers": [
                    {
                        "id": "dvr-1",
                        "enabled": True,
                        "api_key": encrypt_value("current-secret", current_key),
                    }
                ],
                "webhooks": [],
            }
        ),
        encoding="utf-8",
    )

    transaction = tmp_path / ".channelwatch-transactions" / "older"
    (transaction / "old").mkdir(parents=True)
    (transaction / "new").mkdir()
    pending_settings = {
        "_version": 7,
        "dvr_servers": [
            {
                "id": "dvr-1",
                "enabled": True,
                "api_key": encrypt_value("pending-secret", pending_key),
            }
        ],
        "webhooks": [],
    }
    for filename, old_bytes, new_bytes in (
        (
            "settings.json",
            (tmp_path / "settings.json").read_bytes(),
            json.dumps(pending_settings).encode("utf-8"),
        ),
        ("encryption.key", current_key, pending_key),
    ):
        _write_private(transaction / "old" / filename, old_bytes)
        _write_private(transaction / "new" / filename, new_bytes)
    _write_private(
        transaction / "journal.json",
        json.dumps(
            {
                "version": 1,
                "state": "committing",
                "files": ["encryption.key", "settings.json"],
                "absent_before": [],
            }
        ).encode("utf-8"),
    )

    result = reset_protected_credentials(tmp_path)

    reset_settings = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    assert reset_settings["dvr_servers"][0]["api_key"] == ""
    assert reset_settings["dvr_servers"][0]["enabled"] is False
    assert recover_maintenance_transactions(tmp_path) == 0
    snapshot_settings = json.loads(
        (result.recovery_snapshot / "settings.json").read_text("utf-8")
    )
    snapshot_key = (result.recovery_snapshot / "encryption.key").read_bytes()
    assert (
        decrypt_value(snapshot_settings["dvr_servers"][0]["api_key"], snapshot_key)
        == "pending-secret"
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX link and special-file semantics")
@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo"])
def test_transaction_recovery_refuses_unsafe_journal(tmp_path, unsafe_kind):
    transaction = _write_recovery_transaction(tmp_path)
    journal = transaction / "journal.json"
    journal.unlink()
    outside = tmp_path / "outside-journal"
    outside.write_text("{}", encoding="utf-8")
    if unsafe_kind == "symlink":
        journal.symlink_to(outside)
    elif unsafe_kind == "hardlink":
        os.link(outside, journal)
    else:
        os.mkfifo(journal)

    with pytest.raises(PermissionError, match="Unsafe maintenance transaction file"):
        recover_maintenance_transactions(tmp_path)

    assert outside.read_text(encoding="utf-8") == "{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX link and special-file semantics")
@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo"])
def test_transaction_recovery_refuses_unsafe_staged_file(tmp_path, unsafe_kind):
    transaction = _write_recovery_transaction(tmp_path)
    staged = transaction / "new" / "settings.json"
    outside = tmp_path / "outside-settings"
    outside.write_text('{"outside": true}', encoding="utf-8")
    if unsafe_kind == "symlink":
        staged.symlink_to(outside)
    elif unsafe_kind == "hardlink":
        os.link(outside, staged)
    else:
        os.mkfifo(staged)

    with pytest.raises(PermissionError, match="Unsafe maintenance transaction file"):
        recover_maintenance_transactions(tmp_path)

    assert outside.read_text(encoding="utf-8") == '{"outside": true}'
    assert not (tmp_path / "settings.json").exists()
