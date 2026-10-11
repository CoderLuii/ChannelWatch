from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.runtime_generation import descriptor_generation_id
from core.tests.test_runtime_catalog import _descriptor
from core.update_center import canonical_payload_bytes


ROOT = Path(__file__).resolve().parents[3]
SOURCE_COMMIT = "1" * 40
BOOTSTRAP_INDEX = json.dumps({"schemaVersion": 2, "manifests": [
    {"digest": "sha256:" + "a" * 64, "platform": {"os": "linux", "architecture": architecture}}
    for architecture in ("amd64", "arm64")
]}).encode()
BOOTSTRAP_SHA256 = hashlib.sha256(BOOTSTRAP_INDEX).hexdigest()


def _load_script(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(**overrides):
    return {
        "version": "1.6.0",
        "image_required": False,
        "delivery_mode": "app_update",
        "minimum_image_version": "1.5.0",
        "minimum_launcher_protocol": 4,
        "updater_protocol": 4,
        "compatible_launcher_protocols": [4],
        "runtime_generation_required": True,
        "runtime_bootstrap_sha256": BOOTSTRAP_SHA256,
        "minimum_image_sha256": "sha256:" + BOOTSTRAP_SHA256,
        "automatic_install_allowed": False,
        **overrides,
    }


def _signed_catalog(tmp_path: Path):
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    variants = []
    descriptors = {}
    for architecture in ("amd64", "arm64"):
        descriptor = _descriptor(architecture)
        descriptor["application_version"] = "1.6.0"
        descriptor["source_commit"] = SOURCE_COMMIT
        descriptor["generation_id"] = descriptor_generation_id(descriptor)
        descriptors[architecture] = descriptor
        variants.append(
            {
                "bundle_url": f"https://example.invalid/{architecture}.zip",
                "bundle_sha256": hashlib.sha256(architecture.encode()).hexdigest(),
                "bundle_signature": "signed-runtime-bundle",
                "key_id": "test-key",
                "runtime_descriptor": descriptor,
            }
        )
    payload = {
        "channel": "stable",
        "published_at": "2026-10-10T00:00:00Z",
        "releases": [
            {
                "version": "1.6.0",
                "minimum_image_version": "1.5.0",
                "compatible_launcher_protocols": [4],
                "runtime_variants": variants,
            }
        ],
    }
    catalog = {
        "schema": 3,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": "test-key",
            "value": base64.b64encode(
                private.sign(canonical_payload_bytes(payload))
            ).decode("ascii"),
        },
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path, {"test-key": public}, descriptors


def _evidence(catalog_path: Path, descriptors: dict[str, dict]):
    architectures = {}
    for architecture, descriptor in descriptors.items():
        descriptor_sha256 = hashlib.sha256(
            canonical_payload_bytes(descriptor)
        ).hexdigest()
        architectures[architecture] = {
            "status": "passed",
            "architecture": architecture,
            "source_commit": SOURCE_COMMIT,
            "descriptor_sha256": descriptor_sha256,
            "runtime_generation_id": descriptor["generation_id"],
            "dependency_inventory_sha256": descriptor[
                "dependency_inventory_sha256"
            ],
            "python_implementation": descriptor["python_implementation"],
            "python_version": descriptor["python_version"],
            "python_cache_tag": descriptor["python_cache_tag"],
            "os_family": descriptor["os_family"],
            "libc_family": descriptor["libc_family"],
            "minimum_libc_version": descriptor["minimum_libc_version"],
            "interpreter_smoke": True,
            "native_imports": True,
            "system_libraries": True,
            "persistent_restart": True,
            "rollback": True,
        }
        root = "/config/channelwatch-runtime/releases/" + descriptor["generation_id"]
        imports = {
            name: {"loaded": True, "path": root + "/dependencies/site-packages/" + name + "/__init__.py", "version": "1.0.0"}
            for name in ("requests", "cryptography", "sqlmodel", "uvicorn", "pydantic_core")
        }
        components = {
            component: {
                "runtime_generation_id": descriptor["generation_id"],
                "runtime_descriptor_sha256": descriptor["generation_id"],
                "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
                "process_runtime": {
                    "pid": 123,
                    "python_executable": root + "/environment/bin/python",
                    "python_prefix": root + "/environment",
                    "python_version": descriptor["python_version"],
                    "dependencies": imports,
                },
            }
            for component in ("core", "ui")
        }
        architectures[architecture].update({
            "target_variant_bundle_sha256": hashlib.sha256(architecture.encode()).hexdigest(),
            "qualification": {
                "platform_manifest_digest": "sha256:" + "a" * 64,
                "bootstrap_index_base64": base64.b64encode(BOOTSTRAP_INDEX).decode(),
                "pulled_image_id": "sha256:" + "b" * 64,
                "initial_status": {"launcher_protocol": 4, "current_version": "1.5.0"},
                "check": {"update_available": True, "image_required": False, "catalog_state": "update_available", "trusted_target": {
                    "version": "1.6.0", "runtime_descriptor": copy.deepcopy(descriptor),
                    "bundle_sha256": hashlib.sha256(architecture.encode()).hexdigest(),
                }},
                "apply_job_id": "a" * 32,
                "reapply_job_id": "b" * 32,
                "apply_runtime_components": copy.deepcopy(components),
                "reapply_runtime_components": copy.deepcopy(components),
                "native_imports": imports,
                "supervisor_processes": {component: root + "/environment/bin/python --mode " + component for component in ("core", "ui")},
                "preservation_sha256": {path: "c" * 64 for path in ("/config/settings.json", "/config/encryption.key", "/venv/pyvenv.cfg", "/venv/bin/python")},
                "container_restart": {"status": "passed", "current_version": "1.6.0"},
                "rollback": {"operation": "rollback", "status": "restarting", "rolled_back_from": "1.6.0", "job_id": "c" * 32},
                "rollback_status": {"current_version": "1.5.0", "launcher_protocol": 4, "active_bundle": None, "operation_busy": False},
            },
        })
        qualification = architectures[architecture]["qualification"]
        qualification.update(native_host_architecture=architecture, docker_engine_architecture=architecture,
                             container_identity_before="d" * 64, container_identity_after="d" * 64)
        commands = qualification["supervisor_processes"]
        qualification["supervisor_processes_after_restart"] = copy.deepcopy(commands)
        qualification["supervisor_processes_after_reapply"] = copy.deepcopy(commands)
        preserved = qualification.pop("preservation_sha256")
        for stage in ("before", "after_apply", "after_restart", "after_reapply"):
            qualification["preservation_" + stage + "_sha256"] = copy.deepcopy(preserved)
    return {
        "schema": 2,
        "status": "passed",
        "target_application_version": "1.6.0",
        "source_commit": SOURCE_COMMIT,
        "signed_catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "bootstrap": {
            "status": "passed",
            "minimum_image_version": "1.5.0",
            "launcher_protocol": 4,
            "immutable_bootstrap_sha256": BOOTSTRAP_SHA256,
            "image_index_digest": "sha256:" + BOOTSTRAP_SHA256,
        },
        "architectures": architectures,
        "native_logs_sha256": {name: {"app": "e" * 64, "feed": "f" * 64} for name in architectures},
    }


def _apply(module, tmp_path: Path, *, paths=(), mutate=None):
    catalog_path, public_keys, descriptors = _signed_catalog(tmp_path)
    evidence = _evidence(catalog_path, descriptors)
    if mutate is not None:
        mutate(evidence, catalog_path)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    return module.apply_verified_dependency_runtime(
        module.classify_paths(list(paths)),
        config=_config(),
        catalog_path=catalog_path,
        evidence_path=evidence_path,
        expected_source_commit=SOURCE_COMMIT,
        public_keys=public_keys,
    )


def test_release_version_policy_allows_proven_protocol_four_floor_retention():
    module = _load_script(
        "release_version_policy_capability",
        "scripts/release/release_version_policy.py",
    )

    policy = module.validate_release_config(_config())

    assert policy.image_milestone is False
    assert policy.minimum_image_version == "1.5.0"
    assert policy.retained_capability_floor is True


def test_release_version_policy_preserves_legacy_141_and_rejects_unproven_floor():
    module = _load_script(
        "release_version_policy_historical",
        "scripts/release/release_version_policy.py",
    )
    historical = module.validate_release_config(
        {
            "version": "1.4.1",
            "image_required": False,
            "delivery_mode": "app_update",
            "minimum_image_version": "1.4.0",
        }
    )
    assert historical.minimum_image_version == "1.4.0"
    assert historical.retained_capability_floor is False

    with pytest.raises(ValueError, match="protocol-4 runtime generation"):
        module.validate_release_config(
            _config(runtime_generation_required=False)
        )
    with pytest.raises(ValueError, match="v1.5.0 or newer"):
        module.validate_release_config(
            _config(minimum_image_version="1.4.0")
        )


def _recovery_config(**overrides):
    return {
        "version": "1.5.1",
        "image_required": True,
        "delivery_mode": "image_required",
        "minimum_image_version": "1.5.1",
        "recommended_image_version": "1.5.1",
        "updater_protocol": 4,
        "minimum_launcher_protocol": 4,
        "compatible_launcher_protocols": [4],
        "runtime_generation_required": False,
        "runtime_compatibility_evidence": {},
        "automatic_install_allowed": False,
        "recovers_unpublished_image_milestone": "1.5.0",
        "unpublished_predecessor_tags": ["v1.5.0"],
        "unpublished_release_tags": ["v1.4.2", "v1.4.3", "v1.4.4", "v1.5.0"],
        **overrides,
    }


def test_failed_image_milestone_recovery_is_the_replacement_image_foundation():
    module = _load_script(
        "release_version_policy_image_recovery",
        "scripts/release/release_version_policy.py",
    )

    policy = module.validate_release_config(_recovery_config())

    assert policy.image_milestone is True
    assert policy.minimum_image_version == "1.5.1"
    assert policy.retained_capability_floor is False


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"minimum_image_version": "1.5.0"}, "matching image floor"),
        ({"recommended_image_version": "1.5.0"}, "matching image floor"),
        ({"recovers_unpublished_image_milestone": "1.5.1"}, "X.Y.0 milestone"),
        ({"recovers_unpublished_image_milestone": "1.4.0"}, "same minor release line"),
        ({"unpublished_predecessor_tags": []}, "contiguous unpublished predecessors"),
        (
            {"unpublished_release_tags": ["v1.4.2", "v1.4.3", "v1.4.4"]},
            "public release history",
        ),
        ({"runtime_generation_required": True}, "image bootstrap launcher contract"),
        ({"runtime_bootstrap_sha256": "a" * 64}, "runtime generation fields"),
        ({"minimum_image_sha256": "sha256:" + "a" * 64}, "runtime generation fields"),
        ({"runtime_compatibility_evidence": {"amd64": {}}}, "runtime generation fields"),
        ({"automatic_install_allowed": True}, "automatic in-app installation"),
    ],
)
def test_failed_image_milestone_recovery_fails_closed(mutation, error):
    module = _load_script(
        "release_version_policy_invalid_image_recovery",
        "scripts/release/release_version_policy.py",
    )

    with pytest.raises(ValueError, match=error):
        module.validate_release_config(_recovery_config(**mutation))


def test_failed_image_milestone_recovery_forces_image_delivery_classification():
    policy_module = _load_script(
        "release_version_policy_recovery_classifier",
        "scripts/release/release_version_policy.py",
    )
    classifier = _load_script(
        "classify_release_impact_recovery",
        "scripts/release/classify-release-impact.py",
    )

    policy = policy_module.validate_release_config(_recovery_config())
    result = classifier.apply_release_version_policy(
        classifier.classify_paths([]),
        policy,
    )

    assert result.image_required is True
    assert result.delivery_mode == "image_required"
    assert result.triggering_paths == ("scripts/release/release-config.json",)


@pytest.mark.parametrize("version,floor", [("1.5.1", "1.5.0"), ("1.6.1", "1.6.0")])
def test_future_patch_cannot_revert_to_an_app_only_legacy_launcher(version, floor):
    module = _load_script(
        "release_version_policy_monotonic",
        "scripts/release/release_version_policy.py",
    )
    with pytest.raises(ValueError, match="protocol-4 runtime generation"):
        module.validate_release_config(
            _config(
                version=version,
                minimum_image_version=floor,
                updater_protocol=2,
                minimum_launcher_protocol=3,
                compatible_launcher_protocols=[1, 2, 3],
                runtime_generation_required=False,
            )
        )


def test_first_release_after_recovery_requires_a_bootstrap_digest():
    module = _load_script(
        "release_version_policy_first_dependency_patch",
        "scripts/release/release_version_policy.py",
    )
    with pytest.raises(ValueError, match="bootstrap SHA-256"):
        module.validate_release_config(
            _config(
                version="1.5.2",
                minimum_image_version="1.5.1",
                runtime_bootstrap_sha256=None,
                unpublished_release_tags=["v1.5.0"],
            )
        )


@pytest.mark.parametrize("version", ["1.5.2", "1.6.0"])
def test_future_release_accepts_a_complete_generation_from_the_recovery(version):
    module = _load_script(
        "release_version_policy_complete_dependency_patch",
        "scripts/release/release_version_policy.py",
    )
    policy = module.validate_release_config(
        _config(
            version=version,
            minimum_image_version="1.5.1",
            unpublished_release_tags=["v1.5.0"],
        )
    )
    assert policy.image_milestone is False
    assert policy.minimum_image_version == "1.5.1"
    with pytest.raises(ValueError, match="same major release line"):
        module.validate_release_config(
            _config(version="2.0.0", minimum_image_version="1.5.0")
        )
    with pytest.raises(ValueError, match="bootstrap SHA-256"):
        module.validate_release_config(
            _config(runtime_bootstrap_sha256="0" * 63)
        )


def test_retained_floor_must_be_older_published_and_canonical():
    module = _load_script(
        "release_version_policy_retained_floor_identity",
        "scripts/release/release_version_policy.py",
    )

    with pytest.raises(ValueError, match="strictly older"):
        module.validate_release_config(
            _config(version="1.5.2", minimum_image_version="1.5.2")
        )
    with pytest.raises(ValueError, match="published image foundation"):
        module.validate_release_config(
            _config(
                version="1.5.2",
                minimum_image_version="1.5.1",
                unpublished_release_tags=["v1.5.0", "v1.5.1"],
            )
        )
    with pytest.raises(ValueError, match="canonical numeric"):
        module.validate_release_config(
            _config(version="1.5.2", minimum_image_version="1.5.01")
        )


@pytest.mark.parametrize(
    "version,floor",
    [("1.5.2", "1.5.0"), ("1.6.0", "1.5.0")],
)
def test_in_app_generation_cannot_use_an_unpublished_image_floor(version, floor):
    module = _load_script(
        "release_version_policy_unpublished_floor",
        "scripts/release/release_version_policy.py",
    )

    with pytest.raises(ValueError, match="published image foundation"):
        module.validate_release_config(
            _config(
                version=version,
                minimum_image_version=floor,
                unpublished_release_tags=[f"v{floor}"],
            )
        )


def test_dependency_bootstrap_digest_matches_the_pinned_minimum_image():
    module = _load_script(
        "release_version_policy_bootstrap_identity",
        "scripts/release/release_version_policy.py",
    )
    with pytest.raises(ValueError, match="minimum image index digest"):
        module.validate_release_config(_config(minimum_image_sha256="sha256:" + "e" * 64))


def test_protocol_four_app_release_missing_evidence_is_image_required(tmp_path: Path):
    module = _load_script(
        "classify_release_capability_missing",
        "scripts/release/classify-release-impact.py",
    )

    result = module.apply_verified_dependency_runtime(
        module.classify_paths(["app/core/main.py"]),
        config=_config(),
        catalog_path=None,
        evidence_path=None,
        expected_source_commit=SOURCE_COMMIT,
    )

    assert result.delivery_mode == "image_required"
    assert module.RUNTIME_CAPABILITY_EVIDENCE_TRIGGER in result.triggering_paths


def test_unsupported_legacy_bootstrap_floor_is_image_required(tmp_path: Path):
    module = _load_script(
        "classify_release_capability_legacy_floor",
        "scripts/release/classify-release-impact.py",
    )
    catalog_path, public_keys, descriptors = _signed_catalog(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(_evidence(catalog_path, descriptors)), encoding="utf-8"
    )

    result = module.apply_verified_dependency_runtime(
        module.classify_paths(["app/core/main.py"]),
        config=_config(minimum_image_version="1.4.0"),
        catalog_path=catalog_path,
        evidence_path=evidence_path,
        expected_source_commit=SOURCE_COMMIT,
        public_keys=public_keys,
    )

    assert result.delivery_mode == "image_required"
    assert module.RUNTIME_CAPABILITY_EVIDENCE_TRIGGER in result.triggering_paths


def test_compatible_signed_amd64_arm64_runtime_evidence_allows_app_release(
    tmp_path: Path,
):
    module = _load_script(
        "classify_release_capability_valid",
        "scripts/release/classify-release-impact.py",
    )

    result = _apply(
        module,
        tmp_path,
        paths=("deploy/requirements/runtime.txt", "app/core/main.py"),
    )

    assert result.delivery_mode == "app_update"
    assert result.image_required is False
    assert result.triggering_paths == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence, _catalog: evidence.update({"schema": 1}),
        lambda evidence, _catalog: evidence["native_logs_sha256"]["amd64"].pop("feed"),
        lambda evidence, _catalog: evidence["native_logs_sha256"]["amd64"].pop("app"),
        lambda evidence, _catalog: evidence["native_logs_sha256"]["amd64"].update(extra="e" * 64),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(check={}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["check"]["trusted_target"].update(version="1.7.0"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["check"]["trusted_target"].update(bundle_sha256="0" * 64),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(rollback={}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].pop("rollback_status"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_status"].update(current_version="1.6.0"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_status"].update(active_bundle={"runtime_generation_id": "0" * 64}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(bootstrap_index_base64=base64.b64encode(b"{}").decode()),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(platform_manifest_digest="sha256:" + "0" * 64),
        lambda evidence, _catalog: evidence["architectures"]["amd64"].pop("qualification"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["apply_runtime_components"]["core"]["process_runtime"].update({"python_executable": "/venv/bin/python"}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["apply_runtime_components"]["ui"]["process_runtime"]["dependencies"]["cryptography"].update({"path": "/venv/lib/python/site-packages/cryptography/__init__.py"}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["preservation_before_sha256"].pop("/config/encryption.key"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(native_host_architecture="arm64"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["preservation_after_restart_sha256"].update({"/config/settings.json": "0" * 64}),
        lambda evidence, _catalog: evidence["architectures"]["arm64"].update({"target_variant_bundle_sha256": "0" * 64}),
        lambda evidence, _catalog: evidence["architectures"].pop("arm64"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"].update(
            {"python_cache_tag": "cpython-999"}
        ),
        lambda evidence, _catalog: evidence["architectures"]["arm64"].update(
            {"native_imports": False}
        ),
        lambda evidence, _catalog: evidence["architectures"]["amd64"].update(
            {"system_libraries": False}
        ),
        lambda evidence, _catalog: evidence["bootstrap"].update(
            {"minimum_image_version": "1.4.0"}
        ),
        lambda evidence, _catalog: evidence.update({"source_commit": "2" * 40}),
        lambda evidence, _catalog: evidence.update(
            {"signed_catalog_sha256": "0" * 64}
        ),
    ],
)
def test_incomplete_or_incompatible_capability_evidence_requires_image(
    tmp_path: Path, mutate
):
    module = _load_script(
        "classify_release_capability_invalid",
        "scripts/release/classify-release-impact.py",
    )

    result = _apply(
        module,
        tmp_path,
        paths=("deploy/requirements/runtime.txt",),
        mutate=mutate,
    )

    assert result.delivery_mode == "image_required"
    assert module.RUNTIME_CAPABILITY_EVIDENCE_TRIGGER in result.triggering_paths


def test_signed_catalog_tampering_requires_image(tmp_path: Path):
    module = _load_script(
        "classify_release_capability_forged_catalog",
        "scripts/release/classify-release-impact.py",
    )

    def mutate(_evidence, catalog_path):
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["payload"]["releases"][0]["runtime_variants"][0][
            "bundle_sha256"
        ] = "0" * 64
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    result = _apply(
        module,
        tmp_path,
        paths=("deploy/requirements/runtime.txt",),
        mutate=mutate,
    )

    assert result.delivery_mode == "image_required"


@pytest.mark.parametrize(
    "path",
    [
        "app/core/runtime_image_bootstrap.py",
        "app/core/runtime_release_trust.py",
        "app/core/runtime_generation.py",
        "app/core/runtime_dependency_bridge.py",
        "app/core/runtime_worker.py",
    ],
)
def test_image_owned_runtime_surfaces_remain_image_required_with_valid_evidence(
    tmp_path: Path, path: str
):
    module = _load_script(
        "classify_release_capability_image_owned",
        "scripts/release/classify-release-impact.py",
    )

    result = _apply(module, tmp_path, paths=(path,))

    assert result.delivery_mode == "image_required"
    assert result.triggering_paths == (path,)
