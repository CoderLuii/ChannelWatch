import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/aggregate-runtime-source-upgrades.py"
CONFIG = ROOT / "scripts/release/release-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("runtime_source_aggregate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chained_receipt(tmp_path: Path, architecture: str) -> tuple[Path, dict]:
    source_generation = "a" * 64
    target_generation = "b" * 64

    def selected(generation: str) -> dict:
        return {
            "status_worker": {
                "verified_generation": {
                    "runtime_generation_id": generation,
                    "runtime_descriptor_sha256": generation,
                }
            }
        }

    logs = {}
    for name in ("app", "feed"):
        log_path = tmp_path / f"{architecture}-{name}.log"
        log_path.write_text(f"{architecture}-{name}\n", encoding="utf-8")
        logs[name] = {
            "file": log_path.name,
            "sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
            "size": log_path.stat().st_size,
        }
    identity = {
        "container_id": "c" * 64,
        "image_id": "sha256:" + "d" * 64,
        "manifest_digest": "sha256:" + "e" * 64,
    }
    value = {
        "schema": 1,
        "status": "passed",
        "evidence_kind": "native",
        "architecture": architecture,
        "source_application_version": "1.6.0",
        "source_commit": "b48057184752c912e4ae9b3a474374704d896073",
        "source_catalog_sha256": "f20578206578901c9a28486c28e2a6f8c2e7974bad4ec314d55a3bcba53e1b39",
        "target_application_version": "1.6.1",
        "target_commit": "f" * 40,
        "target_catalog_sha256": "",
        "source_runtime_generation_id": source_generation,
        "target_runtime_generation_id": target_generation,
        "source_restart": selected(source_generation),
        "target_restart": selected(target_generation),
        "rollback_status": selected(source_generation),
        "reapply_status": selected(target_generation),
        "identity": {"before": identity, "after": copy.deepcopy(identity)},
        "image_inventory_sha256": "8" * 64,
        "checks": {
            name: True for name in (
                "signed_source", "signed_target", "source_apply_restart",
                "target_apply_restart", "rollback_to_1.6.0", "reapply",
                "same_container", "same_image", "image_packages_unchanged",
                "persistent_state",
            )
        },
        "logs": logs,
        "cleanup": {"status": "passed", "failures": []},
    }
    path = tmp_path / f"runtime-capability-{architecture}-1.6.0.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


def test_exact_four_receipt_source_matrix_passes(tmp_path, monkeypatch):
    module = load_module()
    catalog = tmp_path / "catalog.json"
    catalog.write_text("candidate catalog\n", encoding="utf-8")
    catalog_sha = hashlib.sha256(catalog.read_bytes()).hexdigest()
    receipts = []
    for architecture in ("amd64", "arm64"):
        direct = tmp_path / f"runtime-capability-{architecture}-1.5.3.json"
        direct.write_text(f"{architecture} direct\n", encoding="utf-8")
        receipts.append(f"1.5.3={architecture}={direct}")
        chained, value = chained_receipt(tmp_path, architecture)
        value["target_catalog_sha256"] = catalog_sha
        chained.write_text(json.dumps(value), encoding="utf-8")
        receipts.append(f"1.6.0={architecture}={chained}")

    validated = []
    monkeypatch.setattr(
        module,
        "_load_runtime_aggregate",
        lambda: type(
            "RuntimeAggregate",
            (),
            {"validate_native_receipt_file": staticmethod(
                lambda path, **kwargs: validated.append((path.name, kwargs["architecture"]))
            )},
        ),
    )
    output = tmp_path / "aggregate.json"
    receipt_args = [part for item in receipts for part in ("--receipt", item)]
    argv = [
        "aggregate-runtime-source-upgrades.py",
        *receipt_args,
        "--target-catalog", str(catalog),
        "--release-config", str(CONFIG),
        "--target-commit", "f" * 40,
        "--output", str(output),
    ]
    monkeypatch.setattr("sys.argv", argv)
    assert module.main() == 0
    result = json.loads(output.read_text())
    assert result["native_receipt_count"] == 4
    assert result["source_application_versions"] == ["1.5.3", "1.6.0"]
    assert validated == [
        ("runtime-capability-amd64-1.5.3.json", "amd64"),
        ("runtime-capability-arm64-1.5.3.json", "arm64"),
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__("source_catalog_sha256", "0" * 64), "immutable v1.6.0"),
        (lambda value: value["identity"].pop("before"), "identity"),
        (lambda value: value.__setitem__("identity", {"before": {}, "after": {}}), "identity"),
        (lambda value: value.__setitem__("identity", {"before": None, "after": None}), "identity"),
        (lambda value: value["identity"]["after"].__setitem__("image_id", "d" * 64), "identity"),
        (lambda value: value.__setitem__("image_inventory_sha256", "invalid"), "inventory"),
        (lambda value: value["checks"].__setitem__("rollback_to_1.6.0", False), "checks"),
        (lambda value: value["rollback_status"]["status_worker"]["verified_generation"].__setitem__("runtime_generation_id", "b" * 64), "rollback_status"),
        (lambda value: value["logs"]["app"].__setitem__("sha256", "0" * 64), "Log artifact"),
    ],
)
def test_chained_receipt_rejects_tampered_evidence(tmp_path, mutation, message):
    module = load_module()
    path, value = chained_receipt(tmp_path, "amd64")
    value["target_catalog_sha256"] = "9" * 64
    mutation(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        module.validate_chained_receipt(
            path,
            architecture="amd64",
            target_version="1.6.1",
            target_commit="f" * 40,
            target_catalog_sha256="9" * 64,
        )


def test_source_verifier_is_bounded_to_immutable_v160_assets():
    text = (ROOT / "scripts/release/verify-runtime-source-upgrade.py").read_text()
    assert 'SOURCE_VERSION = "1.6.0"' in text
    assert 'SOURCE_COMMIT = "b48057184752c912e4ae9b3a474374704d896073"' in text
    assert "rollback to v1.6.0" in text
    assert 'args.target_version != "1.6.1"' in text
