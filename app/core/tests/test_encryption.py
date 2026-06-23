import pytest

import os

from core.helpers.atomic_io import _atomic_read_secret_bytes, _is_secret_envelope
from core.helpers.encryption import bootstrap_encryption_key


def test_bootstrap_encryption_key_generates_key(tmp_path):
    key_file = tmp_path / "encryption.key"

    key = bootstrap_encryption_key(key_file)

    assert len(key) == 32
    assert key_file.read_bytes() != key
    assert _is_secret_envelope(key_file.read_bytes())
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
    assert _is_secret_envelope(key_file.read_bytes())


def test_bootstrap_encryption_key_requires_storage_key_for_new_write(tmp_path, monkeypatch):
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)

    from core.helpers.atomic_io import SecretStorageKeyUnavailableError

    with pytest.raises(SecretStorageKeyUnavailableError):
        bootstrap_encryption_key(tmp_path / "encryption.key")


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
    from cryptography.fernet import InvalidToken

    key_file = tmp_path / "encryption.key"
    key = bootstrap_encryption_key(key_file)
    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY",
        "channelwatch-test-secret-storage-key-0002",
    )

    with pytest.raises(InvalidToken):
        _atomic_read_secret_bytes(key_file)

    monkeypatch.setenv(
        "CHANNELWATCH_SECRET_STORAGE_KEY",
        "channelwatch-test-secret-storage-key-0001",
    )
    assert _atomic_read_secret_bytes(key_file) == key


def test_bootstrap_encryption_key_refuses_broad_permissions(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX file mode checks are not available on Windows")

    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"x" * 32)
    key_file.chmod(0o644)

    with pytest.raises(PermissionError, match="0600"):
        bootstrap_encryption_key(key_file)
