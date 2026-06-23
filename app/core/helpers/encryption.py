"""Bootstrap and load the ChannelWatch encryption key.

Public API:
- bootstrap_encryption_key()         raw 32-byte key file
- encrypt_value() / decrypt_value()  Fernet AEAD, "fernet:" prefix
- encrypt_dvr_api_keys()             batch encrypt api_key in dvr_servers list
- decrypt_dvr_api_keys()             batch decrypt api_key in dvr_servers list
"""

from pathlib import Path
import os
import stat

from .atomic_io import _atomic_write_secret_bytes

ENCRYPTION_KEY_FILE = Path(os.getenv("CONFIG_PATH", "/config")) / "encryption.key"
_ALLOWED_MODE = 0o600
FERNET_PREFIX = "fernet:"


class EncryptionKeyUnavailableError(RuntimeError):
    """Raised when DVR API keys cannot be encrypted safely."""


def _validate_key_permissions(path: Path) -> None:
    if os.name == "nt":
        return

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(
            f"Refusing to use {path}: permissions must be 0600 or stricter, got {oct(mode)}"
        )


def bootstrap_encryption_key(key_file: Path = ENCRYPTION_KEY_FILE) -> bytes:
    """Create or load the shared encryption key for `/config/encryption.key`."""
    if key_file.exists():
        _validate_key_permissions(key_file)
        return key_file.read_bytes()

    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    _atomic_write_secret_bytes(key_file, key)
    return key


def _make_fernet(raw_key: bytes):
    from base64 import urlsafe_b64encode
    from cryptography.fernet import Fernet

    return Fernet(urlsafe_b64encode(raw_key))


def is_fernet_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(FERNET_PREFIX)


def encrypt_value(plaintext: str, raw_key: bytes) -> str:
    """Encrypt a UTF-8 string. Returns 'fernet:<token>'."""
    token = _make_fernet(raw_key).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{FERNET_PREFIX}{token}"


def decrypt_value(ciphertext: str, raw_key: bytes) -> str:
    """Decrypt a 'fernet:<token>' string. Returns UTF-8 plaintext.

    Raises ValueError if the prefix is absent. Raises
    cryptography.fernet.InvalidToken if decryption fails.
    """
    if not is_fernet_encrypted(ciphertext):
        raise ValueError(f"Not a fernet-encrypted value: {ciphertext!r}")
    token = ciphertext[len(FERNET_PREFIX) :]
    return _make_fernet(raw_key).decrypt(token.encode("ascii")).decode("utf-8")


def encrypt_dvr_api_keys(
    dvr_servers: list, key_file: Path = ENCRYPTION_KEY_FILE
) -> list:
    """Return a new list where any plaintext api_key in each server dict is encrypted.

    No-ops for entries that are already 'fernet:...' or have no api_key.
    Creates the key file via bootstrap if absent.
    Raises EncryptionKeyUnavailableError if encryption cannot be performed safely.
    """
    plain = [
        s
        for s in dvr_servers
        if isinstance(s, dict)
        and s.get("api_key")
        and not is_fernet_encrypted(s["api_key"])
    ]
    if not plain:
        return list(dvr_servers)

    try:
        raw_key = bootstrap_encryption_key(key_file)
    except (OSError, PermissionError) as exc:
        raise EncryptionKeyUnavailableError(
            f"Unable to access encryption key at {key_file}"
        ) from exc

    result = []
    for server in dvr_servers:
        if (
            isinstance(server, dict)
            and server.get("api_key")
            and not is_fernet_encrypted(server["api_key"])
        ):
            server = dict(server)
            server["api_key"] = encrypt_value(server["api_key"], raw_key)
        result.append(server)
    return result


def decrypt_dvr_api_keys(
    dvr_servers: list, key_file: Path = ENCRYPTION_KEY_FILE
) -> list:
    """Return a new list where any 'fernet:...' api_key is decrypted.

    Silently leaves values unchanged on key-file-missing or decryption error.
    Never raises.
    """
    encrypted = [
        s
        for s in dvr_servers
        if isinstance(s, dict) and is_fernet_encrypted(s.get("api_key", ""))
    ]
    if not encrypted:
        return list(dvr_servers)

    try:
        raw_key = bootstrap_encryption_key(key_file)
    except (OSError, PermissionError):
        return list(dvr_servers)

    result = []
    for server in dvr_servers:
        if isinstance(server, dict) and is_fernet_encrypted(server.get("api_key", "")):
            try:
                server = dict(server)
                server["api_key"] = decrypt_value(server["api_key"], raw_key)
            except Exception:
                pass  # leave encrypted on failure rather than corrupting
        result.append(server)
    return result
