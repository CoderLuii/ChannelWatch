from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from core.tests.test_release_capability_policy import SOURCE_COMMIT, _config, _evidence, _signed_catalog


def _module():
    path = Path(__file__).resolve().parents[3] / "scripts/release/aggregate-runtime-capability.py"
    spec = importlib.util.spec_from_file_location("runtime_capability_aggregate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _receipts(tmp_path):
    catalog, _keys, descriptors = _signed_catalog(tmp_path)
    aggregate = _evidence(catalog, descriptors)
    header = {key: value for key, value in aggregate.items() if key not in {"architectures", "native_logs_sha256"}}
    return aggregate, {
        name: {**copy.deepcopy(header), "architecture": copy.deepcopy(value), "cleanup": {"status": "passed", "failures": []}, "logs_sha256": aggregate["native_logs_sha256"][name]}
        for name, value in aggregate["architectures"].items()
    }


def test_aggregate_retains_exact_native_receipts(tmp_path):
    expected, receipts = _receipts(tmp_path)
    assert _module().aggregate(receipts) == expected


@pytest.mark.parametrize("mutation", [
    lambda receipts: receipts.pop("arm64"),
    lambda receipts: receipts["arm64"].update(status="failed"),
    lambda receipts: receipts["arm64"].update(signed_catalog_sha256="0" * 64),
    lambda receipts: receipts["arm64"]["architecture"].update(architecture="amd64"),
    lambda receipts: receipts["amd64"].update(schema=1),
    lambda receipts: receipts["amd64"]["logs_sha256"].pop("feed"),
    lambda receipts: receipts["amd64"]["logs_sha256"].pop("app"),
    lambda receipts: receipts["amd64"]["logs_sha256"].update(extra="a" * 64),
    lambda receipts: receipts["arm64"].update(cleanup={"status": "failed", "failures": ["container"]}),
])
def test_aggregate_rejects_missing_failed_or_mismatched_proof(tmp_path, mutation):
    _expected, receipts = _receipts(tmp_path)
    mutation(receipts)
    with pytest.raises(ValueError):
        _module().aggregate(receipts)


@pytest.mark.parametrize("tamper_log", [False, True])
def test_cli_verifies_signed_catalog_and_actual_logs_before_publication(tmp_path, monkeypatch, tamper_log):
    from core import update_center

    catalog, keys, descriptors = _signed_catalog(tmp_path)
    expected = _evidence(catalog, descriptors)
    receipt_paths = {}
    for name in ("amd64", "arm64"):
        path = tmp_path / (name + ".json")
        hashes = {}
        for component in ("app", "feed"):
            data = (name + " " + component + " verification\n").encode()
            path.with_name(path.stem + "-" + component + ".log").write_bytes(data)
            hashes[component] = hashlib.sha256(data).hexdigest()
        receipt = {key: value for key, value in expected.items() if key not in {"architectures", "native_logs_sha256"}}
        receipt.update(architecture=expected["architectures"][name], logs_sha256=hashes,
                       cleanup={"status": "passed", "failures": []})
        path.write_text(json.dumps(receipt))
        receipt_paths[name] = path
    if tamper_log:
        (tmp_path / "arm64-app.log").write_text("changed log\n")
    config = tmp_path / "release-config.json"
    config.write_text(json.dumps(_config()))
    output = tmp_path / "aggregate.json"
    monkeypatch.setattr(update_center, "UPDATE_PUBLIC_KEYS", keys)
    monkeypatch.setattr("sys.argv", ["aggregate-runtime-capability.py", "--amd64-evidence", str(receipt_paths["amd64"]),
                                    "--arm64-evidence", str(receipt_paths["arm64"]), "--catalog", str(catalog),
                                    "--release-config", str(config), "--source-commit", SOURCE_COMMIT, "--output", str(output)])
    if tamper_log:
        with pytest.raises(ValueError, match="log hash"):
            _module().main()
        assert not output.exists()
    else:
        assert _module().main() == 0
        actual = json.loads(output.read_text())
        assert actual["architectures"] == expected["architectures"]
