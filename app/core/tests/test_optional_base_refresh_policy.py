from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load():
    spec = importlib.util.spec_from_file_location("optional_base_policy", ROOT / "scripts/release/classify-release-impact.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dockerfiles():
    before = "\n".join([
        "FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:" + "a" * 64 + " AS ui-builder",
        "RUN build-ui",
        "FROM python:3.14-slim-bookworm@sha256:" + "b" * 64 + " AS python-deps",
        "RUN install-dependencies",
        "FROM python:3.14-slim-bookworm@sha256:" + "b" * 64 + " AS runtime",
        "ARG VERSION=1.6.0",
    ])
    return before, before.replace("a" * 64, "c" * 64).replace("b" * 64, "d" * 64).replace("VERSION=1.6.0", "VERSION=1.6.1")


@pytest.fixture
def release_161_config():
    return {
        "version": "1.6.1",
        "minimum_image_version": "1.5.3",
        "image_required": False,
        "delivery_mode": "app_update_with_image_refresh",
        "compatible_source_application_versions": ["1.5.3", "1.6.0"],
        "legacy_app_update_sources": ["1.4.0", "1.4.1", "1.4.5", "1.6.0"],
    }


def test_only_immutable_same_line_base_digests_can_be_optional():
    tool = load()
    before, after = dockerfiles()
    assert tool.optional_base_digest_refresh(before, after)
    assert not tool.optional_base_digest_refresh(before, before)
    for mutation in (
        after + "\nRUN curl unreviewed-script | sh",
        after.replace("python:3.14", "python:3.15"),
        after.replace("node:24", "node:25"),
        after.replace("slim-bookworm", "slim-trixie"),
        after.replace("sha256:" + "d" * 64, "latest", 1),
        after.replace("d" * 64, "e" * 64, 1),
        after.replace("RUN build-ui", "RUN different-build"),
    ):
        assert not tool.optional_base_digest_refresh(before, mutation)


def test_current_dockerfile_final_stage_needs_no_alias_for_base_refresh():
    tool = load()
    after = (ROOT / "deploy/docker/Dockerfile").read_text()
    import re
    before = re.sub(r"@sha256:[0-9a-f]{64}", "@sha256:" + "a" * 64, after)
    assert tool.optional_base_digest_refresh(before, after)


@pytest.fixture
def native_image_proof(tmp_path):
    archive = tmp_path / "image.oci.tar"
    archive.write_bytes(b"sealed image fixture")
    descriptor = {"schema": 1, "image_index": {"digest": "sha256:" + "a" * 64}, "platforms": [
        {"platform": "linux/" + arch, "manifest": {"digest": "sha256:" + char * 64}, "config": {"digest": "sha256:" + char.upper().lower() * 64}}
        for arch, char in (("amd64", "b"), ("arm64", "c"))
    ]}
    descriptor_path = tmp_path / "image.json"
    descriptor_path.write_text(json.dumps(descriptor))
    for platform in descriptor["platforms"]:
        arch = platform["platform"].split("/")[1]
        log = tmp_path / f"native-image-{arch}.log"
        log.write_bytes(b"native image log\n")
        receipt = {
            "schema": 1, "status": "passed", "native": True, "architecture": arch,
            "source_commit": "f" * 40, "version": "1.6.1",
            "oci_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "descriptor_sha256": hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
            "image_index_digest": descriptor["image_index"]["digest"],
            "platform_manifest_digest": platform["manifest"]["digest"],
            "config_digest": platform["config"]["digest"],
            "container_log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "python_version": "3.14.9", "launcher_protocol": 4,
            **{key: True for key in ("runtime_ready", "cli", "headless_browser", "same_container_restart", "persistence")},
            "settings_key_preserved": {"/config/settings.json": "1" * 64, "/config/encryption.key": "2" * 64},
        }
        (tmp_path / f"native-image-{arch}.json").write_text(json.dumps(receipt))
    return tmp_path, archive, descriptor_path


@pytest.mark.parametrize("mutation", [None, "missing", "log", "archive", "source", "architecture", "python", "pending"])
def test_final_native_image_proof_rejects_missing_tampered_or_wrong_bindings(native_image_proof, mutation):
    tool = load()
    directory, archive, descriptor = native_image_proof
    receipt_path = directory / "native-image-arm64.json"
    receipt = json.loads(receipt_path.read_text())
    if mutation == "missing":
        receipt_path.unlink()
    elif mutation == "log":
        (directory / "native-image-arm64.log").write_bytes(b"changed")
    elif mutation == "archive":
        archive.write_bytes(b"changed")
    elif mutation in {"source", "architecture", "python", "pending"}:
        key, value = {"source": ("source_commit", "e" * 40), "architecture": ("architecture", "amd64"), "python": ("python_version", "3.14.8"), "pending": ("status", "pending_native_image")}[mutation]
        receipt[key] = value
        receipt_path.write_text(json.dumps(receipt))
    assert tool.verify_optional_native_image_proof(directory, archive, descriptor, version="1.6.1", source_commit="f" * 40) is (mutation is None)


@pytest.mark.parametrize("phase,legacy,runtime,native,expected_pending,expected_required", [
    (False, True, True, False, False, True),
    (False, True, True, True, False, False),
    (True, True, True, False, True, False),
    (True, False, True, False, False, True),
    (True, True, False, False, False, True),
])
def test_pre_image_phase_is_explicit_and_final_phase_requires_native_proof(monkeypatch, release_161_config, phase, legacy, runtime, native, expected_pending, expected_required):
    tool = load()
    before, after = dockerfiles()
    config = release_161_config
    args = SimpleNamespace(pre_image_phase=phase, runtime_catalog=Path("catalog"), runtime_capability_evidence=Path("runtime"), legacy_app_receipts=Path("legacy"), legacy_app_catalog=Path("app-catalog"), legacy_app_bundle=Path("app-bundle"), config=ROOT / "scripts/release/release-config.json", native_image_evidence=Path("native"), candidate_image_archive=Path("archive"), candidate_image_descriptor=Path("descriptor"))
    descriptors = {arch: {"python_version": "3.14.8", "python_cache_tag": "cpython-314", "minimum_libc_version": [2, 36]} for arch in ("amd64", "arm64")}
    monkeypatch.setattr(tool, "_signed_protocol_four_runtime_variants", lambda **kwargs: (b"signed", descriptors, {}) if runtime else None)
    monkeypatch.setattr(tool, "_read_protocol_four_runtime_evidence", lambda **kwargs: runtime)
    monkeypatch.setattr(tool, "verify_optional_legacy_proof", lambda **kwargs: legacy)
    monkeypatch.setattr(tool, "verify_optional_native_image_proof", lambda *a, **kw: native)
    result, pending = tool.apply_optional_base_refresh(tool._impact({"deploy/docker/Dockerfile"}, set()), before={"deploy/docker/Dockerfile": before}, after={"deploy/docker/Dockerfile": after}, config=config, args=args, source_commit="f" * 40, public_keys=None)
    assert result.image_required is expected_required
    assert pending is expected_pending
    if not expected_required:
        assert result.delivery_mode == "app_update_with_image_refresh"


@pytest.mark.parametrize("raised_floor", ["python", "image", "sources", "key"])
def test_optional_refresh_never_raises_the_retained_floor_or_changes_trust(monkeypatch, release_161_config, raised_floor):
    tool = load()
    before, after = dockerfiles()
    config = release_161_config
    args = SimpleNamespace(pre_image_phase=True, runtime_catalog=Path("catalog"), runtime_capability_evidence=Path("runtime"))
    descriptor = {"python_version": "3.14.9" if raised_floor == "python" else "3.14.8", "python_cache_tag": "cpython-314", "minimum_libc_version": [2, 36]}
    monkeypatch.setattr(tool, "_signed_protocol_four_runtime_variants", lambda **kwargs: (b"signed", {arch: descriptor for arch in ("amd64", "arm64")}, {}))
    if raised_floor == "image":
        config["minimum_image_version"] = "1.6.0"
    if raised_floor == "sources":
        config["legacy_app_update_sources"] = ["1.4.1", "1.4.5", "1.6.0"]
    result, pending = tool.apply_optional_base_refresh(tool._impact({"deploy/docker/Dockerfile"}, set()), before={"deploy/docker/Dockerfile": before}, after={"deploy/docker/Dockerfile": after}, config=config, args=args, source_commit="f" * 40, public_keys={} if raised_floor == "key" else None)
    assert result.image_required is True
    assert pending is False


@pytest.mark.parametrize("version", ["1.6.2", "1.6.3"])
def test_optional_base_refresh_exception_does_not_extend_to_new_releases(monkeypatch, release_161_config, version):
    tool = load()
    before, after = dockerfiles()
    config = {**release_161_config, "version": version}
    args = SimpleNamespace(pre_image_phase=True, runtime_catalog=Path("catalog"), runtime_capability_evidence=Path("runtime"))
    descriptors = {arch: {"python_version": "3.14.8", "python_cache_tag": "cpython-314", "minimum_libc_version": [2, 36]} for arch in ("amd64", "arm64")}
    monkeypatch.setattr(tool, "_signed_protocol_four_runtime_variants", lambda **kwargs: (b"signed", descriptors, {}))
    monkeypatch.setattr(tool, "_read_protocol_four_runtime_evidence", lambda **kwargs: True)
    monkeypatch.setattr(tool, "verify_optional_legacy_proof", lambda **kwargs: True)
    monkeypatch.setattr(tool, "verify_optional_native_image_proof", lambda *args, **kwargs: True)
    result, pending = tool.apply_optional_base_refresh(
        tool._impact({"deploy/docker/Dockerfile"}, set()),
        before={"deploy/docker/Dockerfile": before}, after={"deploy/docker/Dockerfile": after},
        config=config, args=args, source_commit="f" * 40, public_keys=None,
    )
    assert result.image_required is True
    assert pending is False


def test_publishing_classifier_never_uses_the_pending_phase():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())
    candidate = str(workflow["jobs"]["candidate-qualification"])
    final = str(workflow["jobs"]["build-update-bundle-and-release"])
    assert "--pre-image-phase" in candidate
    assert "--pre-image-phase" not in final
    for flag in ("--native-image-evidence", "--candidate-image-archive", "--candidate-image-descriptor", "--legacy-app-receipts"):
        assert flag in final


def test_modern_optional_image_advice_cannot_change_the_legacy_projection():
    path = ROOT / "scripts/release/build-update-bundle.py"
    spec = importlib.util.spec_from_file_location("optional_image_projection", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    from core.tests.test_runtime_release_assets import _release_161
    canonical = _release_161()
    canonical.update(delivery_mode="app_update_with_image_refresh", image_refresh_recommended=True)
    original = copy.deepcopy(canonical)
    projected = builder.project_current_release_v2(canonical, ["1.4.0", "1.4.1", "1.4.5", "1.6.0"])
    assert canonical == original
    assert projected["delivery_mode"] == "app_update"
    assert projected["image_refresh_recommended"] is False
    assert projected["image_required"] is False
    spec = importlib.util.spec_from_file_location("optional_image_asset_binding", ROOT / "scripts/release/verify-update-assets.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    app_catalog = {"payload": {"channel": "stable", "published_at": "now", "releases": [projected]}}
    runtime_catalog = {"payload": {"channel": "stable", "published_at": "now", "releases": [canonical]}}
    verifier.verify_runtime_catalog_binding(app_catalog, runtime_catalog, expected_version="1.6.1", legacy_app_update_sources=["1.4.0", "1.4.1", "1.4.5", "1.6.0"])
    canonical["image_refresh_recommended"] = False
    with pytest.raises(verifier.UpdateBundleError, match="protocol-4 release contract"):
        verifier.verify_runtime_catalog_binding(app_catalog, runtime_catalog, expected_version="1.6.1", legacy_app_update_sources=["1.4.0", "1.4.1", "1.4.5", "1.6.0"])
