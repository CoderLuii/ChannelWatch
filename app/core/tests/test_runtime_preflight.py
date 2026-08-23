from pathlib import Path
import asyncio
import os
from unittest.mock import patch

import pytest

from core.helpers.atomic_io import _atomic_write_secret_bytes, secret_storage_key_status
from core.helpers.runtime_preflight import inspect_runtime_preflight
from core.helpers.runtime_preflight import RuntimePreflight


VALID_KEY = "review-only-0123456789abcdef0123456789abcdef"


def _clear_key_env(monkeypatch):
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)


def test_fresh_install_without_external_key_requires_setup(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    result = inspect_runtime_preflight(tmp_path / "encryption.key")
    assert result.public_payload() == {
        "status": "setup_required",
        "setup_required": True,
        "blockers": ["secret_storage_key_missing"],
        "warnings": [],
    }


def test_short_external_key_requires_setup(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", "too-short")
    result = inspect_runtime_preflight(tmp_path / "encryption.key")
    assert result.blockers == ("secret_storage_key_too_short",)


def test_valid_external_key_allows_fresh_install(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", VALID_KEY)
    assert VALID_KEY not in repr(secret_storage_key_status())
    assert inspect_runtime_preflight(tmp_path / "encryption.key").status == "ready"


def test_legacy_plaintext_key_remains_compatible_with_warning(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"legacy-logical-key-material-0001")
    key_file.chmod(0o600)
    result = inspect_runtime_preflight(key_file)
    assert result.status == "migration_recommended"
    assert not result.setup_required
    assert result.warnings == ("legacy_plaintext_key_migration_recommended",)


def test_local_key_with_unsafe_permissions_is_setup_required(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX key-mode enforcement is not available on Windows")

    _clear_key_env(monkeypatch)
    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(os.urandom(32))
    key_file.chmod(0o644)

    result = inspect_runtime_preflight(key_file)

    assert result.status == "setup_required"
    assert result.setup_required is True
    assert result.blockers == ("secret_storage_key_file_unreadable",)


@pytest.mark.parametrize("external_key", [None, VALID_KEY])
def test_corrupt_plaintext_key_requires_setup(
    tmp_path, monkeypatch, external_key
):
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
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", VALID_KEY)
    _atomic_write_secret_bytes(key_file, b"logical-key")
    assert inspect_runtime_preflight(key_file).status == "ready"

    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY",
        "different-0123456789abcdef0123456789abcdef",
    )
    assert inspect_runtime_preflight(key_file).blockers == (
        "secret_storage_key_mismatch",
    )


def test_external_key_file_uses_same_contract(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    material = tmp_path / "runtime-secret"
    material.write_text(VALID_KEY, encoding="utf-8")
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(material))
    assert secret_storage_key_status().available
    assert inspect_runtime_preflight(tmp_path / "encryption.key").status == "ready"


def test_unreadable_external_key_file_is_a_non_sensitive_blocker(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(tmp_path / "missing-secret")
    )
    result = inspect_runtime_preflight(tmp_path / "encryption.key")
    assert result.blockers == ("secret_storage_key_file_unreadable",)
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
    assert result.blockers == ("secret_storage_key_file_unreadable",)


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
            patch("core.main.bootstrap_encryption_key") as bootstrap,
            patch("core.main.get_settings") as get_settings,
            patch("core.main.log") as runtime_log,
        ):
            task = asyncio.create_task(__import__("core.main", fromlist=["main"]).main())
            await asyncio.sleep(0)
            assert not task.done()
            callbacks[0]()
            await asyncio.wait_for(task, timeout=1)
            bootstrap.assert_not_called()
            get_settings.assert_not_called()
            assert "Monitoring has not started" in runtime_log.call_args_list[0].args[0]

    asyncio.run(exercise())
