from pathlib import Path
import asyncio
import os
import stat
from unittest.mock import patch

import pytest

from core.helpers.atomic_io import (
    _encrypt_secret_bytes,
    atomic_write_bytes,
    secret_storage_key_status,
)
from core.helpers.runtime_preflight import inspect_runtime_preflight
from core.helpers.runtime_preflight import RuntimePreflight


VALID_KEY = "review-only-0123456789abcdef0123456789abcdef"


def _clear_key_env(monkeypatch):
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)


def test_fresh_install_without_external_key_is_prepared_automatically(
    tmp_path, monkeypatch
):
    _clear_key_env(monkeypatch)
    result = inspect_runtime_preflight(tmp_path / "encryption.key")
    assert result.public_payload() == {
        "status": "ready",
        "setup_required": False,
        "blockers": [],
        "warnings": [],
    }
    assert len((tmp_path / "encryption.key").read_bytes()) == 32


def test_short_external_key_is_ignored_for_fresh_managed_storage(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", "too-short")
    result = inspect_runtime_preflight(tmp_path / "encryption.key")
    assert result.status == "ready"


def test_valid_external_key_allows_fresh_install(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", VALID_KEY)
    assert VALID_KEY not in repr(secret_storage_key_status())
    assert inspect_runtime_preflight(tmp_path / "encryption.key").status == "ready"


def test_existing_managed_key_ignores_changed_external_migration_input(
    tmp_path, monkeypatch
):
    _clear_key_env(monkeypatch)
    key_file = tmp_path / "encryption.key"
    managed_key = os.urandom(32)
    key_file.write_bytes(managed_key)
    key_file.chmod(0o600)
    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY",
        "wrong-for-this-install-0123456789abcdef0123456789",
    )

    result = inspect_runtime_preflight(key_file)

    assert result.status == "ready"
    assert key_file.read_bytes() == managed_key


def test_legacy_plaintext_key_is_the_managed_format(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"legacy-logical-key-material-0001")
    key_file.chmod(0o600)
    result = inspect_runtime_preflight(key_file)
    assert result.status == "ready"
    assert not result.setup_required
    assert result.warnings == ()


def test_local_key_with_unsafe_permissions_is_repaired(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX key-mode enforcement is not available on Windows")

    _clear_key_env(monkeypatch)
    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(os.urandom(32))
    key_file.chmod(0o644)

    result = inspect_runtime_preflight(key_file)

    assert result.status == "ready"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


@pytest.mark.parametrize("external_key", [None, VALID_KEY])
def test_corrupt_plaintext_key_requires_setup(tmp_path, monkeypatch, external_key):
    _clear_key_env(monkeypatch)
    if external_key is not None:
        monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", external_key)
    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"not-a-valid-legacy-key")
    key_file.chmod(0o600)

    result = inspect_runtime_preflight(key_file)

    assert result.status == "setup_required"
    assert result.setup_required is True
    assert result.blockers == ("secret_storage_key_mismatch",)


def test_corrupt_envelope_requires_setup(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", VALID_KEY)
    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"channelwatch-secret-v1\nnot-a-valid-token\n")
    key_file.chmod(0o600)

    result = inspect_runtime_preflight(key_file)

    assert result.status == "setup_required"
    assert result.blockers == ("secret_storage_key_mismatch",)


def test_enveloped_key_requires_same_external_key(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    key_file = tmp_path / "encryption.key"
    logical_key = os.urandom(32)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", VALID_KEY)
    atomic_write_bytes(key_file, _encrypt_secret_bytes(logical_key))
    key_file.chmod(0o600)
    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY",
        "different-0123456789abcdef0123456789abcdef",
    )
    assert inspect_runtime_preflight(key_file).blockers == (
        "secret_storage_key_mismatch",
    )
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", VALID_KEY)
    assert inspect_runtime_preflight(key_file).status == "ready"
    assert key_file.read_bytes() == logical_key


def test_external_key_file_uses_same_contract(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    material = tmp_path / "runtime-secret"
    material.write_text(VALID_KEY, encoding="utf-8")
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(material))
    assert secret_storage_key_status().available
    assert inspect_runtime_preflight(tmp_path / "encryption.key").status == "ready"


def test_unreadable_external_key_file_is_ignored_for_fresh_storage(
    tmp_path, monkeypatch
):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(tmp_path / "missing-secret")
    )
    result = inspect_runtime_preflight(tmp_path / "encryption.key")
    assert result.status == "ready"
    assert VALID_KEY not in repr(result)


def test_non_utf8_external_key_file_is_a_non_sensitive_blocker(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    material = tmp_path / "runtime-secret"
    material.write_bytes(b"\xff\xfe\x00\x80")
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(material))

    status = secret_storage_key_status()
    result = inspect_runtime_preflight(tmp_path / "encryption.key")

    assert status.available is False
    assert status.code == "secret_storage_key_file_unreadable"
    assert result.status == "ready"


@pytest.mark.skipif(os.name == "nt", reason="POSIX link and FIFO semantics")
@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo"])
def test_legacy_external_key_file_rejects_links_and_special_files(
    tmp_path, monkeypatch, unsafe_kind
):
    _clear_key_env(monkeypatch)
    source = tmp_path / "source"
    source.write_text(VALID_KEY, encoding="utf-8")
    material = tmp_path / "runtime-secret"
    if unsafe_kind == "symlink":
        material.symlink_to(source)
    elif unsafe_kind == "hardlink":
        os.link(source, material)
    else:
        os.mkfifo(material)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(material))

    status = secret_storage_key_status()
    assert status.available is False
    assert status.code == "secret_storage_key_file_unreadable"


def test_legacy_external_key_file_is_bounded(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    material = tmp_path / "runtime-secret"
    material.write_bytes(b"x" * 5000)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(material))

    status = secret_storage_key_status()
    assert status.available is False
    assert status.code == "secret_storage_key_file_unreadable"


def test_core_stays_alive_but_does_not_initialize_when_setup_is_required():
    callbacks = []

    def capture_handler(_loop, _signal, callback):
        callbacks.append(callback)

    async def exercise():
        with (
            patch("core.main.sys.argv", ["channelwatch"]),
            patch(
                "core.main.inspect_runtime_preflight",
                return_value=RuntimePreflight(
                    status="setup_required",
                    setup_required=True,
                    blockers=("secret_storage_key_missing",),
                ),
            ),
            patch("core.main._install_signal_handler", side_effect=capture_handler),
            patch(
                "core.update_center.guard_legacy_launcher_before_start",
                return_value={"allowed": True},
            ),
            patch("core.main.recover_maintenance_transactions"),
            patch("core.main.bootstrap_encryption_key") as bootstrap,
            patch("core.main.get_settings") as get_settings,
            patch("core.main.log") as runtime_log,
        ):
            task = asyncio.create_task(
                __import__("core.main", fromlist=["main"]).main()
            )
            await asyncio.sleep(0)
            assert not task.done()
            callbacks[0]()
            await asyncio.wait_for(task, timeout=1)
            bootstrap.assert_not_called()
            get_settings.assert_not_called()
            assert runtime_log.called

    asyncio.run(exercise())
