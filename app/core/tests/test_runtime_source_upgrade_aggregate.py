import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE_CATALOG_162 = "37e91da9638adea1b9a758642c1f97e83888c4da64b1877cbc7dbd494376c4c9"

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/aggregate-runtime-source-upgrades.py"
CONFIG = ROOT / "scripts/release/release-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("runtime_source_aggregate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chained_receipt(tmp_path: Path, architecture: str, *, source_version="1.6.0", target_version="1.6.1") -> tuple[Path, dict]:
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
        log_path = tmp_path / f"{architecture}-{source_version}-{name}.log"
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
        "source_application_version": source_version,
        "source_commit": (
            "b48057184752c912e4ae9b3a474374704d896073" if source_version == "1.6.0"
            else "f2e2943097451480bb11ec926bafb1be5ca5fbdc" if source_version == "1.6.1"
            else "453deb679c6dfd67ff98437d3f96b76183cff3cb"
        ),
        "source_catalog_sha256": (
            "f20578206578901c9a28486c28e2a6f8c2e7974bad4ec314d55a3bcba53e1b39" if source_version == "1.6.0"
            else "fd8cf8110c997a5259eb9f19023498dde4bc148e3785352abc1b7912cbfa9c34" if source_version == "1.6.1"
            else SOURCE_CATALOG_162
        ),
        "target_application_version": target_version,
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
                "target_apply_restart", f"rollback_to_{source_version}", "reapply",
                "same_container", "same_image", "image_packages_unchanged",
                "persistent_state",
            )
        },
        "logs": logs,
        "cleanup": {"status": "passed", "failures": []},
    }
    path = tmp_path / f"runtime-capability-{architecture}-{source_version}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


@pytest.mark.parametrize("target_version", ["1.6.1", "1.6.2", "1.6.3"])
@pytest.mark.parametrize("mutation", [None, "missing", "duplicate", "extra", "config"])
def test_exact_source_receipt_matrix_passes(tmp_path, monkeypatch, target_version, mutation):
    module = load_module()
    catalog = tmp_path / "catalog.json"
    catalog.write_text("candidate catalog\n", encoding="utf-8")
    catalog_sha = hashlib.sha256(catalog.read_bytes()).hexdigest()
    receipts = []
    for architecture in ("amd64", "arm64"):
        direct = tmp_path / f"runtime-capability-{architecture}-1.5.3.json"
        direct.write_text(f"{architecture} direct\n", encoding="utf-8")
        receipts.append(f"1.5.3={architecture}={direct}")
        sources = {"1.6.1": ["1.6.0"], "1.6.2": ["1.6.0", "1.6.1"], "1.6.3": ["1.6.0", "1.6.1", "1.6.2"]}[target_version]
        for source in sources:
            chained, value = chained_receipt(tmp_path, architecture, source_version=source, target_version=target_version)
            value["target_catalog_sha256"] = catalog_sha
            chained.write_text(json.dumps(value), encoding="utf-8")
            receipts.append(f"{source}={architecture}={chained}")

    config = tmp_path / "release-config.json"
    configured = json.loads(CONFIG.read_text())
    configured.update(version=target_version, compatible_source_application_versions=["1.5.3", *sources])
    if mutation == "missing":
        receipts.pop()
    elif mutation == "duplicate":
        receipts.append(receipts[-1])
    elif mutation == "extra":
        receipts.append("1.6.3=amd64=unapproved.json")
    elif mutation == "config":
        configured["compatible_source_application_versions"].pop()
    config.write_text(json.dumps(configured), encoding="utf-8")

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
        "--release-config", str(config),
        "--target-commit", "f" * 40,
        "--output", str(output),
    ]
    monkeypatch.setattr("sys.argv", argv)
    if mutation is not None:
        with pytest.raises(ValueError, match="sources|matrix|binding"):
            module.main()
        assert not output.exists()
        return
    assert module.main() == 0
    result = json.loads(output.read_text())
    assert result["native_receipt_count"] == 2 * (1 + len(sources))
    assert result["source_application_versions"] == ["1.5.3", *sources]
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
    assert "rollback to v{source_version}" in text
    assert 'TARGET_SOURCES = {' in text


@pytest.mark.parametrize("field", ["source_commit", "source_catalog_sha256", "source_application_version"])
def test_v162_chain_rejects_unbound_v161_source(tmp_path, field):
    module = load_module()
    path, value = chained_receipt(tmp_path, "amd64", source_version="1.6.1", target_version="1.6.2")
    value["target_catalog_sha256"] = "9" * 64
    value[field] = "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="immutable v1.6.1"):
        module.validate_chained_receipt(
            path, architecture="amd64", source_version="1.6.1",
            target_version="1.6.2", target_commit="f" * 40,
            target_catalog_sha256="9" * 64,
        )


def test_v162_chain_requires_rollback_to_its_v161_source(tmp_path):
    module = load_module()
    path, value = chained_receipt(tmp_path, "amd64", source_version="1.6.1", target_version="1.6.2")
    value["target_catalog_sha256"] = "9" * 64
    value["checks"]["rollback_to_1.6.0"] = value["checks"].pop("rollback_to_1.6.1")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="checks"):
        module.validate_chained_receipt(
            path, architecture="amd64", source_version="1.6.1",
            target_version="1.6.2", target_commit="f" * 40,
            target_catalog_sha256="9" * 64,
        )


@pytest.mark.parametrize("target_version,source_version,accepted", [
    ("1.6.2", "1.6.1", True),
    ("1.6.2", "1.6.0", True),
    ("1.6.1", "1.6.0", True),
    ("1.6.1", "1.6.1", False),
    ("1.6.3", "1.6.0", True),
    ("1.6.3", "1.6.1", True),
    ("1.6.3", "1.6.2", True),
    ("1.6.2", "1.6.2", False),
    ("1.6.4", "1.6.2", False),
])
def test_source_verifier_accepts_only_bounded_source_target_pairs(tmp_path, monkeypatch, target_version, source_version, accepted):
    spec = importlib.util.spec_from_file_location("runtime_source_verifier", ROOT / "scripts/release/verify-runtime-source-upgrade.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "receipt.json"
    arguments = ["verify-runtime-source-upgrade.py", "--source-version", source_version]
    for flag in ("source-catalog", "source-amd64-bundle", "source-arm64-bundle", "target-catalog", "target-amd64-bundle", "target-arm64-bundle"):
        arguments.extend([f"--{flag}", str(tmp_path / flag)])
    arguments.extend([
        "--target-version", target_version, "--target-commit", "f" * 40,
        "--image-index-digest", "sha256:" + "a" * 64, "--architecture", "amd64",
        "--evidence-json", str(output),
    ])
    called = []
    monkeypatch.setattr(module, "qualify", lambda args: called.append((args.target_version, args.source_version)) or {"status": "passed"})
    monkeypatch.setattr("sys.argv", arguments)
    if accepted:
        assert module.main() == 0
        assert called == [(target_version, source_version)]
        assert output.is_file()
    else:
        with pytest.raises(SystemExit, match="bounded|requires"):
            module.main()
        assert called == []
        assert not output.exists()
