from __future__ import annotations

import base64
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.tests.test_runtime_bundle_build import _load_builder, _payload
from core.runtime_generation import descriptor_generation_id


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
