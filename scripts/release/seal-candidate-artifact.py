#!/usr/bin/env python3
"""Seal or open a nonpublishing release-candidate artifact.

The repository is public, so production-signed bundles and exact OCI archives
must never be uploaded as plaintext Actions artifacts.  This format uses a
domain-separated key derived from the production signing secret and streaming
AES-256-GCM authentication.  The secret is supplied only through the protected
workflow environment and is never written or printed.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"CHANNELWATCH-CANDIDATE-SEALED-V1\n"
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
KEY_ENV = "CHANNELWATCH_UPDATE_SIGNING_KEY"


def _derived_key() -> bytes:
    source = os.environ.get(KEY_ENV, "").strip().encode("utf-8")
    if not source:
        raise ValueError(f"{KEY_ENV} is required.")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"channelwatch-candidate-artifact-v1",
        info=b"public-repository-actions-artifact-confidentiality",
    ).derive(source)


def _atomic_output(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.chmod(temporary, 0o600)
    return descriptor, Path(temporary)


def seal(source: Path, destination: Path) -> None:
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(_derived_key()), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC + nonce)
    descriptor, temporary = _atomic_output(destination)
    try:
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output:
            output.write(MAGIC)
            output.write(nonce)
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


def open_sealed(source: Path, destination: Path) -> None:
    size = source.stat().st_size
    minimum = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    if size < minimum:
        raise ValueError("Sealed candidate artifact is truncated.")
    descriptor, temporary = _atomic_output(destination)
    try:
        with source.open("rb") as input_file:
            if input_file.read(len(MAGIC)) != MAGIC:
                raise ValueError("Sealed candidate artifact header is invalid.")
            nonce = input_file.read(NONCE_BYTES)
            ciphertext_bytes = size - len(MAGIC) - NONCE_BYTES - TAG_BYTES
            input_file.seek(size - TAG_BYTES)
            tag = input_file.read(TAG_BYTES)
            input_file.seek(len(MAGIC) + NONCE_BYTES)
            decryptor = Cipher(
                algorithms.AES(_derived_key()), modes.GCM(nonce, tag)
            ).decryptor()
            decryptor.authenticate_additional_data(MAGIC + nonce)
            remaining = ciphertext_bytes
            with os.fdopen(descriptor, "wb") as output:
                while remaining:
                    chunk = input_file.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("Sealed candidate artifact is truncated.")
                    remaining -= len(chunk)
                    output.write(decryptor.update(chunk))
                output.write(decryptor.finalize())
                output.flush()
                os.fsync(output.fileno())
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
    parser.add_argument("mode", choices=("seal", "open"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.input.is_file() or args.input.is_symlink():
        parser.error("--input must be a regular, non-symlink file.")
    try:
        if args.mode == "seal":
            seal(args.input, args.output)
        else:
            open_sealed(args.input, args.output)
    except Exception as exc:
        parser.exit(
            1, f"candidate artifact {args.mode} failed: {exc.__class__.__name__}\n"
        )
    print(f"Candidate artifact {args.mode} completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
