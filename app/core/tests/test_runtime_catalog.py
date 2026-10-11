import base64
import hashlib
import json

import pytest

from core.tests.test_update_manager import _bundle, _key_pair
from core.tests.test_update_v0918 import _catalog
from core.update_center import (
    UpdateManifestError,
    canonical_payload_bytes,
    read_update_document_bytes,
)


def _descriptor(architecture="amd64"):
    descriptor = {
        "schema": 1,
        "application_version": "1.5.1",
        "source_commit": "a" * 40,
        "dependency_inventory_sha256": "b" * 64,
        "bundle_format": "channelwatch-runtime-generation-v1",
        "required_launcher_capability": "isolated_dependencies_v1",
        "settings_schema_version": 7,
        "architecture": architecture,
        "python_implementation": "cpython",
        "python_cache_tag": "cpython-314",
        "python_version": "3.14.0",
        "os_family": "linux",
        "libc_family": "glibc",
        "minimum_libc_version": [2, 36],
        "dependency_inventory_path": "dependencies/inventory.json",
        "files": [
            {"path": "core/main.py", "sha256": "c" * 64, "mode": "0644", "size": 1},
            {"path": "dependencies/site-packages/package.py", "sha256": "e" * 64, "mode": "0644", "size": 1},
            {"path": "ui/backend/main.py", "sha256": "d" * 64, "mode": "0644", "size": 1},
        ],
        "expanded_size": 3,
    }
    descriptor["generation_id"] = hashlib.sha256(canonical_payload_bytes(descriptor)).hexdigest()
    return descriptor


def _runtime_catalog(private, architectures=("arm64", "amd64")):
    raw = json.loads(_catalog(private, _bundle("1.5.1"), version="1.5.1", sources=["1.5.0"]))
    raw["schema"] = 3
    release = raw["payload"]["releases"][0]
    release["minimum_image_version"] = "1.5.0"
    release["updater_protocol"] = 4
    release["compatible_launcher_protocols"] = [4]
    release["runtime_variants"] = [
        {
            "bundle_url": f"https://github.com/CoderLuii/ChannelWatch/releases/download/v1.5.1/{arch}.zip",
            "bundle_sha256": hashlib.sha256(arch.encode()).hexdigest(),
            "bundle_signature": release["bundle_signature"],
            "key_id": release["key_id"],
            "runtime_descriptor": _descriptor(arch),
        }
        for arch in architectures
    ]
    _resign(raw, private)
    return raw


def _resign(raw, private):
    raw["signature"]["value"] = base64.b64encode(private.sign(canonical_payload_bytes(raw["payload"]))).decode()


def _read(raw, public, architecture="amd64", capabilities=frozenset({"isolated_dependencies_v1"})):
    from core.runtime_generation import RuntimePlatform
    return read_update_document_bytes(
        json.dumps(raw).encode(), public, current_version="1.5.0",
        runtime_abi="channelwatch-runtime-v1", settings_schema_version=7,
        launcher_protocol=4,
        runtime_platform=RuntimePlatform(architecture, "cpython-314", "glibc", (2, 36), capabilities),
    )


def test_signed_catalog_selects_exact_platform_variant():
    private, public = _key_pair()
    raw = _runtime_catalog(private)
    result = _read(raw, public)
    assert result["schema"] == 3
    assert result["payload"]["runtime_descriptor"]["architecture"] == "amd64"
    assert result["payload"]["bundle_url"].endswith("/amd64.zip")
    assert result["payload"]["bundle_sha256"] == hashlib.sha256(b"amd64").hexdigest()


def test_verified_dependency_launcher_rejects_legacy_updater_protocol():
    private, public = _key_pair()
    raw = _runtime_catalog(private)
    raw["payload"]["releases"][0]["updater_protocol"] = 2
    _resign(raw, private)

    with pytest.raises(UpdateManifestError, match="compatible"):
        _read(raw, public)


def test_legacy_launcher_rejects_protocol_four_app_only_release():
    private, public = _key_pair()
    raw = json.loads(_catalog(
        private, _bundle("1.4.2"), version="1.4.2", sources=["1.4.1"],
    ))
    release = raw["payload"]["releases"][0]
    release["updater_protocol"] = 4
    release["compatible_launcher_protocols"] = [3]
    _resign(raw, private)

    with pytest.raises(UpdateManifestError, match="compatible"):
        read_update_document_bytes(
            json.dumps(raw).encode(), public, current_version="1.4.1",
            runtime_abi="channelwatch-runtime-v1", settings_schema_version=7,
            launcher_protocol=3,
        )


@pytest.mark.parametrize("schema,protocol,accepted", [(3, 4, True), (3, 3, False), (2, 4, False)])
def test_large_runtime_catalog_has_separate_bounded_contract(schema, protocol, accepted):
    from core.runtime_generation import RuntimePlatform
    from core.update_center import MAX_MANIFEST_BYTES

    private, public = _key_pair()
    raw = _runtime_catalog(private)
    raw["schema"] = schema
    data = json.dumps(raw).encode().ljust(MAX_MANIFEST_BYTES + 128, b" ")
    kwargs = dict(
        current_version="1.5.0", runtime_abi="channelwatch-runtime-v1",
        settings_schema_version=7, launcher_protocol=protocol,
        runtime_platform=RuntimePlatform(
            "amd64", "cpython-314", "glibc", (2, 36),
            frozenset({"isolated_dependencies_v1"}),
        ),
    )
    if accepted:
        assert read_update_document_bytes(data, public, **kwargs)["schema"] == 3
    else:
        with pytest.raises(UpdateManifestError, match="size limit"):
            read_update_document_bytes(data, public, **kwargs)


def test_runtime_catalog_over_four_mib_rejected_before_json_decode():
    from core.update_center import MAX_RUNTIME_CATALOG_BYTES

    with pytest.raises(UpdateManifestError, match="size limit"):
        read_update_document_bytes(
            b" " * (MAX_RUNTIME_CATALOG_BYTES + 1), {}, current_version="1.5.0",
            runtime_abi="channelwatch-runtime-v1", settings_schema_version=7,
            launcher_protocol=4,
        )


def test_catalog_variant_tampering_is_rejected_before_selection():
    private, public = _key_pair()
    raw = _runtime_catalog(private)
    raw["payload"]["releases"][0]["runtime_variants"][0]["bundle_sha256"] = "f" * 64
    with pytest.raises(UpdateManifestError, match="signature"):
        _read(raw, public)


@pytest.mark.parametrize("architecture,capabilities", [
    ("riscv64", frozenset({"isolated_dependencies_v1"})),
    ("amd64", frozenset()),
])
def test_unsupported_runtime_does_not_become_installable(architecture, capabilities):
    private, public = _key_pair()
    with pytest.raises(UpdateManifestError, match="compatible"):
        _read(_runtime_catalog(private), public, architecture, capabilities)


def test_duplicate_platform_variants_are_rejected():
    private, public = _key_pair()
    with pytest.raises(UpdateManifestError, match="duplicate"):
        _read(_runtime_catalog(private, ("amd64", "amd64")), public)


def test_dependency_variants_cannot_hide_in_legacy_schema_two():
    private, public = _key_pair()
    raw = _runtime_catalog(private)
    raw["schema"] = 2
    with pytest.raises(UpdateManifestError, match="schema"):
        _read(raw, public)


def test_future_python_variant_does_not_hide_a_compatible_release():
    private, public = _key_pair()
    raw = _runtime_catalog(private)
    descriptor = raw["payload"]["releases"][0]["runtime_variants"][0]["runtime_descriptor"]
    descriptor["python_version"] = "3.99.0"
    from core.runtime_generation import descriptor_generation_id
    descriptor["generation_id"] = descriptor_generation_id(descriptor)
    _resign(raw, private)
    assert _read(raw, public)["payload"]["runtime_descriptor"]["architecture"] == "amd64"


@pytest.mark.parametrize("mutation", [
    {"unexpected_capability": "ignore-me"},
    {"architecture": ["amd64"]},
    {"minimum_libc_version": "2.36"},
    {"generation_id": "f" * 64},
])
def test_malformed_signed_foreign_variant_is_rejected(mutation):
    private, public = _key_pair()
    raw = _runtime_catalog(private)
    descriptor = raw["payload"]["releases"][0]["runtime_variants"][0]["runtime_descriptor"]
    descriptor.update(mutation)
    _resign(raw, private)
    with pytest.raises(UpdateManifestError):
        _read(raw, public)
