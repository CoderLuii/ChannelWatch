from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.runtime_generation import descriptor_generation_id
from core.tests.test_runtime_bundle_build import _load_builder, _payload
from core.tests.test_update_manager import _bundle
from core.update_catalog import normalize_catalog, select_catalog_release
from core.update_center import UpdateManager

ROOT = Path(__file__).resolve().parents[3]
SOURCE_COMMIT = "a" * 40


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _key_pair():
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return private, {"test-key": public}


def _runtime_archives(
    tmp_path: Path, monkeypatch, *, application_version: str = "1.4.4"
) -> dict[str, tuple[Path, str]]:
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "4")
    runtime_builder = _load_builder()
    result = {}
    for architecture in ("amd64", "arm64"):
        app_root, dependency_root = _payload(tmp_path / architecture)
        inventory_path = dependency_root / "dependencies/inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["platform"]["architecture"] = architecture
        inventory_path.write_text(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        output = tmp_path / (
            f"channelwatch-runtime-v{application_version}-{architecture}.zip"
        )
        runtime_builder.build_runtime_bundle(
            app_root=app_root,
            dependency_root=dependency_root,
            output_path=output,
            application_version=application_version,
            source_commit=SOURCE_COMMIT,
            settings_schema_version=7,
            source_date_epoch=1_700_000_000,
        )
        result[architecture] = (
            output,
            "https://github.com/CoderLuii/ChannelWatch/releases/download/"
            f"v{application_version}/{output.name}",
        )
    return result


def _release() -> dict:
    return {
        "version": "1.4.4",
        "version_tag": "v1.4.4",
        "channel": "stable",
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "image_required": False,
        "delivery_mode": "app_update",
        "minimum_image_version": "1.5.0",
        "updater_protocol": 4,
        "recommended_image_version": "1.5.0",
        "automatic_install_allowed": False,
        "recovery_compatible": True,
        "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v1.4.4",
        "bundle_url": "https://example.invalid/legacy-app.zip",
        "bundle_sha256": "1" * 64,
        "bundle_signature": "legacy-signature",
        "key_id": "test-key",
        "published_at": "2026-10-10T19:10:00Z",
        "highlights": ["runtime"],
        "automatic_install_after": "2026-10-11T19:10:00Z",
        "compatible_source_application_versions": ["1.5.0"],
        "compatible_runtime_abis": ["channelwatch-runtime-v1"],
        "compatible_settings_schema_versions": [7],
        "compatible_launcher_protocols": [4],
        "revocation_state": "active",
        "publication_time": "2026-10-10T19:10:00Z",
    }


def _image_bootstrap_release() -> dict:
    release = _release()
    release.update(
        {
            "version": "1.5.0",
            "version_tag": "v1.5.0",
            "image_required": True,
            "delivery_mode": "image_required",
            "minimum_image_version": "1.5.0",
            "recommended_image_version": "1.5.0",
            "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v1.5.0",
            "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.5.0/channelwatch-app-v1.5.0.zip",
            "compatible_source_application_versions": ["1.5.0"],
        }
    )
    return release


def _release_160() -> dict:
    release = _release()
    release.update(
        {
            "version": "1.6.0",
            "version_tag": "v1.6.0",
            "image_refresh_recommended": False,
            "minimum_image_version": "1.5.3",
            "recommended_image_version": "1.6.0",
            "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v1.6.0",
            "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.0/channelwatch-app-v1.6.0.zip",
            "compatible_source_application_versions": ["1.5.3"],
        }
    )
    return release


def _release_161() -> dict:
    release = _release_160()
    release.update(
        {
            "version": "1.6.1",
            "version_tag": "v1.6.1",
            "recommended_image_version": "1.6.1",
            "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v1.6.1",
            "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.1/channelwatch-app-v1.6.1.zip",
            "compatible_source_application_versions": ["1.5.3", "1.6.0"],
        }
    )
    return release


def _signed_schema_two(builder, private, release):
    payload = {
        "channel": "stable",
        "published_at": release["published_at"],
        "releases": [release],
    }
    return {
        "schema": 2,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": "test-key",
            "value": base64.b64encode(
                private.sign(builder.canonical_payload_bytes(payload))
            ).decode("ascii"),
        },
    }


def test_schema_two_projects_exact_legacy_app_bridge_without_mutating_assets(
    tmp_path: Path,
):
    builder = _load("legacy_160_projection", "scripts/release/build-update-bundle.py")
    verifier = _load(
        "legacy_160_projection_verifier", "scripts/release/verify-update-assets.py"
    )
    private, public = _key_pair()
    bundle = _bundle("1.6.0")
    bundle_digest = hashlib.sha256(bundle).digest()
    canonical = _release_160()
    canonical["bundle_sha256"] = bundle_digest.hex()
    canonical["bundle_signature"] = base64.b64encode(
        private.sign(bundle_digest)
    ).decode("ascii")
    before = copy.deepcopy(canonical)
    projected = builder.project_current_release_v2(
        canonical, ["1.4.0", "1.4.1", "1.4.5"]
    )

    assert canonical == before
    assert projected is not canonical
    assert projected["delivery_mode"] == "app_update"
    assert projected["image_required"] is False
    assert projected["image_refresh_recommended"] is False
    assert projected["updater_protocol"] == 2
    assert projected["compatible_launcher_protocols"] == [3]
    assert projected["compatible_source_application_versions"] == [
        "1.4.0",
        "1.4.1",
        "1.4.5",
    ]
    assert projected["minimum_image_version"] == "1.4.0"
    assert projected["recommended_image_version"] == "1.6.0"
    assert projected["automatic_install_allowed"] is False
    for field in (
        "version",
        "version_tag",
        "runtime_abi",
        "settings_schema_version",
        "release_url",
        "bundle_url",
        "bundle_sha256",
        "bundle_signature",
        "key_id",
        "published_at",
        "publication_time",
        "automatic_install_after",
        "recommended_image_version",
        "highlights",
    ):
        assert projected[field] == canonical[field]

    signed = _signed_schema_two(builder, private, projected)
    assert verifier.verify_update_catalog(
        json.dumps(signed).encode("utf-8"),
        bundle,
        public_keys=public,
        expected_version="1.6.0",
        expected_delivery_mode="app_update",
        expected_runtime_abi="channelwatch-runtime-v1",
        expected_settings_schema_version=7,
        expected_recommended_image_version="1.6.0",
        legacy_app_update_sources=["1.4.0", "1.4.1", "1.4.5"],
    ) == normalize_catalog(
        signed,
        public_keys=public,
        verify_signature=verifier.verify_ed25519_signature,
        canonical_payload=verifier.canonical_payload_bytes,
        validate_url=verifier.validate_trusted_url,
    )
    tampered = copy.deepcopy(signed)
    tampered["payload"]["releases"][0]["minimum_image_version"] = "1.4.1"
    with pytest.raises(Exception, match="Signed update catalog is invalid"):
        verifier.verify_update_catalog(
            json.dumps(tampered).encode("utf-8"),
            bundle,
            public_keys=public,
            expected_version="1.6.0",
            expected_delivery_mode="app_update",
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
            expected_recommended_image_version="1.6.0",
            legacy_app_update_sources=["1.4.0", "1.4.1", "1.4.5"],
        )

    def verify_signature(keys, key_id, value, payload):
        assert keys == public
        assert key_id == "test-key"
        private.public_key().verify(base64.b64decode(value), payload)

    catalog = normalize_catalog(
        signed,
        public_keys=public,
        verify_signature=verify_signature,
        canonical_payload=builder.canonical_payload_bytes,
        validate_url=lambda value: value,
    )
    for source in ("1.4.0", "1.4.1", "1.4.5"):
        selection = select_catalog_release(
            catalog,
            current_version=source,
            runtime_abi="channelwatch-runtime-v1",
            settings_schema_version=7,
            launcher_protocol=3,
        )
        assert selection.release is not None
        assert selection.release["version"] == "1.6.0"
        assert selection.release["delivery_mode"] == "app_update"
        manager = UpdateManager(
            config_dir=tmp_path / source,
            current_version=source,
            image_version=source,
            launcher_protocol=3,
        )
        assert manager._payload_requires_image(selection.release) is False

    for source, launcher_protocol in (("1.4.4", 3), ("1.4.0", 2), ("1.4.0", 4)):
        assert select_catalog_release(
            catalog,
            current_version=source,
            runtime_abi="channelwatch-runtime-v1",
            settings_schema_version=7,
            launcher_protocol=launcher_protocol,
        ).release is None


@pytest.mark.parametrize(
    "sources",
    [
        "1.4.0",
        ["1.4.0", "1.4.1"],
        ["1.4.5", "1.4.1", "1.4.0"],
        ["v1.4.0", "1.4.1", "1.4.5"],
        ["1.4.0", "1.4.1", "1.5.3"],
    ],
)
def test_schema_two_legacy_app_bridge_rejects_any_other_source_set(sources):
    builder = _load("legacy_160_invalid_sources", "scripts/release/build-update-bundle.py")
    with pytest.raises(ValueError, match="exact target contract"):
        builder.project_current_release_v2(_release_160(), sources)


@pytest.mark.parametrize(
    "field,value",
    [
        ("delivery_mode", "image_required"),
        ("image_required", True),
        ("updater_protocol", 3),
        ("minimum_image_version", "1.4.0"),
        ("compatible_source_application_versions", ["1.4.5"]),
        ("compatible_launcher_protocols", [3]),
    ],
)
def test_schema_two_legacy_app_bridge_rejects_noncanonical_release(field, value):
    builder = _load("legacy_160_invalid_release", "scripts/release/build-update-bundle.py")
    release = _release_160()
    release[field] = value
    with pytest.raises(ValueError, match="verified-dependency app-update contract"):
        builder.project_current_release_v2(
            release, ["1.4.0", "1.4.1", "1.4.5"]
        )


@pytest.mark.parametrize("version", ["1.6.5", "1.7.0", "2.0.0"])
def test_schema_two_legacy_app_bridge_rejects_any_future_target(version):
    builder = _load("legacy_160_future_target", "scripts/release/build-update-bundle.py")
    release = _release_160()
    release["version"] = version
    release["version_tag"] = f"v{version}"
    with pytest.raises(ValueError, match="not allowed for this release target"):
        builder.project_current_release_v2(
            release, ["1.4.0", "1.4.1", "1.4.5"]
        )


@pytest.mark.parametrize("delivery_mode", ["app_update", "app_update_with_image_refresh"])
@pytest.mark.parametrize("version", ["1.6.1", "1.6.2", "1.6.3", "1.6.4"])
def test_schema_two_projects_exact_legacy_contract_for_old_image_launchers(delivery_mode, version):
    builder = _load("legacy_161_projection", "scripts/release/build-update-bundle.py")
    verifier = _load(
        "legacy_161_projection_verifier", "scripts/release/verify-update-assets.py"
    )
    private, public = _key_pair()
    bundle = _bundle(version)
    bundle_digest = hashlib.sha256(bundle).digest()
    canonical = _release_161()
    sources = ["1.4.0", "1.4.1", "1.4.5", "1.6.0"]
    canonical_sources = ["1.5.3", "1.6.0"]
    if version in {"1.6.2", "1.6.3", "1.6.4"}:
        sources.append("1.6.1")
        canonical_sources.append("1.6.1")
        canonical = json.loads(json.dumps(canonical).replace("1.6.1", version))
        canonical["compatible_source_application_versions"] = canonical_sources
    if version in {"1.6.3", "1.6.4"}:
        sources.append("1.6.2")
        canonical_sources.append("1.6.2")
        canonical["compatible_source_application_versions"] = canonical_sources
    if version == "1.6.4":
        sources.append("1.6.3")
        canonical_sources.append("1.6.3")
        canonical["compatible_source_application_versions"] = canonical_sources
    canonical.update(delivery_mode=delivery_mode, image_refresh_recommended=delivery_mode == "app_update_with_image_refresh")
    canonical["bundle_sha256"] = bundle_digest.hex()
    canonical["bundle_signature"] = base64.b64encode(
        private.sign(bundle_digest)
    ).decode("ascii")

    projected = builder.project_current_release_v2(
        canonical, sources
    )

    assert projected["version"] == version
    assert projected["minimum_image_version"] == "1.4.0"
    assert projected["updater_protocol"] == 2
    assert projected["compatible_launcher_protocols"] == [3]
    assert projected["compatible_source_application_versions"] == sources
    assert projected["automatic_install_allowed"] is False
    assert canonical["compatible_source_application_versions"] == canonical_sources

    signed = _signed_schema_two(builder, private, projected)
    verified = verifier.verify_update_catalog(
        json.dumps(signed).encode("utf-8"),
        bundle,
        public_keys=public,
        expected_version=version,
        expected_delivery_mode=delivery_mode,
        expected_runtime_abi="channelwatch-runtime-v1",
        expected_settings_schema_version=7,
        expected_recommended_image_version=version,
        legacy_app_update_sources=sources,
    )
    for source in sources:
        selection = select_catalog_release(
            verified,
            current_version=source,
            runtime_abi="channelwatch-runtime-v1",
            settings_schema_version=7,
            launcher_protocol=3,
        )
        assert selection.release is not None
        assert selection.release["version"] == version
        assert selection.release["delivery_mode"] == "app_update"
        manager = UpdateManager(
            config_dir=Path("unused"),
            current_version=source,
            image_version="1.4.0",
            launcher_protocol=3,
        )
        assert manager._payload_requires_image(selection.release) is False


@pytest.mark.parametrize(
    "release,sources",
    [
        (_release_160(), ["1.4.0", "1.4.1", "1.4.5", "1.6.0"]),
        (_release_161(), ["1.4.0", "1.4.1", "1.4.5"]),
    ],
)
def test_schema_two_projection_rejects_cross_target_source_contracts(release, sources):
    builder = _load("legacy_cross_target_projection", "scripts/release/build-update-bundle.py")

    with pytest.raises(ValueError, match="exact target contract"):
        builder.project_current_release_v2(release, sources)


def test_empty_schema_two_projection_keeps_canonical_protocol_four_release():
    builder = _load("legacy_160_empty_projection", "scripts/release/build-update-bundle.py")
    canonical = _release_160()
    assert builder.project_current_release_v2(canonical, []) == canonical
    assert builder.project_current_release_v2(canonical, None) == canonical


def test_asset_verifier_loads_only_the_exact_target_legacy_bridge(tmp_path: Path):
    verifier = _load("legacy_160_config_verifier", "scripts/release/verify-update-assets.py")
    config = tmp_path / "release-config.json"
    config.write_text(
        json.dumps(
            {
                "version": "1.6.0",
                "legacy_app_update_sources": ["1.4.0", "1.4.1", "1.4.5"],
            }
        ),
        encoding="utf-8",
    )
    assert verifier.load_legacy_app_update_sources(
        config, expected_version="1.6.0"
    ) == ["1.4.0", "1.4.1", "1.4.5"]
    assert verifier.load_legacy_app_update_sources(
        config, expected_version="1.5.3"
    ) == []

    config.write_text(
        json.dumps(
            {
                "version": "1.6.0",
                "legacy_app_update_sources": ["1.4.0", "1.4.5"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="exact published bridge set"):
        verifier.load_legacy_app_update_sources(config, expected_version="1.6.0")

    config.write_text(
        json.dumps(
            {
                "version": "1.6.1",
                "legacy_app_update_sources": [
                    "1.4.0",
                    "1.4.1",
                    "1.4.5",
                    "1.6.0",
                ],
            }
        ),
        encoding="utf-8",
    )
    assert verifier.load_legacy_app_update_sources(
        config, expected_version="1.6.1"
    ) == ["1.4.0", "1.4.1", "1.4.5", "1.6.0"]

    config.write_text(
        json.dumps(
            {
                "version": "1.6.5",
                "legacy_app_update_sources": [
                    "1.4.0",
                    "1.4.1",
                    "1.4.5",
                    "1.6.0",
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="supported release target"):
        verifier.load_legacy_app_update_sources(config, expected_version="1.6.5")


def test_protocol_four_161_catalog_accepts_only_153_and_160_sources():
    builder = _load("runtime_161_canonical", "scripts/release/build-update-bundle.py")
    canonical = _release_161()

    projected = builder.project_current_release_v2(
        canonical, ["1.4.0", "1.4.1", "1.4.5", "1.6.0"]
    )

    assert canonical["minimum_image_version"] == "1.5.3"
    assert canonical["updater_protocol"] == 4
    assert canonical["compatible_launcher_protocols"] == [4]
    assert canonical["compatible_source_application_versions"] == ["1.5.3", "1.6.0"]
    assert projected["compatible_launcher_protocols"] == [3]


def test_protocol_four_runtime_catalog_keeps_only_153_app_source(
    tmp_path: Path, monkeypatch
):
    builder = _load("runtime_160_canonical", "scripts/release/build-update-bundle.py")
    verifier = _load("runtime_160_verifier", "scripts/release/verify-update-assets.py")
    private, public = _key_pair()
    bundles = _runtime_archives(
        tmp_path, monkeypatch, application_version="1.6.0"
    )
    canonical = _release_160()
    catalog = builder.build_runtime_catalog_v3(
        catalog_release=canonical,
        catalog_history=[],
        runtime_bundles=bundles,
        expected_source_commit=SOURCE_COMMIT,
        private_key=private,
        key_id="test-key",
        publication_time=canonical["published_at"],
    )
    release = catalog["payload"]["releases"][0]
    assert release["delivery_mode"] == "app_update"
    assert release["image_required"] is False
    assert release["updater_protocol"] == 4
    assert release["compatible_launcher_protocols"] == [4]
    assert release["compatible_source_application_versions"] == ["1.5.3"]
    assert release["minimum_image_version"] == "1.5.3"
    assert release["recommended_image_version"] == "1.6.0"
    assert verifier.verify_runtime_update_catalog(
        json.dumps(catalog).encode("utf-8"),
        {arch: path.read_bytes() for arch, (path, _url) in bundles.items()},
        public_keys=public,
        expected_version="1.6.0",
        expected_source_commit=SOURCE_COMMIT,
        expected_bundle_urls={arch: url for arch, (_path, url) in bundles.items()},
    ) == catalog

    legacy_sources = ["1.4.0", "1.4.1", "1.4.5"]
    projected = builder.project_current_release_v2(canonical, legacy_sources)
    app_catalog = _signed_schema_two(builder, private, projected)
    verifier.verify_runtime_catalog_binding(
        app_catalog,
        catalog,
        expected_version="1.6.0",
        legacy_app_update_sources=legacy_sources,
    )

    for field, value, message in (
        ("updater_protocol", 3, "legacy app-update projection"),
        ("minimum_image_version", "1.4.1", "legacy app-update projection"),
        (
            "compatible_source_application_versions",
            ["1.4.0"],
            "legacy app-update projection",
        ),
        ("automatic_install_allowed", True, "legacy app-update projection"),
        ("bundle_signature", "different", "same signed app asset"),
    ):
        changed = copy.deepcopy(app_catalog)
        changed["payload"]["releases"][0][field] = value
        with pytest.raises(Exception, match=message):
            verifier.verify_runtime_catalog_binding(
                changed,
                catalog,
                expected_version="1.6.0",
                legacy_app_update_sources=legacy_sources,
            )

    wrong_runtime = copy.deepcopy(catalog)
    wrong_runtime["payload"]["releases"][0][
        "compatible_source_application_versions"
    ] = ["1.4.5"]
    with pytest.raises(Exception, match="protocol-4 release contract"):
        verifier.verify_runtime_catalog_binding(
            app_catalog,
            wrong_runtime,
            expected_version="1.6.0",
            legacy_app_update_sources=legacy_sources,
        )


@pytest.mark.parametrize("version", ["1.6.1", "1.6.2", "1.6.3", "1.6.4"])
def test_protocol_four_runtime_binding_requires_exact_source_versions(
    tmp_path: Path, monkeypatch, version
):
    builder = _load("runtime_161_binding_builder", "scripts/release/build-update-bundle.py")
    verifier = _load("runtime_161_binding_verifier", "scripts/release/verify-update-assets.py")
    private, _public = _key_pair()
    bundles = _runtime_archives(
        tmp_path, monkeypatch, application_version=version
    )
    canonical = _release_161()
    legacy_sources = ["1.4.0", "1.4.1", "1.4.5", "1.6.0"]
    if version in {"1.6.2", "1.6.3", "1.6.4"}:
        legacy_sources.append("1.6.1")
        canonical = json.loads(json.dumps(canonical).replace("1.6.1", version))
        canonical["compatible_source_application_versions"] = ["1.5.3", "1.6.0", "1.6.1"]
    if version in {"1.6.3", "1.6.4"}:
        legacy_sources.append("1.6.2")
        canonical["compatible_source_application_versions"].append("1.6.2")
    if version == "1.6.4":
        legacy_sources.append("1.6.3")
        canonical["compatible_source_application_versions"].append("1.6.3")
    runtime_catalog = builder.build_runtime_catalog_v3(
        catalog_release=canonical,
        catalog_history=[],
        runtime_bundles=bundles,
        expected_source_commit=SOURCE_COMMIT,
        private_key=private,
        key_id="test-key",
        publication_time=canonical["published_at"],
    )
    app_catalog = _signed_schema_two(
        builder,
        private,
        builder.project_current_release_v2(canonical, legacy_sources),
    )

    verifier.verify_runtime_catalog_binding(
        app_catalog,
        runtime_catalog,
        expected_version=version,
        legacy_app_update_sources=legacy_sources,
    )

    wrong_runtime = copy.deepcopy(runtime_catalog)
    wrong_runtime["payload"]["releases"][0][
        "compatible_source_application_versions"
    ] = ["1.5.3"]
    with pytest.raises(Exception, match="protocol-4 release contract"):
        verifier.verify_runtime_catalog_binding(
            app_catalog,
            wrong_runtime,
            expected_version=version,
            legacy_app_update_sources=legacy_sources,
        )


@pytest.mark.parametrize("version", ["1.6.3", "1.6.4"])
def test_v163_real_asset_cli_accepts_the_signed_legacy_and_native_catalogs(tmp_path, monkeypatch, version):
    from core.tests import test_runtime_release_assets as fixtures
    from core.tests.test_release_tooling import _signed_update_assets

    builder = _load("v163_cli_builder", "scripts/release/build-update-bundle.py")
    verifier = _load("v163_cli_verifier", "scripts/release/verify-update-assets.py")
    source = tmp_path / "source"
    members = {}
    for member, relative in {
        **verifier.CRITICAL_SOURCE_MEMBERS,
        **verifier.GENERATED_LEGAL_SOURCE_MEMBERS,
    }.items():
        data = (ROOT / relative).read_bytes()
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        members[member] = (
            verifier.render_document(data.decode("utf-8"), version).encode("utf-8")
            if member in verifier.GENERATED_LEGAL_SOURCE_MEMBERS else data
        )
    for arguments in (
        ["init", "-q"], ["config", "user.name", "Release Review"],
        ["config", "user.email", "review@example.invalid"], ["add", "."],
        ["commit", "-q", "-m", "fixture"],
    ):
        subprocess.run(["git", *arguments], cwd=source, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    monkeypatch.setattr(fixtures, "SOURCE_COMMIT", commit)
    private, public = _key_pair()
    manifest_bytes, bundle, _ = _signed_update_assets(
        version, git_sha=commit, member_contents=members,
    )
    canonical = json.loads(json.dumps(_release_161()).replace("1.6.1", version))
    canonical["compatible_source_application_versions"] = ["1.5.3", "1.6.0", "1.6.1", "1.6.2"]
    if version == "1.6.4":
        canonical["compatible_source_application_versions"].append("1.6.3")
    canonical["bundle_sha256"] = hashlib.sha256(bundle).hexdigest()
    canonical["bundle_signature"] = base64.b64encode(private.sign(hashlib.sha256(bundle).digest())).decode("ascii")
    manifest = json.loads(manifest_bytes)
    manifest["payload"].update(canonical)
    manifest["signature"]["value"] = base64.b64encode(private.sign(builder.canonical_payload_bytes(manifest["payload"]))).decode("ascii")
    legacy_sources = ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2"]
    if version == "1.6.4":
        legacy_sources.append("1.6.3")
    app_catalog = _signed_schema_two(builder, private, builder.project_current_release_v2(canonical, legacy_sources))
    bundles = _runtime_archives(tmp_path / "runtime", monkeypatch, application_version=version)
    runtime_catalog = builder.build_runtime_catalog_v3(
        catalog_release=canonical, catalog_history=[], runtime_bundles=bundles,
        expected_source_commit=commit, private_key=private, key_id="test-key",
        publication_time=canonical["published_at"],
    )
    paths = {name: tmp_path / f"{name}.json" for name in ("manifest", "catalog", "runtime", "config")}
    for name, value in (("manifest", manifest), ("catalog", app_catalog), ("runtime", runtime_catalog),
                        ("config", {"version": version, "legacy_app_update_sources": legacy_sources})):
        paths[name].write_text(json.dumps(value), encoding="utf-8")
    bundle_path = tmp_path / "app.zip"
    bundle_path.write_bytes(bundle)
    arguments = [sys.executable, "-B", str(ROOT / "scripts/release/verify-update-assets.py"),
        "--manifest", str(paths["manifest"]), "--bundle", str(bundle_path),
        "--catalog", str(paths["catalog"]), "--runtime-catalog", str(paths["runtime"]),
        "--release-config", str(paths["config"]), "--source-root", str(source),
        "--expected-version", version, "--expected-channel", "stable",
        "--expected-runtime-abi", "channelwatch-runtime-v1", "--expected-settings-schema-version", "7",
        "--expected-image-required", "false", "--expected-delivery-mode", "app_update",
        "--expected-recommended-image-version", version, "--expected-git-sha", commit,
        "--expected-runtime-source-commit", commit, "--expected-release-url", canonical["release_url"],
        "--expected-bundle-url", canonical["bundle_url"], "--public-key", f"test-key={public['test-key']}"]
    for arch, (path, url) in bundles.items():
        arguments.extend(["--runtime-bundle", f"{arch}={path}", "--expected-runtime-bundle-url", f"{arch}={url}"])
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert f"Verified signed update assets for v{version}" in result.stdout
    refresh_runtime = copy.deepcopy(runtime_catalog)
    refresh_runtime["payload"]["releases"][0].update(
        delivery_mode="app_update_with_image_refresh", image_refresh_recommended=True,
    )
    refresh_runtime["signature"]["value"] = base64.b64encode(
        private.sign(builder.canonical_payload_bytes(refresh_runtime["payload"]))
    ).decode("ascii")
    paths["runtime"].write_text(json.dumps(refresh_runtime), encoding="utf-8")
    refresh_result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if version == "1.6.3":
        assert refresh_result.returncode == 0, refresh_result.stderr
    else:
        assert refresh_result.returncode == 1
        assert "protocol-4 release contract" in refresh_result.stderr
    paths["runtime"].write_text(json.dumps(runtime_catalog), encoding="utf-8")
    app_catalog["payload"]["releases"][0]["compatible_source_application_versions"].pop()
    app_catalog["signature"]["value"] = base64.b64encode(private.sign(builder.canonical_payload_bytes(app_catalog["payload"]))).decode("ascii")
    paths["catalog"].write_text(json.dumps(app_catalog), encoding="utf-8")
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "legacy app-update projection" in result.stderr


@pytest.mark.parametrize(
    "difference",
    [
        None,
        "recommended_image_version",
        "release_url",
        "bundle_sha256",
        "highlights",
        "channel",
        "published_at",
        "source_commit",
        "missing_catalog",
        "missing_app_source_commit",
        "historical_highlights",
        "missing_runtime_history",
        "missing_app_history",
    ],
)
def test_joint_cli_binds_signed_image_catalog_to_verified_app_release(
    tmp_path: Path, monkeypatch, difference: str | None
):
    builder = _load("joint_bootstrap_builder", "scripts/release/build-update-bundle.py")
    verifier = _load("joint_bootstrap_verifier", "scripts/release/verify-update-assets.py")
    private, public = _key_pair()
    release = _image_bootstrap_release()
    runtime_catalog = builder.build_runtime_catalog_v3(
        catalog_release=release,
        catalog_history=[{**release, "version": "1.4.0", "version_tag": "v1.4.0"}],
        runtime_bundles={},
        expected_source_commit=SOURCE_COMMIT,
        private_key=private,
        key_id="test-key",
        publication_time=release["published_at"],
    )
    app_catalog = copy.deepcopy(runtime_catalog)
    app_catalog["schema"] = 2
    changed = runtime_catalog["payload"]["releases"][0]
    changes = {
        "recommended_image_version": "9.9.9",
        "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v9.9.9",
        "bundle_sha256": "f" * 64,
        "highlights": ["different signed bootstrap"],
    }
    if difference in changes:
        changed[difference] = changes[difference]
    elif difference == "channel":
        runtime_catalog["payload"]["channel"] = "different-channel"
    elif difference == "published_at":
        runtime_catalog["payload"]["published_at"] = "2026-10-10T20:10:00Z"
    elif difference == "historical_highlights":
        runtime_catalog["payload"]["releases"][1]["highlights"] = ["different signed history"]
    elif difference == "missing_runtime_history":
        runtime_catalog["payload"]["releases"].pop()
    elif difference == "missing_app_history":
        app_catalog["payload"]["releases"].pop()
    runtime_catalog["signature"]["value"] = base64.b64encode(
        private.sign(verifier.canonical_payload_bytes(runtime_catalog["payload"]))
    ).decode("ascii")
    paths = {name: tmp_path / name for name in ("manifest", "bundle", "catalog", "runtime")}
    paths["manifest"].write_bytes(b"independently verified manifest")
    paths["bundle"].write_bytes(b"independently verified app archive")
    paths["catalog"].write_text(json.dumps(app_catalog), encoding="utf-8")
    paths["runtime"].write_text(json.dumps(runtime_catalog), encoding="utf-8")
    # Existing asset tests cover app verification; exercise real runtime
    # signatures and the mandatory CLI's cross-catalog boundary here.
    monkeypatch.setattr(
        verifier,
        "verify_update_assets",
        lambda *_args, **_kwargs: {"payload": release},
    )
    monkeypatch.setattr(
        verifier, "verify_update_catalog", lambda *_args, **_kwargs: app_catalog
    )
    argv = [
        "verify-update-assets.py",
        "--manifest", str(paths["manifest"]),
        "--bundle", str(paths["bundle"]),
        "--runtime-catalog", str(paths["runtime"]),
        "--expected-runtime-source-commit",
        "b" * 40 if difference == "source_commit" else SOURCE_COMMIT,
        "--expected-version", "1.5.0",
        "--expected-channel", "stable",
        "--expected-runtime-abi", "channelwatch-runtime-v1",
        "--expected-settings-schema-version", "7",
        "--expected-delivery-mode", "image_required",
        "--expected-recommended-image-version", "1.5.0",
        "--expected-image-required", "true",
        "--expected-git-sha", SOURCE_COMMIT,
        "--expected-release-url", release["release_url"],
        "--expected-bundle-url", release["bundle_url"],
        "--public-key", f"test-key={public['test-key']}",
    ]
    if difference != "missing_catalog":
        argv.extend(["--catalog", str(paths["catalog"])])
    if difference == "missing_app_source_commit":
        index = argv.index("--expected-git-sha")
        del argv[index : index + 2]
    monkeypatch.setattr(sys, "argv", argv)
    if difference == "missing_app_source_commit":
        with pytest.raises(SystemExit) as exc:
            verifier.main()
        assert exc.value.code == 2
        return
    assert verifier.main() == (0 if difference is None else 1)


def _catalog(tmp_path: Path, monkeypatch):
    builder = _load("runtime_release_builder", "scripts/release/build-update-bundle.py")
    private, public = _key_pair()
    bundles = _runtime_archives(tmp_path, monkeypatch)
    history = [{"version": "1.4.1", "legacy": "unchanged"}]
    release = _release()
    release_before = copy.deepcopy(release)
    history_before = copy.deepcopy(history)
    catalog = builder.build_runtime_catalog_v3(
        catalog_release=release,
        catalog_history=history,
        runtime_bundles=bundles,
        expected_source_commit=SOURCE_COMMIT,
        private_key=private,
        key_id="test-key",
        publication_time="2026-10-10T19:10:00Z",
    )
    assert release == release_before
    assert history == history_before
    return builder, catalog, bundles, private, public


def test_runtime_catalog_is_deterministic_and_keeps_schema_two_history_separate(
    tmp_path: Path, monkeypatch
):
    builder, first, bundles, private, _public = _catalog(tmp_path, monkeypatch)
    second = builder.build_runtime_catalog_v3(
        catalog_release=_release(),
        catalog_history=[{"version": "1.4.1", "legacy": "unchanged"}],
        runtime_bundles=dict(reversed(list(bundles.items()))),
        expected_source_commit=SOURCE_COMMIT,
        private_key=private,
        key_id="test-key",
        publication_time="2026-10-10T19:10:00Z",
    )

    assert first == second
    assert first["schema"] == 3
    release = first["payload"]["releases"][0]
    assert [item["runtime_descriptor"]["architecture"] for item in release["runtime_variants"]] == [
        "amd64",
        "arm64",
    ]
    assert first["payload"]["releases"][1] == {
        "version": "1.4.1",
        "legacy": "unchanged",
    }
    assert "runtime_variants" not in first["payload"]["releases"][1]


def test_runtime_catalog_requires_both_native_platforms(tmp_path: Path, monkeypatch):
    builder, _catalog_value, bundles, private, _public = _catalog(tmp_path, monkeypatch)
    bundles.pop("arm64")

    with pytest.raises(ValueError, match="amd64 and arm64"):
        builder.build_runtime_catalog_v3(
            catalog_release=_release(),
            catalog_history=[],
            runtime_bundles=bundles,
            expected_source_commit=SOURCE_COMMIT,
            private_key=private,
            key_id="test-key",
            publication_time="2026-10-10T19:10:00Z",
        )


def test_image_bootstrap_runtime_catalog_is_deterministic_and_has_no_fake_generation():
    builder = _load("runtime_image_bootstrap_builder", "scripts/release/build-update-bundle.py")
    verifier = _load("runtime_image_bootstrap_verifier", "scripts/release/verify-update-assets.py")
    private, public = _key_pair()
    kwargs = {
        "catalog_release": _image_bootstrap_release(),
        "catalog_history": [{"version": "1.4.1", "legacy": "unchanged"}],
        "runtime_bundles": {},
        "expected_source_commit": SOURCE_COMMIT,
        "private_key": private,
        "key_id": "test-key",
        "publication_time": "2026-10-10T19:10:00Z",
    }

    first = builder.build_runtime_catalog_v3(**kwargs)
    second = builder.build_runtime_catalog_v3(**copy.deepcopy(kwargs))
    release = first["payload"]["releases"][0]

    assert first == second
    assert first["schema"] == 3
    assert release["delivery_mode"] == "image_required"
    assert release["image_required"] is True
    assert "runtime_variants" not in release
    assert "runtime_descriptor" not in release
    assert verifier.verify_runtime_update_catalog(
        (json.dumps(first, indent=2) + "\n").encode("utf-8"),
        {},
        public_keys=public,
        expected_version="1.5.0",
        expected_source_commit=SOURCE_COMMIT,
        expected_bundle_urls={},
    ) == first

    runtime_catalog = _load("runtime_image_bootstrap_projection", "app/core/runtime_catalog.py")
    projected = runtime_catalog.normalize_runtime_release(
        release, platform=None, validate_url=lambda value: value
    )
    assert projected["delivery_mode"] == "image_required"
    assert projected["image_required"] is True

    forged_generation = copy.deepcopy(first)
    forged_generation["payload"]["releases"][0]["runtime_variants"] = []
    forged_generation["signature"]["value"] = base64.b64encode(
        private.sign(verifier.canonical_payload_bytes(forged_generation["payload"]))
    ).decode("ascii")
    with pytest.raises(Exception, match="Image-only"):
        verifier.verify_runtime_update_catalog(
            json.dumps(forged_generation).encode("utf-8"),
            {},
            public_keys=public,
            expected_version="1.5.0",
            expected_source_commit=SOURCE_COMMIT,
            expected_bundle_urls={},
        )


def test_image_bootstrap_catalog_refuses_runtime_generation_assets(
    tmp_path: Path, monkeypatch
):
    builder = _load("runtime_image_bootstrap_refusal", "scripts/release/build-update-bundle.py")
    private, _public = _key_pair()

    with pytest.raises(ValueError, match="[Ii]mage-only"):
        builder.build_runtime_catalog_v3(
            catalog_release=_image_bootstrap_release(),
            catalog_history=[],
            runtime_bundles=_runtime_archives(tmp_path, monkeypatch),
            expected_source_commit=SOURCE_COMMIT,
            private_key=private,
            key_id="test-key",
            publication_time="2026-10-10T19:10:00Z",
        )


def test_full_runtime_catalog_continues_after_image_bootstrap(
    tmp_path: Path, monkeypatch
):
    builder = _load("runtime_after_bootstrap_builder", "scripts/release/build-update-bundle.py")
    private, public = _key_pair()
    bundles = _runtime_archives(
        tmp_path, monkeypatch, application_version="1.5.1"
    )
    bootstrap = _image_bootstrap_release()
    future_release = _release()
    future_release.update(
        {
            "version": "1.5.1",
            "version_tag": "v1.5.1",
            "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v1.5.1",
            "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.5.1/channelwatch-app-v1.5.1.zip",
        }
    )
    catalog = builder.build_runtime_catalog_v3(
        catalog_release=future_release,
        catalog_history=[bootstrap],
        runtime_bundles=bundles,
        expected_source_commit=SOURCE_COMMIT,
        private_key=private,
        key_id="test-key",
        publication_time="2026-10-10T19:10:00Z",
    )

    assert "runtime_variants" not in catalog["payload"]["releases"][1]
    verifier = _load("runtime_after_bootstrap_verifier", "scripts/release/verify-update-assets.py")
    assert verifier.verify_runtime_update_catalog(
        json.dumps(catalog).encode("utf-8"),
        {arch: path.read_bytes() for arch, (path, _url) in bundles.items()},
        public_keys=public,
        expected_version="1.5.1",
        expected_source_commit=SOURCE_COMMIT,
        expected_bundle_urls={arch: url for arch, (_path, url) in bundles.items()},
    ) == catalog


def test_runtime_cli_mapping_rejects_duplicate_platform_values():
    builder = _load("runtime_release_builder_mapping", "scripts/release/build-update-bundle.py")

    with pytest.raises(ValueError, match="unique"):
        builder._architecture_values(
            ["amd64=first.zip", "amd64=second.zip"], label="--runtime-bundle"
        )


def test_runtime_catalog_rejects_descriptor_native_binding_mismatch(
    tmp_path: Path, monkeypatch
):
    builder, _catalog_value, bundles, private, _public = _catalog(tmp_path, monkeypatch)
    bundles["arm64"] = bundles["amd64"]

    with pytest.raises(ValueError, match="architecture"):
        builder.build_runtime_catalog_v3(
            catalog_release=_release(),
            catalog_history=[],
            runtime_bundles=bundles,
            expected_source_commit=SOURCE_COMMIT,
            private_key=private,
            key_id="test-key",
            publication_time="2026-10-10T19:10:00Z",
        )


def test_runtime_asset_verifier_rejects_bundle_tamper(tmp_path: Path, monkeypatch):
    _builder, catalog, bundles, _private, public = _catalog(tmp_path, monkeypatch)
    verifier = _load("runtime_asset_verifier", "scripts/release/verify-update-assets.py")
    archive_bytes = {arch: path.read_bytes() for arch, (path, _url) in bundles.items()}
    archive_bytes["amd64"] += b"tamper"

    with pytest.raises(Exception, match="hash"):
        verifier.verify_runtime_update_catalog(
            json.dumps(catalog).encode("utf-8"),
            archive_bytes,
            public_keys=public,
            expected_version="1.4.4",
            expected_source_commit=SOURCE_COMMIT,
            expected_bundle_urls={arch: url for arch, (_path, url) in bundles.items()},
        )


def test_runtime_asset_verifier_accepts_both_real_prebuilt_archives(
    tmp_path: Path, monkeypatch
):
    _builder, catalog, bundles, _private, public = _catalog(tmp_path, monkeypatch)
    verifier = _load("runtime_asset_verifier_success", "scripts/release/verify-update-assets.py")

    verified = verifier.verify_runtime_update_catalog(
        json.dumps(catalog).encode("utf-8"),
        {arch: path.read_bytes() for arch, (path, _url) in bundles.items()},
        public_keys=public,
        expected_version="1.4.4",
        expected_source_commit=SOURCE_COMMIT,
        expected_bundle_urls={arch: url for arch, (_path, url) in bundles.items()},
    )

    assert verified == catalog


def test_joint_binding_accepts_real_generation_and_rejects_extra_common_fields(
    tmp_path: Path, monkeypatch
):
    _builder, catalog, bundles, private, public = _catalog(tmp_path, monkeypatch)
    verifier = _load("joint_generation_verifier", "scripts/release/verify-update-assets.py")
    app_catalog = copy.deepcopy(catalog)
    app_catalog["schema"] = 2
    app_catalog["payload"]["releases"][0].pop("runtime_variants")
    kwargs = {
        "public_keys": public,
        "expected_version": "1.4.4",
        "expected_source_commit": SOURCE_COMMIT,
        "expected_bundle_urls": {arch: url for arch, (_path, url) in bundles.items()},
    }
    archive_bytes = {arch: path.read_bytes() for arch, (path, _url) in bundles.items()}
    verified = verifier.verify_runtime_update_catalog(
        json.dumps(catalog).encode("utf-8"), archive_bytes, **kwargs
    )
    verifier.verify_runtime_catalog_binding(
        app_catalog, verified, expected_version="1.4.4"
    )
    catalog["payload"]["releases"][0]["foreign_field"] = "must not be ignored"
    catalog["signature"]["value"] = base64.b64encode(
        private.sign(verifier.canonical_payload_bytes(catalog["payload"]))
    ).decode("ascii")
    verified = verifier.verify_runtime_update_catalog(
        json.dumps(catalog).encode("utf-8"), archive_bytes, **kwargs
    )
    with pytest.raises(Exception, match="does not match"):
        verifier.verify_runtime_catalog_binding(
            app_catalog, verified, expected_version="1.4.4"
        )


def test_signed_runtime_catalog_over_legacy_limit_is_accepted(
    tmp_path: Path, monkeypatch
):
    builder, _catalog_value, bundles, private, public = _catalog(tmp_path, monkeypatch)
    verifier = _load("runtime_asset_verifier_large", "scripts/release/verify-update-assets.py")
    release = _release()
    release["highlights"] = ["x" * (verifier.MAX_MANIFEST_BYTES + 64 * 1024)]
    catalog = builder.build_runtime_catalog_v3(
        catalog_release=release,
        catalog_history=[],
        runtime_bundles=bundles,
        expected_source_commit=SOURCE_COMMIT,
        private_key=private,
        key_id="test-key",
        publication_time="2026-10-10T19:10:00Z",
    )
    encoded = (json.dumps(catalog, indent=2) + "\n").encode("utf-8")

    assert verifier.MAX_MANIFEST_BYTES < len(encoded) < verifier.MAX_RUNTIME_CATALOG_BYTES
    assert verifier.verify_runtime_update_catalog(
        encoded,
        {arch: path.read_bytes() for arch, (path, _url) in bundles.items()},
        public_keys=public,
        expected_version="1.4.4",
        expected_source_commit=SOURCE_COMMIT,
        expected_bundle_urls={arch: url for arch, (_path, url) in bundles.items()},
    ) == catalog


def test_runtime_catalog_over_four_mib_is_rejected_by_builder_and_verifier(
    tmp_path: Path, monkeypatch
):
    builder, catalog, bundles, private, public = _catalog(tmp_path, monkeypatch)
    release = _release()
    release["highlights"] = ["x" * (builder.MAX_RUNTIME_CATALOG_BYTES + 1)]

    with pytest.raises(ValueError, match="size limit"):
        builder.build_runtime_catalog_v3(
            catalog_release=release,
            catalog_history=[],
            runtime_bundles=bundles,
            expected_source_commit=SOURCE_COMMIT,
            private_key=private,
            key_id="test-key",
            publication_time="2026-10-10T19:10:00Z",
        )

    verifier = _load("runtime_asset_verifier_too_large", "scripts/release/verify-update-assets.py")
    catalog["payload"]["padding"] = "x" * verifier.MAX_RUNTIME_CATALOG_BYTES
    catalog["signature"]["value"] = base64.b64encode(
        private.sign(verifier.canonical_payload_bytes(catalog["payload"]))
    ).decode("ascii")
    encoded = json.dumps(catalog).encode("utf-8")
    assert len(encoded) > verifier.MAX_RUNTIME_CATALOG_BYTES
    with pytest.raises(Exception, match="size limit"):
        verifier.verify_runtime_update_catalog(
            encoded,
            {arch: path.read_bytes() for arch, (path, _url) in bundles.items()},
            public_keys=public,
            expected_version="1.4.4",
            expected_source_commit=SOURCE_COMMIT,
            expected_bundle_urls={arch: url for arch, (_path, url) in bundles.items()},
        )


def test_legacy_catalog_and_cli_file_bound_remain_one_mib(tmp_path: Path):
    verifier = _load("legacy_asset_verifier_bound", "scripts/release/verify-update-assets.py")
    oversized = b"x" * (verifier.MAX_MANIFEST_BYTES + 1)
    path = tmp_path / "oversized-schema-two.json"
    path.write_bytes(oversized)

    with pytest.raises(Exception, match="size limit"):
        verifier.verify_update_catalog(
            oversized,
            b"",
            expected_version="1.4.1",
            expected_delivery_mode="app_update",
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
            expected_recommended_image_version="1.4.1",
        )
    with pytest.raises(Exception, match="size limit"):
        verifier._read_limited_file(path, max_bytes=verifier.MAX_MANIFEST_BYTES)
    assert verifier._read_limited_file(
        path, max_bytes=verifier.MAX_RUNTIME_CATALOG_BYTES
    ) == oversized


def test_runtime_asset_verifier_checks_inventory_and_interpreter_binding(
    tmp_path: Path, monkeypatch
):
    _builder, catalog, bundles, private, public = _catalog(tmp_path, monkeypatch)
    verifier = _load("runtime_asset_verifier_binding", "scripts/release/verify-update-assets.py")
    release = catalog["payload"]["releases"][0]
    descriptor = release["runtime_variants"][1]["runtime_descriptor"]
    descriptor["python_version"] = "3.13.0"
    descriptor["generation_id"] = descriptor_generation_id(descriptor)
    catalog["signature"]["value"] = base64.b64encode(
        private.sign(verifier.canonical_payload_bytes(catalog["payload"]))
    ).decode("ascii")

    with pytest.raises(Exception, match="interpreter"):
        verifier.verify_runtime_update_catalog(
            json.dumps(catalog).encode("utf-8"),
            {arch: path.read_bytes() for arch, (path, _url) in bundles.items()},
            public_keys=public,
            expected_version="1.4.4",
            expected_source_commit=SOURCE_COMMIT,
            expected_bundle_urls={arch: url for arch, (_path, url) in bundles.items()},
        )


def test_v163_history_retains_the_exact_published_v162_legacy_record():
    builder = _load("v163_history_builder", "scripts/release/build-update-bundle.py")
    history = builder.load_catalog_history("1.6.4")
    predecessor = next(release for release in history if release["version"] == "1.6.2")
    assert hashlib.sha256(builder.canonical_payload_bytes(predecessor)).hexdigest() == "1aa1aab169f81223084cc6ace956c662a64b02cd8830a7dd8004879093728740"
    projected = builder.project_catalog_history_v2(history)
    assert next(release for release in projected if release["version"] == "1.6.2") == predecessor
    verifier = _load("v163_history_verifier", "scripts/release/verify-update-assets.py")
    verifier.verify_ed25519_signature(
        verifier.UPDATE_PUBLIC_KEYS, predecessor["key_id"], predecessor["bundle_signature"],
        bytes.fromhex(predecessor["bundle_sha256"]),
    )


def test_v164_history_retains_authentic_v163_without_rewriting_prior_records():
    builder = _load("v164_history_builder", "scripts/release/build-update-bundle.py")
    raw = json.loads((ROOT / "scripts/release/update-catalog-history.json").read_text(encoding="utf-8"))["releases"]
    assert len(raw) == 26
    assert hashlib.sha256(builder.canonical_payload_bytes(raw[:-1])).hexdigest() == "3e85e0b4bf3ccd5fa93ab880172ff9a895049dba7888e326682e64cec109626d"
    history = builder.load_catalog_history("1.6.4")
    predecessor = next(release for release in history if release["version"] == "1.6.3")
    assert hashlib.sha256(builder.canonical_payload_bytes(predecessor)).hexdigest() == "b7d4e205bd7c102e1d3985dd3693d958b39a84f0477ae583a1b01dfb9e6b54b8"
    assert next(release for release in builder.project_catalog_history_v2(history) if release["version"] == "1.6.3") == predecessor
    verifier = _load("v164_history_verifier", "scripts/release/verify-update-assets.py")
    verifier.verify_ed25519_signature(verifier.UPDATE_PUBLIC_KEYS, predecessor["key_id"], predecessor["bundle_signature"], bytes.fromhex(predecessor["bundle_sha256"]))
