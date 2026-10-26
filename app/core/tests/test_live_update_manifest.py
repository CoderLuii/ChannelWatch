from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from core.tests.test_runtime_release_assets import (
    _release_160,
    _release_161,
    _signed_schema_two,
)
from core.tests.test_update_manager import _bundle, _key_pair, _manifest


ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_case(tmp_path, monkeypatch, version: str, *, production_keys: bool = False):
    live = _load("live_manifest_main", "scripts/release/verify-live-update-manifest.py")
    verifier = live.load_asset_verifier()
    builder = _load("live_manifest_projection", "scripts/release/build-update-bundle.py")
    private, public = _key_pair()
    if not production_keys:
        monkeypatch.setattr(verifier, "UPDATE_PUBLIC_KEYS", public)
    monkeypatch.setattr(live, "load_asset_verifier", lambda: verifier)
    monkeypatch.setattr(live, "VERIFIER", tmp_path / "verify-update-assets.py")

    sources = ["1.4.0", "1.4.1", "1.4.5"]
    if version == "1.6.1":
        sources.append("1.6.0")
    config = {"version": version, "legacy_app_update_sources": sources}
    config_path = tmp_path / "release-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    bridge_bundle = _bundle("0.9.18")
    bridge = json.loads(_manifest(private, bridge_bundle, "0.9.18"))
    bridge["payload"].update(
        delivery_mode="app_update_with_image_refresh",
        recommended_image_version="0.9.18",
    )
    bridge["signature"]["value"] = base64.b64encode(
        private.sign(verifier.canonical_payload_bytes(bridge["payload"]))
    ).decode("ascii")

    bundle = _bundle(version)
    digest = hashlib.sha256(bundle).digest()
    canonical = _release_160() if version == "1.6.0" else _release_161()
    canonical.update(
        delivery_mode="app_update" if version == "1.6.0" else "app_update_with_image_refresh",
        image_refresh_recommended=version == "1.6.1",
        bundle_sha256=digest.hex(),
        bundle_signature=base64.b64encode(private.sign(digest)).decode("ascii"),
    )
    release = builder.project_current_release_v2(canonical, sources)
    catalog = _signed_schema_two(builder, private, release)
    bridge_url = "https://channelwatch.coderluii.dev/updates/stable.json"
    catalog_url = "https://channelwatch.coderluii.dev/updates/v2/stable.json"
    documents = {
        bridge_url: json.dumps(bridge).encode(),
        bridge["payload"]["bundle_url"]: bridge_bundle,
        catalog_url: json.dumps(catalog).encode(),
        release["bundle_url"]: bundle,
    }
    argv = [
        str(ROOT / "scripts/release/verify-live-update-manifest.py"),
        "--version", version,
        "--release-url", release["release_url"],
        "--bundle-sha256", digest.hex(),
        "--image-required", "false",
        "--delivery-mode", canonical["delivery_mode"],
        "--recommended-image-version", version,
        "--catalog-url", catalog_url,
        "--catalog-sha256", hashlib.sha256(documents[catalog_url]).hexdigest(),
        "--attempts", "1",
        "--interval", "0",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    fetched = []

    def fetch(url, *, max_bytes):
        fetched.append(url)
        data = documents[url]
        assert len(data) <= max_bytes
        return data

    monkeypatch.setattr(verifier, "fetch_bytes", fetch)

    def resign_catalog():
        catalog["signature"]["value"] = base64.b64encode(
            private.sign(verifier.canonical_payload_bytes(catalog["payload"]))
        ).decode("ascii")
        documents[catalog_url] = json.dumps(catalog).encode()
        argv[argv.index("--catalog-sha256") + 1] = hashlib.sha256(
            documents[catalog_url]
        ).hexdigest()

    return {
        "live": live,
        "release": release,
        "catalog": catalog,
        "catalog_url": catalog_url,
        "bridge_url": bridge_url,
        "bridge_bundle_url": bridge["payload"]["bundle_url"],
        "documents": documents,
        "argv": argv,
        "fetched": fetched,
        "resign_catalog": resign_catalog,
        "config": config,
        "config_path": config_path,
    }


@pytest.mark.parametrize("version", ["1.6.0", "1.6.1"])
def test_live_main_verifies_signed_legacy_catalog_and_permanent_bridge(
    tmp_path, monkeypatch, capsys, version
):
    case = _live_case(tmp_path, monkeypatch, version)
    bridge = case["documents"][case["bridge_url"]]
    assert case["live"].main() == 0
    assert case["fetched"] == [
        case["bridge_url"],
        case["bridge_bundle_url"],
        case["catalog_url"],
        case["release"]["bundle_url"],
    ]
    assert case["documents"][case["bridge_url"]] == bridge
    assert f"v2 release v{version} verified" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["1.6.0", "1.6.1"])
@pytest.mark.parametrize("field,value", [
    ("delivery_mode", "app_update_with_image_refresh"),
    ("image_required", True),
    ("image_refresh_recommended", True),
    ("minimum_image_version", "1.5.3"),
    ("updater_protocol", 3),
    ("compatible_source_application_versions", ["1.4.5"]),
    ("compatible_launcher_protocols", [4]),
    ("automatic_install_allowed", True),
])
def test_live_main_rejects_signed_wrong_legacy_projection(
    tmp_path, monkeypatch, capsys, version, field, value
):
    case = _live_case(tmp_path, monkeypatch, version)
    case["release"][field] = value
    case["resign_catalog"]()
    assert case["live"].main() == 1
    assert "live stable manifest verification failed" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["1.6.0", "1.6.1"])
def test_live_main_rejects_wrong_configured_legacy_sources(
    tmp_path, monkeypatch, capsys, version
):
    case = _live_case(tmp_path, monkeypatch, version)
    case["config"]["legacy_app_update_sources"].pop()
    case["config_path"].write_text(json.dumps(case["config"]), encoding="utf-8")
    assert case["live"].main() == 1
    assert "exact published bridge set" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["1.6.0", "1.6.1"])
def test_live_main_rejects_runtime_schema_at_legacy_catalog_url(
    tmp_path, monkeypatch, capsys, version
):
    case = _live_case(tmp_path, monkeypatch, version)
    case["catalog"]["schema"] = 3
    case["resign_catalog"]()
    assert case["live"].main() == 1
    assert "live stable manifest verification failed" in capsys.readouterr().err


@pytest.mark.parametrize("mutation", [
    "catalog_digest", "catalog_signature", "bundle_signature", "bundle_bytes", "bridge_signature",
])
def test_live_main_keeps_digest_signature_and_bundle_checks(
    tmp_path, monkeypatch, capsys, mutation
):
    case = _live_case(tmp_path, monkeypatch, "1.6.1")
    if mutation == "catalog_digest":
        case["argv"][case["argv"].index("--catalog-sha256") + 1] = "0" * 64
    elif mutation == "catalog_signature":
        case["catalog"]["signature"]["value"] = base64.b64encode(bytes(64)).decode("ascii")
        case["documents"][case["catalog_url"]] = json.dumps(case["catalog"]).encode()
        case["argv"][case["argv"].index("--catalog-sha256") + 1] = hashlib.sha256(
            case["documents"][case["catalog_url"]]
        ).hexdigest()
    elif mutation == "bundle_signature":
        case["release"]["bundle_signature"] = base64.b64encode(bytes(64)).decode("ascii")
        case["resign_catalog"]()
    elif mutation == "bundle_bytes":
        case["documents"][case["release"]["bundle_url"]] += b"changed"
    else:
        bridge = json.loads(case["documents"][case["bridge_url"]])
        bridge["signature"]["value"] = base64.b64encode(bytes(64)).decode("ascii")
        case["documents"][case["bridge_url"]] = json.dumps(bridge).encode()
    assert case["live"].main() == 1
    assert "live stable manifest verification failed" in capsys.readouterr().err


def test_live_main_does_not_trust_test_key_with_production_roots(
    tmp_path, monkeypatch, capsys
):
    case = _live_case(tmp_path, monkeypatch, "1.6.1", production_keys=True)
    assert case["live"].main() == 1
    assert "key" in capsys.readouterr().err.lower()
