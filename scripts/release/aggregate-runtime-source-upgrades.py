#!/usr/bin/env python3
"""Aggregate the exact native protocol-4 source-version qualification matrix."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_AGGREGATE = ROOT / "scripts/release/aggregate-runtime-capability.py"
SOURCE_CONTRACTS = {
    "1.6.1": ["1.5.3", "1.6.0"],
    "1.6.2": ["1.5.3", "1.6.0", "1.6.1"],
    "1.6.3": ["1.5.3", "1.6.0", "1.6.1", "1.6.2"],
    "1.6.4": ["1.5.3", "1.6.0", "1.6.1", "1.6.2", "1.6.3"],
}
SOURCE_IDENTITIES = {
    "1.6.3": (
        "5318c64df0bf059a107c98ee127145b2b1007cb1",
        "ced805957f5414f73678643c0458aae4f2c1a0c6ecaf2c6c52a7e1ef922f3454",
    ),
    "1.6.2": (
        "453deb679c6dfd67ff98437d3f96b76183cff3cb",
        "37e91da9638adea1b9a758642c1f97e83888c4da64b1877cbc7dbd494376c4c9",
    ),
    "1.6.0": (
        "b48057184752c912e4ae9b3a474374704d896073",
        "f20578206578901c9a28486c28e2a6f8c2e7974bad4ec314d55a3bcba53e1b39",
    ),
    "1.6.1": (
        "f2e2943097451480bb11ec926bafb1be5ca5fbdc",
        "fd8cf8110c997a5259eb9f19023498dde4bc148e3785352abc1b7912cbfa9c34",
    ),
}
REQUIRED_CHAIN_CHECKS = {
    "signed_source", "signed_target", "source_apply_restart",
    "target_apply_restart", "rollback_to_1.6.0", "reapply",
    "same_container", "same_image", "image_packages_unchanged",
    "persistent_state",
}
HEX = re.compile(r"[0-9a-f]{64}")


def _load_runtime_aggregate():
    spec = importlib.util.spec_from_file_location(
        "runtime_capability_aggregate", RUNTIME_AGGREGATE
    )
    if spec is None or spec.loader is None:
        raise ValueError("Could not load the runtime capability aggregator.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def validate_chained_receipt(
    path: Path,
    *,
    architecture: str,
    target_version: str,
    target_commit: str,
    target_catalog_sha256: str,
    source_version: str = "1.6.0",
) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(
        value.get("schema") == 1
        and value.get("status") == "passed"
        and value.get("evidence_kind") == "native",
        "Chained protocol-4 receipt did not pass natively.",
    )
    require(value.get("architecture") == architecture, "Receipt architecture is unbound.")
    source_identity = SOURCE_IDENTITIES.get(source_version)
    require(
        source_identity is not None
        and source_version in SOURCE_CONTRACTS.get(target_version, [])
        and value.get("source_application_version") == source_version
        and value.get("source_commit") == source_identity[0]
        and value.get("source_catalog_sha256") == source_identity[1],
        f"Chained receipt is not bound to the immutable v{source_version} source.",
    )
    require(
        value.get("target_application_version") == target_version
        and value.get("target_commit") == target_commit
        and value.get("target_catalog_sha256") == target_catalog_sha256,
        "Chained receipt is not bound to the candidate target.",
    )
    source_generation = str(value.get("source_runtime_generation_id") or "")
    target_generation = str(value.get("target_runtime_generation_id") or "")
    require(
        HEX.fullmatch(source_generation) is not None
        and HEX.fullmatch(target_generation) is not None
        and source_generation != target_generation,
        "Chained receipt runtime generations are unbound.",
    )
    identities = value.get("identity")
    before_identity = identities.get("before") if isinstance(identities, dict) else None
    after_identity = identities.get("after") if isinstance(identities, dict) else None
    require(
        isinstance(identities, dict)
        and set(identities) == {"before", "after"}
        and isinstance(before_identity, dict)
        and isinstance(after_identity, dict)
        and set(before_identity) == {"container_id", "image_id", "manifest_digest"}
        and set(after_identity) == {"container_id", "image_id", "manifest_digest"}
        and HEX.fullmatch(str(before_identity.get("container_id") or "")) is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(before_identity.get("image_id") or "")) is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(before_identity.get("manifest_digest") or "")) is not None
        and before_identity == after_identity,
        "Chained receipt changed the container or image identity.",
    )
    require(
        HEX.fullmatch(str(value.get("image_inventory_sha256") or "")) is not None,
        "Chained receipt image inventory is unbound.",
    )
    checks = value.get("checks")
    required_checks = (REQUIRED_CHAIN_CHECKS - {"rollback_to_1.6.0"}) | {f"rollback_to_{source_version}"}
    require(
        isinstance(checks, dict)
        and set(checks) == required_checks
        and all(result is True for result in checks.values()),
        "Chained protocol-4 checks are incomplete.",
    )
    for name, generation in (
        ("source_restart", source_generation),
        ("target_restart", target_generation),
        ("rollback_status", source_generation),
        ("reapply_status", target_generation),
    ):
        status = value.get(name)
        verified = (
            status.get("status_worker", {}).get("verified_generation")
            if isinstance(status, dict)
            else None
        )
        require(
            isinstance(status, dict)
            and isinstance(verified, dict)
            and verified.get("runtime_generation_id") == generation
            and verified.get("runtime_descriptor_sha256") == generation,
            f"{name} does not prove a selected verified runtime generation.",
        )
    logs = value.get("logs")
    require(isinstance(logs, dict) and set(logs) == {"app", "feed"}, "Logs are incomplete.")
    for descriptor in logs.values():
        require(
            isinstance(descriptor, dict)
            and set(descriptor) == {"file", "sha256", "size"}
            and HEX.fullmatch(str(descriptor.get("sha256") or "")) is not None,
            "Log digest is invalid.",
        )
        log_path = path.parent / str(descriptor.get("file") or "")
        require(
            type(descriptor.get("size")) is int
            and descriptor["size"] > 0
            and log_path.is_file()
            and log_path.stat().st_size == descriptor["size"]
            and sha256_file(log_path) == descriptor["sha256"],
            "Log artifact is missing or changed.",
        )
    require(
        value.get("cleanup") == {"status": "passed", "failures": []},
        "Native Docker cleanup did not pass.",
    )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--target-catalog", type=Path, required=True)
    parser.add_argument("--release-config", type=Path, required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    release_config = json.loads(args.release_config.read_text(encoding="utf-8"))
    target_version = str(release_config.get("version") or "")
    sources = SOURCE_CONTRACTS.get(target_version)
    require(sources is not None, "Source matrix is bounded to v1.6.1, v1.6.2, v1.6.3 and v1.6.4.")
    require(
        release_config.get("compatible_source_application_versions")
        == sources,
        "Canonical protocol-4 sources are not exact.",
    )
    target_catalog_sha256 = sha256_file(args.target_catalog)
    expected_keys = {
        (source, architecture)
        for source in sources
        for architecture in ("amd64", "arm64")
    }
    mapped: dict[tuple[str, str], Path] = {}
    for item in args.receipt:
        parts = item.split("=", 2)
        require(len(parts) == 3, "Receipt binding must be source=architecture=path.")
        source, architecture, raw_path = parts
        key = (source, architecture)
        require(key in expected_keys and key not in mapped, "Receipt binding is invalid or duplicated.")
        mapped[key] = Path(raw_path)
    require(set(mapped) == expected_keys, "Protocol-4 source receipt matrix is incomplete or has extras.")
    runtime_aggregate = _load_runtime_aggregate()
    summaries = []
    for source, architecture in sorted(expected_keys):
        path = mapped[(source, architecture)]
        require(path.is_file(), f"Missing native receipt: {path}")
        if source == "1.5.3":
            runtime_aggregate.validate_native_receipt_file(
                path,
                architecture=architecture,
                catalog_path=args.target_catalog,
                release_config=release_config,
                source_commit=args.target_commit,
            )
        else:
            validate_chained_receipt(
                path,
                architecture=architecture,
                target_version=target_version,
                target_commit=args.target_commit,
                target_catalog_sha256=target_catalog_sha256,
                source_version=source,
            )
        summaries.append({
            "source_application_version": source,
            "architecture": architecture,
            "receipt_sha256": sha256_file(path),
        })
    result = {
        "schema": 1,
        "status": "passed",
        "evidence_kind": "native",
        "target_application_version": target_version,
        "target_commit": args.target_commit,
        "target_catalog_sha256": target_catalog_sha256,
        "source_application_versions": sources,
        "architectures": ["amd64", "arm64"],
        "native_receipt_count": len(expected_keys),
        "qualifications": summaries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
