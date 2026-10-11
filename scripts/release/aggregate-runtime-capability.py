#!/usr/bin/env python3
"""Bind two native qualification receipts to the signed release catalog."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


HEADER_FIELDS = (
    "schema", "status", "target_application_version", "source_commit",
    "signed_catalog_sha256", "bootstrap",
)


def aggregate(receipts: dict[str, dict]) -> dict:
    if set(receipts) != {"amd64", "arm64"}:
        raise ValueError("Both native architecture receipts are required.")
    first = receipts["amd64"]
    if first.get("schema") != 2 or first.get("status") != "passed":
        raise ValueError("Native qualification did not pass with schema 2.")
    header = {key: first[key] for key in HEADER_FIELDS}
    architectures = {}
    logs = {}
    for name, receipt in receipts.items():
        if any(receipt.get(key) != value for key, value in header.items()):
            raise ValueError("Native receipts describe different release artifacts.")
        if receipt.get("cleanup") != {"status": "passed", "failures": []}:
            raise ValueError("Native qualification cleanup did not pass.")
        architecture = receipt.get("architecture")
        if not isinstance(architecture, dict) or architecture.get("architecture") != name or architecture.get("status") != "passed":
            raise ValueError("Native architecture receipt is missing or mismatched.")
        architectures[name] = architecture
        hashes = receipt.get("logs_sha256")
        if not isinstance(hashes, dict) or set(hashes) != {"app", "feed"}:
            raise ValueError("Native qualification logs are missing.")
        logs[name] = hashes
    return {**header, "architectures": architectures, "native_logs_sha256": logs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amd64-evidence", type=Path, required=True)
    parser.add_argument("--arm64-evidence", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--release-config", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt_paths = {
        "amd64": args.amd64_evidence, "arm64": args.arm64_evidence,
    }
    receipts = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in receipt_paths.items()}
    result = aggregate(receipts)
    for name, path in receipt_paths.items():
        for component, expected_hash in result["native_logs_sha256"][name].items():
            if component not in {"app", "feed"}:
                raise ValueError("Native qualification log component is invalid.")
            log = path.with_name(path.stem + "-" + component + ".log")
            if log.is_symlink() or not log.is_file() or not 0 < log.stat().st_size <= 64 * 1024 * 1024:
                raise ValueError("Native qualification log is missing or invalid.")
            if hashlib.sha256(log.read_bytes()).hexdigest() != expected_hash:
                raise ValueError("Native qualification log hash does not match its receipt.")
    spec = importlib.util.spec_from_file_location("release_impact", Path(__file__).with_name("classify-release-impact.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("Release classifier could not be loaded.")
    classifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(classifier)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-capability-", dir=args.output.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(result, output, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if not classifier._read_protocol_four_runtime_evidence(
            config=json.loads(args.release_config.read_text(encoding="utf-8")),
            catalog_path=args.catalog, evidence_path=temporary_path,
            expected_source_commit=args.source_commit, public_keys=None,
        ):
            raise ValueError("Native evidence is incompatible with the signed release contract.")
        os.replace(temporary_path, args.output)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(json.dumps({"status": "passed", "architectures": sorted(result["architectures"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
