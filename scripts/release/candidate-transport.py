#!/usr/bin/env python3
"""Encrypt private release-candidate inputs across untrusted workflow jobs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import stat
import struct
import tempfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"CHANNELWATCH-CANDIDATE-TRANSPORT-V1\n"
SCHEMA = 1
NONCE_BYTES = 12
SALT_BYTES = 32
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SOURCE_SHA = re.compile(r"[0-9a-f]{40}")
_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_ARTIFACT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _load_context(path: Path) -> tuple[dict[str, Any], str]:
    _regular_file(path, "--context")
    raw = path.read_bytes()
    if len(raw) > 16 * 1024:
        raise ValueError("Candidate transport context is too large.")
    value = json.loads(raw.decode("utf-8"))
    required = {
        "schema",
        "repository",
        "run_id",
        "run_attempt",
        "source_sha",
        "release_tag",
        "release_config_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Candidate transport context fields are invalid.")
    if (
        value["schema"] != SCHEMA
        or not isinstance(value["repository"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"])
        or not isinstance(value["run_id"], str)
        or not value["run_id"].isdigit()
        or not isinstance(value["run_attempt"], str)
        or not value["run_attempt"].isdigit()
        or not isinstance(value["source_sha"], str)
        or _SOURCE_SHA.fullmatch(value["source_sha"]) is None
        or not isinstance(value["release_tag"], str)
        or _TAG.fullmatch(value["release_tag"]) is None
        or not isinstance(value["release_config_sha256"], str)
        or _SHA256.fullmatch(value["release_config_sha256"]) is None
    ):
        raise ValueError("Candidate transport context values are invalid.")
    canonical = _canonical(value)
    if raw != canonical:
        raise ValueError("Candidate transport context must use canonical JSON bytes.")
    return value, hashlib.sha256(canonical).hexdigest()


def _regular_file(path: Path, label: str) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(f"{label} must be a regular, unlinked file.")


def _prepare_output(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.resolve() != path.parent.absolute():
        raise ValueError(f"{label} parent must not traverse a symlink.")
    if path.exists() or path.is_symlink():
        raise ValueError(f"{label} must not already exist.")


def _atomic_output(path: Path):
    _prepare_output(path, "output")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.chmod(temporary, 0o600)
    return descriptor, Path(temporary)


def _load_sealer():
    script = Path(__file__).with_name("seal-candidate-artifact.py")
    spec = importlib.util.spec_from_file_location("channelwatch_candidate_sealer", script)
    if spec is None or spec.loader is None:
        raise ValueError("Candidate sealer could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _raw_public(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def generate_keypair(context: Path, public_output: Path, sealed_private_output: Path) -> None:
    value, context_sha256 = _load_context(context)
    private = X25519PrivateKey.generate()
    public = _raw_public(private.public_key())
    public_record = {
        "schema": SCHEMA,
        "algorithm": "X25519-HKDF-SHA256-AES-256-GCM",
        "context_sha256": context_sha256,
        "public_key": base64.b64encode(public).decode("ascii"),
    }
    private_record = {
        **public_record,
        "context": value,
        "private_key": base64.b64encode(_raw_private(private)).decode("ascii"),
    }
    public_descriptor, public_temporary = _atomic_output(public_output)
    with os.fdopen(public_descriptor, "wb") as output:
        output.write(_canonical(public_record))
        output.flush()
        os.fsync(output.fileno())
    os.replace(public_temporary, public_output)
    os.chmod(public_output, 0o644)
    descriptor, temporary = _atomic_output(sealed_private_output)
    os.close(descriptor)
    private_descriptor, private_name = tempfile.mkstemp(
        prefix=".channelwatch-transport-private-",
        suffix=".json",
        dir=sealed_private_output.parent,
    )
    private_path = Path(private_name)
    try:
        os.fchmod(private_descriptor, 0o600)
        with os.fdopen(private_descriptor, "wb") as private_output:
            private_output.write(_canonical(private_record))
            private_output.flush()
            os.fsync(private_output.fileno())
        _load_sealer().seal(private_path, temporary)
        os.replace(temporary, sealed_private_output)
        os.chmod(sealed_private_output, 0o600)
    finally:
        private_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def _public_record(path: Path, context_sha256: str) -> tuple[X25519PublicKey, str]:
    _regular_file(path, "--public-key")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {
        "schema", "algorithm", "context_sha256", "public_key"
    }:
        raise ValueError("Candidate transport public key fields are invalid.")
    if (
        value["schema"] != SCHEMA
        or value["algorithm"] != "X25519-HKDF-SHA256-AES-256-GCM"
        or value["context_sha256"] != context_sha256
    ):
        raise ValueError("Candidate transport public key context is invalid.")
    raw = base64.b64decode(value["public_key"], validate=True)
    return X25519PublicKey.from_public_bytes(raw), hashlib.sha256(raw).hexdigest()


def _derive_key(shared: bytes, salt: bytes, context_sha256: str, artifact_name: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=(
            "channelwatch-candidate-transport-v1:"
            f"{context_sha256}:{artifact_name}"
        ).encode("utf-8"),
    ).derive(shared)


def encrypt(
    source: Path,
    destination: Path,
    public_key_path: Path,
    context: Path,
    artifact_name: str,
) -> None:
    _regular_file(source, "--input")
    if _ARTIFACT.fullmatch(artifact_name) is None:
        raise ValueError("Candidate transport artifact name is invalid.")
    _, context_sha256 = _load_context(context)
    recipient, recipient_sha256 = _public_record(public_key_path, context_sha256)
    plaintext_size = source.stat().st_size
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    ephemeral = X25519PrivateKey.generate()
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    header = _canonical({
        "schema": SCHEMA,
        "algorithm": "X25519-HKDF-SHA256-AES-256-GCM",
        "artifact_name": artifact_name,
        "context_sha256": context_sha256,
        "recipient_public_key_sha256": recipient_sha256,
        "sender_public_key": base64.b64encode(_raw_public(ephemeral.public_key())).decode("ascii"),
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "plaintext_sha256": digest.hexdigest(),
        "plaintext_size": plaintext_size,
    })
    prefix = MAGIC + struct.pack(">I", len(header)) + header
    encryptor = Cipher(
        algorithms.AES(_derive_key(ephemeral.exchange(recipient), salt, context_sha256, artifact_name)),
        modes.GCM(nonce),
    ).encryptor()
    encryptor.authenticate_additional_data(prefix)
    descriptor, temporary = _atomic_output(destination)
    try:
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output:
            output.write(prefix)
            while chunk := input_file.read(CHUNK_BYTES):
                output.write(encryptor.update(chunk))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _private_key(path: Path, context: Path) -> tuple[X25519PrivateKey, str, str]:
    _regular_file(path, "--sealed-private-key")
    context_value, context_sha256 = _load_context(context)
    descriptor, name = tempfile.mkstemp(prefix="channelwatch-transport-private-", suffix=".json")
    os.close(descriptor)
    temporary = Path(name)
    try:
        _load_sealer().open_sealed(path, temporary)
        value = json.loads(temporary.read_text(encoding="utf-8"))
    finally:
        temporary.unlink(missing_ok=True)
    expected = {
        "schema", "algorithm", "context_sha256", "public_key", "context", "private_key"
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Candidate transport private key fields are invalid.")
    if (
        value["schema"] != SCHEMA
        or value["algorithm"] != "X25519-HKDF-SHA256-AES-256-GCM"
        or value["context_sha256"] != context_sha256
        or value["context"] != context_value
    ):
        raise ValueError("Candidate transport private key context is invalid.")
    raw_private = base64.b64decode(value["private_key"], validate=True)
    private = X25519PrivateKey.from_private_bytes(raw_private)
    raw_public = _raw_public(private.public_key())
    if base64.b64encode(raw_public).decode("ascii") != value["public_key"]:
        raise ValueError("Candidate transport keypair does not match.")
    return private, context_sha256, hashlib.sha256(raw_public).hexdigest()


def decrypt(
    source: Path,
    destination: Path,
    sealed_private_key: Path,
    context: Path,
    artifact_name: str,
) -> None:
    _regular_file(source, "--input")
    if _ARTIFACT.fullmatch(artifact_name) is None:
        raise ValueError("Candidate transport artifact name is invalid.")
    private, context_sha256, recipient_sha256 = _private_key(sealed_private_key, context)
    size = source.stat().st_size
    minimum = len(MAGIC) + 4 + TAG_BYTES
    if size < minimum:
        raise ValueError("Candidate transport artifact is truncated.")
    descriptor, temporary = _atomic_output(destination)
    try:
        with source.open("rb") as input_file:
            if input_file.read(len(MAGIC)) != MAGIC:
                raise ValueError("Candidate transport artifact header is invalid.")
            encoded_length = input_file.read(4)
            if len(encoded_length) != 4:
                raise ValueError("Candidate transport artifact is truncated.")
            header_length = struct.unpack(">I", encoded_length)[0]
            if not (1 <= header_length <= MAX_HEADER_BYTES):
                raise ValueError("Candidate transport header length is invalid.")
            header_bytes = input_file.read(header_length)
            header = json.loads(header_bytes.decode("utf-8"))
            if header_bytes != _canonical(header):
                raise ValueError("Candidate transport header is not canonical.")
            required = {
                "schema", "algorithm", "artifact_name", "context_sha256",
                "recipient_public_key_sha256", "sender_public_key", "salt", "nonce",
                "plaintext_sha256", "plaintext_size",
            }
            if not isinstance(header, dict) or set(header) != required:
                raise ValueError("Candidate transport header fields are invalid.")
            if (
                header["schema"] != SCHEMA
                or header["algorithm"] != "X25519-HKDF-SHA256-AES-256-GCM"
                or header["artifact_name"] != artifact_name
                or header["context_sha256"] != context_sha256
                or header["recipient_public_key_sha256"] != recipient_sha256
                or not isinstance(header["plaintext_size"], int)
                or header["plaintext_size"] < 0
                or not isinstance(header["plaintext_sha256"], str)
                or _SHA256.fullmatch(header["plaintext_sha256"]) is None
            ):
                raise ValueError("Candidate transport binding is invalid.")
            sender = X25519PublicKey.from_public_bytes(
                base64.b64decode(header["sender_public_key"], validate=True)
            )
            salt = base64.b64decode(header["salt"], validate=True)
            nonce = base64.b64decode(header["nonce"], validate=True)
            if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES:
                raise ValueError("Candidate transport parameters are invalid.")
            prefix = MAGIC + encoded_length + header_bytes
            ciphertext_bytes = size - len(prefix) - TAG_BYTES
            if ciphertext_bytes != header["plaintext_size"]:
                raise ValueError("Candidate transport artifact size is invalid.")
            input_file.seek(size - TAG_BYTES)
            tag = input_file.read(TAG_BYTES)
            input_file.seek(len(prefix))
            decryptor = Cipher(
                algorithms.AES(
                    _derive_key(private.exchange(sender), salt, context_sha256, artifact_name)
                ),
                modes.GCM(nonce, tag),
            ).decryptor()
            decryptor.authenticate_additional_data(prefix)
            remaining = ciphertext_bytes
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "wb") as output:
                while remaining:
                    chunk = input_file.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("Candidate transport artifact is truncated.")
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    digest.update(plaintext)
                    output.write(plaintext)
                final = decryptor.finalize()
                digest.update(final)
                output.write(final)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != header["plaintext_sha256"]:
                raise ValueError("Candidate transport plaintext digest is invalid.")
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate-keypair")
    generate.add_argument("--context", required=True, type=Path)
    generate.add_argument("--public-key-out", required=True, type=Path)
    generate.add_argument("--sealed-private-key-out", required=True, type=Path)
    encrypt_parser = subparsers.add_parser("encrypt")
    encrypt_parser.add_argument("--context", required=True, type=Path)
    encrypt_parser.add_argument("--public-key", required=True, type=Path)
    encrypt_parser.add_argument("--input", required=True, type=Path)
    encrypt_parser.add_argument("--output", required=True, type=Path)
    encrypt_parser.add_argument("--artifact-name", required=True)
    decrypt_parser = subparsers.add_parser("decrypt")
    decrypt_parser.add_argument("--context", required=True, type=Path)
    decrypt_parser.add_argument("--sealed-private-key", required=True, type=Path)
    decrypt_parser.add_argument("--input", required=True, type=Path)
    decrypt_parser.add_argument("--output", required=True, type=Path)
    decrypt_parser.add_argument("--artifact-name", required=True)
    args = parser.parse_args()
    try:
        if args.command == "generate-keypair":
            generate_keypair(args.context, args.public_key_out, args.sealed_private_key_out)
        elif args.command == "encrypt":
            encrypt(args.input, args.output, args.public_key, args.context, args.artifact_name)
        else:
            decrypt(
                args.input,
                args.output,
                args.sealed_private_key,
                args.context,
                args.artifact_name,
            )
    except Exception as exc:
        parser.exit(1, f"candidate transport failed: {exc.__class__.__name__}\n")
    print(f"Candidate transport {args.command} completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
