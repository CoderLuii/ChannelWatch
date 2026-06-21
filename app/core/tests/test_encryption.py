import pytest

import os

from core.helpers.encryption import bootstrap_encryption_key


def test_bootstrap_encryption_key_generates_key(tmp_path):
    key_file = tmp_path / "encryption.key"

    key = bootstrap_encryption_key(key_file)

    assert len(key) == 32
    assert key_file.read_bytes() == key
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
    assert key_file.read_bytes() == original


def test_bootstrap_encryption_key_refuses_broad_permissions(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX file mode checks are not available on Windows")

    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"x" * 32)
    key_file.chmod(0o644)

    with pytest.raises(PermissionError, match="0600"):
        bootstrap_encryption_key(key_file)
