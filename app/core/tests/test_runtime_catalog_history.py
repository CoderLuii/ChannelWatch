from __future__ import annotations

import base64
import copy
import json
from typing import Any

import pytest

from core.runtime_catalog import normalize_runtime_release
from core.runtime_generation import (
    RuntimePlatform,
    descriptor_generation_id,
    detect_runtime_platform,
)
from core.tests import test_runtime_bundle_build as bundle_assets
from core.tests import test_runtime_release_assets as assets
from core.update_center import (
    UpdateManifestError,
    canonical_payload_bytes,
    read_update_document_bytes,
)


def _release(version: str) -> dict[str, Any]:
    release = assets._release()
    release.update(
        {
            "version": version,
            "version_tag": f"v{version}",
            "release_url": (
                "https://github.com/CoderLuii/ChannelWatch/releases/tag/"
                f"v{version}"
            ),
            "bundle_url": (
                "https://github.com/CoderLuii/ChannelWatch/releases/download/"
                f"v{version}/channelwatch-app-v{version}.zip"
            ),
        }
    )
    return release


def _resign(catalog: dict[str, Any], private_key: Any) -> None:
    catalog["signature"]["value"] = base64.b64encode(
        private_key.sign(canonical_payload_bytes(catalog["payload"]))
    ).decode("ascii")


def test_prospective_history_keeps_an_installable_fallback_within_budget(
    tmp_path, monkeypatch
):
    builder = assets._load(
        "prospective_runtime_history_builder",
        "scripts/release/build-update-bundle.py",
    )
    private_key, public_keys = assets._key_pair()
    release_151 = _release("1.5.1")
    bundles_151 = assets._runtime_archives(
        tmp_path / "151", monkeypatch, application_version="1.5.1"
    )
    catalog_151 = builder.build_runtime_catalog_v3(
        catalog_release=release_151,
        catalog_history=[],
        runtime_bundles=bundles_151,
        expected_source_commit=assets.SOURCE_COMMIT,
        private_key=private_key,
        key_id="test-key",
        publication_time="2026-10-11T19:10:00Z",
    )
    history = [catalog_151["payload"]["releases"][0]]
    history_before = copy.deepcopy(history)

    local_platform = detect_runtime_platform(launcher_protocol=4)
    future_platform = RuntimePlatform(
        local_platform.architecture,
        local_platform.python_cache_tag,
        local_platform.libc_family,
        (99, 0),
        local_platform.launcher_capabilities,
    )
    monkeypatch.setattr(
        bundle_assets, "detect_runtime_platform", lambda: future_platform
    )
    release_152 = _release("1.5.2")
    bundles_152 = assets._runtime_archives(
        tmp_path / "152", monkeypatch, application_version="1.5.2"
    )
    release_152_before = copy.deepcopy(release_152)
    catalog_152 = builder.build_runtime_catalog_v3(
        catalog_release=release_152,
        catalog_history=history,
        runtime_bundles=bundles_152,
        expected_source_commit=assets.SOURCE_COMMIT,
        private_key=private_key,
        key_id="test-key",
        publication_time="2026-10-12T19:10:00Z",
    )

    assert history == history_before
    assert release_152 == release_152_before
    assert catalog_152["payload"]["releases"][1] == history_before[0]
    for field, value in (
        ("application_version", "9.9.9"),
        ("generation_id", "f" * 64),
    ):
        corrupted_history = copy.deepcopy(history)
        corrupted_history[0]["runtime_variants"][0]["runtime_descriptor"][
            field
        ] = value
        with pytest.raises(ValueError):
            builder.build_runtime_catalog_v3(
                catalog_release=release_152,
                catalog_history=corrupted_history,
                runtime_bundles=bundles_152,
                expected_source_commit=assets.SOURCE_COMMIT,
                private_key=private_key,
                key_id="test-key",
                publication_time="2026-10-12T19:10:00Z",
            )

    one_architecture = copy.deepcopy(history)
    one_architecture[0]["runtime_variants"].pop()
    duplicate_architecture = copy.deepcopy(history)
    duplicate_descriptor = duplicate_architecture[0]["runtime_variants"][1][
        "runtime_descriptor"
    ]
    duplicate_descriptor["architecture"] = "amd64"
    duplicate_descriptor["python_cache_tag"] = "cpython-999"
    duplicate_descriptor["generation_id"] = descriptor_generation_id(
        duplicate_descriptor
    )
    for malformed_history in (one_architecture, duplicate_architecture):
        with pytest.raises(ValueError):
            builder.build_runtime_catalog_v3(
                catalog_release=release_152,
                catalog_history=malformed_history,
                runtime_bundles=bundles_152,
                expected_source_commit=assets.SOURCE_COMMIT,
                private_key=private_key,
                key_id="test-key",
                publication_time="2026-10-12T19:10:00Z",
            )

    verifier = assets._load(
        "prospective_runtime_history_verifier",
        "scripts/release/verify-update-assets.py",
    )
    runtime_bundle_bytes = {
        architecture: path.read_bytes()
        for architecture, (path, _url) in bundles_152.items()
    }
    runtime_bundle_urls = {
        architecture: url
        for architecture, (_path, url) in bundles_152.items()
    }
    assert verifier.verify_runtime_update_catalog(
        json.dumps(catalog_152).encode("utf-8"),
        runtime_bundle_bytes,
        public_keys=public_keys,
        expected_version="1.5.2",
        expected_source_commit=assets.SOURCE_COMMIT,
        expected_bundle_urls=runtime_bundle_urls,
    ) == catalog_152
    corrupted_catalog = copy.deepcopy(catalog_152)
    corrupted_catalog["payload"]["releases"][1]["runtime_variants"][0][
        "runtime_descriptor"
    ]["generation_id"] = "f" * 64
    _resign(corrupted_catalog, private_key)
    with pytest.raises(verifier.UpdateBundleError, match="invalid"):
        verifier.verify_runtime_update_catalog(
            json.dumps(corrupted_catalog).encode("utf-8"),
            runtime_bundle_bytes,
            public_keys=public_keys,
            expected_version="1.5.2",
            expected_source_commit=assets.SOURCE_COMMIT,
            expected_bundle_urls=runtime_bundle_urls,
        )

    historical_failures = []
    one_architecture_catalog = copy.deepcopy(catalog_152)
    one_architecture_catalog["payload"]["releases"][1][
        "runtime_variants"
    ].pop()
    historical_failures.append(one_architecture_catalog)

    duplicate_architecture_catalog = copy.deepcopy(catalog_152)
    duplicate_descriptor = duplicate_architecture_catalog["payload"]["releases"][1][
        "runtime_variants"
    ][1]["runtime_descriptor"]
    duplicate_descriptor["architecture"] = "amd64"
    duplicate_descriptor["python_cache_tag"] = "cpython-999"
    duplicate_descriptor["generation_id"] = descriptor_generation_id(
        duplicate_descriptor
    )
    historical_failures.append(duplicate_architecture_catalog)

    unknown_key_catalog = copy.deepcopy(catalog_152)
    unknown_key_catalog["payload"]["releases"][1]["runtime_variants"][0][
        "key_id"
    ] = "unknown-key"
    historical_failures.append(unknown_key_catalog)

    wrong_signature_catalog = copy.deepcopy(catalog_152)
    wrong_signature_catalog["payload"]["releases"][1]["runtime_variants"][0][
        "bundle_signature"
    ] = base64.b64encode(bytes(64)).decode("ascii")
    historical_failures.append(wrong_signature_catalog)

    for malformed_catalog in historical_failures:
        _resign(malformed_catalog, private_key)
        with pytest.raises((verifier.UpdateBundleError, verifier.UpdateCenterError)):
            verifier.verify_runtime_update_catalog(
                json.dumps(malformed_catalog).encode("utf-8"),
                runtime_bundle_bytes,
                public_keys=public_keys,
                expected_version="1.5.2",
                expected_source_commit=assets.SOURCE_COMMIT,
                expected_bundle_urls=runtime_bundle_urls,
            )

    historical_image_generation = copy.deepcopy(catalog_152)
    historical_image_release = historical_image_generation["payload"]["releases"][1]
    historical_image_release["delivery_mode"] = "image_required"
    historical_image_release["image_required"] = True
    _resign(historical_image_generation, private_key)
    with pytest.raises(verifier.UpdateBundleError, match="invalid"):
        verifier.verify_runtime_update_catalog(
            json.dumps(historical_image_generation).encode("utf-8"),
            runtime_bundle_bytes,
            public_keys=public_keys,
            expected_version="1.5.2",
            expected_source_commit=assets.SOURCE_COMMIT,
            expected_bundle_urls=runtime_bundle_urls,
        )

    descriptor_151 = history[0]["runtime_variants"][0]["runtime_descriptor"]
    runtime_platform = RuntimePlatform(
        "amd64",
        descriptor_151["python_cache_tag"],
        descriptor_151["libc_family"],
        tuple(descriptor_151["minimum_libc_version"]),
        frozenset({"isolated_dependencies_v1"}),
    )
    selected = read_update_document_bytes(
        json.dumps(catalog_152).encode("utf-8"),
        public_keys,
        current_version="1.5.0",
        runtime_abi="channelwatch-runtime-v1",
        settings_schema_version=7,
        launcher_protocol=4,
        runtime_platform=runtime_platform,
    )
    assert selected["payload"]["version"] == "1.5.1"
    assert selected["payload"]["runtime_descriptor"]["architecture"] == "amd64"

    projected_v2 = builder.project_catalog_history_v2(history)
    assert history == history_before
    assert "runtime_variants" not in projected_v2[0]
    assert "runtime_descriptor" not in projected_v2[0]
    assert projected_v2[0] == {
        key: value
        for key, value in history_before[0].items()
        if key != "runtime_variants"
    }

    older_full_record = copy.deepcopy(history[0])
    older_full_record["version"] = "1.5.0"
    older_full_record["version_tag"] = "v1.5.0"
    full_history = [older_full_record, history[0]]
    full_history_before = copy.deepcopy(full_history)
    bounded_history, retained_version = builder._project_runtime_catalog_history(
        full_history
    )
    assert retained_version == "1.5.1"
    assert "runtime_variants" not in bounded_history[0]
    assert "runtime_variants" in bounded_history[1]
    assert full_history == full_history_before
    assert bounded_history[0] == {
        key: value
        for key, value in older_full_record.items()
        if key != "runtime_variants"
    }

    projected_catalog = copy.deepcopy(catalog_152)
    projected_catalog["payload"]["releases"][1].pop("runtime_variants")
    _resign(projected_catalog, private_key)
    projected_size = len(
        (json.dumps(projected_catalog, indent=2) + "\n").encode("utf-8")
    )
    full_size = len((json.dumps(catalog_152, indent=2) + "\n").encode("utf-8"))
    assert projected_size < full_size
    monkeypatch.setattr(builder, "MAX_RUNTIME_CATALOG_BYTES", projected_size)
    bounded = builder.build_runtime_catalog_v3(
        catalog_release=release_152,
        catalog_history=history,
        runtime_bundles=bundles_152,
        expected_source_commit=assets.SOURCE_COMMIT,
        private_key=private_key,
        key_id="test-key",
        publication_time="2026-10-12T19:10:00Z",
    )
    assert "runtime_variants" not in bounded["payload"]["releases"][1]
    assert bounded["payload"]["releases"][1] == projected_v2[0]
    assert (
        len((json.dumps(bounded, indent=2) + "\n").encode("utf-8"))
        <= projected_size
    )
    with pytest.raises(UpdateManifestError, match="no release compatible"):
        read_update_document_bytes(
            json.dumps(bounded).encode("utf-8"),
            public_keys,
            current_version="1.5.0",
            runtime_abi="channelwatch-runtime-v1",
            settings_schema_version=7,
            launcher_protocol=4,
            runtime_platform=runtime_platform,
        )

    stray = copy.deepcopy(history)
    stray[0]["runtime_descriptor"] = descriptor_151
    with pytest.raises(ValueError, match="requires signed platform variants"):
        builder.project_catalog_history_v2(stray)


def test_highest_variantless_protocol_four_release_is_malformed():
    private_key, public_keys = assets._key_pair()
    release = _release("1.5.2")
    payload = {
        "channel": "stable",
        "published_at": "2026-10-12T19:10:00Z",
        "releases": [release],
    }
    catalog = {
        "schema": 3,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": "test-key",
            "value": base64.b64encode(
                private_key.sign(canonical_payload_bytes(payload))
            ).decode("ascii"),
        },
    }
    platform = RuntimePlatform(
        "amd64", "cpython-314", "glibc", (2, 36),
        frozenset({"isolated_dependencies_v1"}),
    )

    with pytest.raises(UpdateManifestError, match="highest protocol-4 release"):
        read_update_document_bytes(
            json.dumps(catalog).encode("utf-8"),
            public_keys,
            current_version="1.5.0",
            runtime_abi="channelwatch-runtime-v1",
            settings_schema_version=7,
            launcher_protocol=4,
            runtime_platform=platform,
        )


@pytest.mark.parametrize("kind", ["image", "legacy"])
def test_variantless_bootstrap_and_legacy_records_keep_their_existing_shape(kind):
    release = assets._image_bootstrap_release() if kind == "image" else _release("1.4.3")
    if kind == "legacy":
        release["updater_protocol"] = 2
        release["compatible_launcher_protocols"] = [3]

    normalized = normalize_runtime_release(
        release,
        platform=None,
        validate_url=lambda value: value,
    )

    assert "runtime_compatible" not in normalized
