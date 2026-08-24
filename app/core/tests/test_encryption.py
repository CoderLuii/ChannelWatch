import pytest

import os

from core.helpers.atomic_io import (
    _atomic_read_secret_bytes,
    _encrypt_secret_bytes,
    _is_secret_envelope,
    atomic_write_bytes,
)
from core.helpers.encryption import bootstrap_encryption_key


def test_bootstrap_encryption_key_generates_key(tmp_path):
    key_file = tmp_path / "encryption.key"

    key = bootstrap_encryption_key(key_file)

    assert len(key) == 32
    assert key_file.read_bytes() == key
    assert not _is_secret_envelope(key_file.read_bytes())
    assert _atomic_read_secret_bytes(key_file) == key
    if os.name == "nt":
        return
    assert key_file.stat().st_mode & 0o777 == 0o600


def test_bootstrap_encryption_key_reuses_existing_key(tmp_path):
    key_file = tmp_path / "encryption.key"
    original = b"x" * 32
    key_file.write_bytes(original)
    key_file.chmod(0o600)

    key = bootstrap_encryption_key(key_file)

    assert key == original
    assert _atomic_read_secret_bytes(key_file) == original
    assert not _is_secret_envelope(key_file.read_bytes())


def test_bootstrap_encryption_key_needs_no_external_storage_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)

    key_file = tmp_path / "encryption.key"
    assert len(bootstrap_encryption_key(key_file)) == 32
    assert len(key_file.read_bytes()) == 32


def test_bootstrap_encryption_key_loads_existing_plaintext_without_storage_key(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)
    key_file = tmp_path / "encryption.key"
    original = b"x" * 32
    key_file.write_bytes(original)
    key_file.chmod(0o600)

    assert bootstrap_encryption_key(key_file) == original
    assert key_file.read_bytes() == original


def test_encrypted_key_rejects_wrong_storage_key(tmp_path, monkeypatch):
    key_file = tmp_path / "encryption.key"
    key = os.urandom(32)
    atomic_write_bytes(key_file, _encrypt_secret_bytes(key))
    key_file.chmod(0o600)
    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY",
        "channelwatch-test-secret-storage-key-0002",
    )

    from core.helpers.key_manager import ManagedKeyUnavailableError

    with pytest.raises(ManagedKeyUnavailableError) as exc_info:
        bootstrap_encryption_key(key_file)
    assert exc_info.value.code == "secret_storage_key_mismatch"

    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY",
        "channelwatch-test-secret-storage-key-0001",
    )
    assert bootstrap_encryption_key(key_file) == key
    assert key_file.read_bytes() == key


def test_bootstrap_encryption_key_repairs_broad_permissions(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX file mode checks are not available on Windows")

    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"x" * 32)
    key_file.chmod(0o644)

    assert bootstrap_encryption_key(key_file) == b"x" * 32
    assert key_file.stat().st_mode & 0o777 == 0o600
