from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.tests.test_update_manager import _key_pair
from core.update_center import canonical_payload_bytes


def _descriptor(generation: Path) -> dict:
    files = []
    for relative in (
        "core/main.py",
        "core/runtime_dependency_bridge.py",
        "core/runtime_generation.py",
        "core/runtime_worker.py",
        "dependencies/site-packages/example_package/__init__.py",
        "ui/backend/main.py",
    ):
        path = generation / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n", encoding="utf-8")
        payload = path.read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "mode": "0644",
            }
        )
    inventory = b"{}"
    inventory_path = generation / "dependencies/inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_bytes(inventory)
    descriptor = {
        "schema": 1,
        "application_version": "1.5.0",
        "source_commit": "a" * 40,
        "dependency_inventory_sha256": hashlib.sha256(inventory).hexdigest(),
        "bundle_format": "channelwatch-runtime-generation-v1",
        "required_launcher_capability": "isolated_dependencies_v1",
        "settings_schema_version": 7,
        "architecture": "amd64",
        "python_implementation": "cpython",
        "python_cache_tag": sys.implementation.cache_tag,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.0",
        "os_family": "linux",
        "libc_family": "glibc",
        "minimum_libc_version": [2, 36],
        "dependency_inventory_path": "dependencies/inventory.json",
        "files": sorted(files, key=lambda item: item["path"]),
        "expanded_size": sum(item["size"] for item in files) + len(inventory),
    }
    descriptor["generation_id"] = hashlib.sha256(
        canonical_payload_bytes(descriptor)
    ).hexdigest()
    return descriptor


def _signed_catalog(private, descriptor: dict) -> bytes:
    bundle_digest = hashlib.sha256(b"runtime bundle").digest()
    alternate_descriptor = copy.deepcopy(descriptor)
    alternate_descriptor["architecture"] = "arm64"
    alternate_descriptor["generation_id"] = hashlib.sha256(
        canonical_payload_bytes(
            {
                key: value
                for key, value in alternate_descriptor.items()
                if key != "generation_id"
            }
        )
    ).hexdigest()
    alternate_digest = hashlib.sha256(b"arm64 runtime bundle").digest()
    release = {
        "version": "1.5.0",
        "version_tag": "v1.5.0",
        "delivery_mode": "app_update",
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "bundle_url": (
            "https://github.com/CoderLuii/ChannelWatch/releases/download/"
            "v1.5.0/channelwatch-runtime-amd64.zip"
        ),
        "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v1.5.0",
        "bundle_sha256": bundle_digest.hex(),
        "bundle_signature": base64.b64encode(private.sign(bundle_digest)).decode(),
        "key_id": "test-key",
        "updater_protocol": 4,
        "automatic_install_allowed": True,
        "automatic_install_after": "2026-10-12T00:00:00Z",
        "recovery_compatible": True,
        "compatible_source_application_versions": ["1.4.4"],
        "compatible_runtime_abis": ["channelwatch-runtime-v1"],
        "compatible_settings_schema_versions": [7],
        "compatible_launcher_protocols": [4],
        "recommended_image_version": "1.5.0",
        "revocation_state": "active",
        "publication_time": "2026-10-11T00:00:00Z",
        "highlights": ["Runtime dependencies"],
        "runtime_variants": [
            {
                "bundle_url": (
                    "https://github.com/CoderLuii/ChannelWatch/releases/download/"
                    "v1.5.0/channelwatch-runtime-amd64.zip"
                ),
                "bundle_sha256": bundle_digest.hex(),
                "bundle_signature": base64.b64encode(
                    private.sign(bundle_digest)
                ).decode(),
                "key_id": "test-key",
                "runtime_descriptor": descriptor,
            },
            {
                "bundle_url": (
                    "https://github.com/CoderLuii/ChannelWatch/releases/download/"
                    "v1.5.0/channelwatch-runtime-arm64.zip"
                ),
                "bundle_sha256": alternate_digest.hex(),
                "bundle_signature": base64.b64encode(
                    private.sign(alternate_digest)
                ).decode(),
                "key_id": "test-key",
                "runtime_descriptor": alternate_descriptor,
            },
        ],
    }
    payload = {
        "channel": "stable",
        "published_at": "2026-10-11T00:00:00Z",
        "releases": [release],
    }
    return json.dumps(
        {
            "schema": 3,
            "payload": payload,
            "signature": {
                "alg": "ed25519",
                "key_id": "test-key",
                "value": base64.b64encode(
                    private.sign(canonical_payload_bytes(payload))
                ).decode(),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _runtime_state(tmp_path: Path, monkeypatch):
    from core import runtime_image_bootstrap
    from core.runtime_generation import RuntimeGeneration, RuntimePlatform

    runtime_dir = tmp_path / "channelwatch-runtime"
    trust_dir = runtime_dir / "trust"
    trust_dir.mkdir(parents=True)
    staging = tmp_path / "staging"
    descriptor = _descriptor(staging)
    generation = runtime_dir / "releases" / descriptor["generation_id"]
    generation.parent.mkdir()
    staging.rename(generation)
    (generation / "channelwatch-bundle.json").write_text(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    (generation / "prepared.json").write_text(
        json.dumps(
            {
                "generation_id": descriptor["generation_id"],
                "python_executable": "environment/bin/python",
                "dependency_digest": descriptor["dependency_inventory_sha256"],
            }
        ),
        encoding="utf-8",
    )
    private, public = _key_pair()
    catalog = _signed_catalog(private, descriptor)
    trust_path = trust_dir / f"{descriptor['generation_id']}.json"
    trust_path.write_bytes(catalog)
    active = {
        "version": "1.5.0",
        "activation_id": "activation-1",
        "activation_protocol": 4,
        "path": str(generation),
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "runtime_generation_id": descriptor["generation_id"],
        "runtime_descriptor_sha256": descriptor["generation_id"],
        "dependency_inventory_sha256": descriptor[
            "dependency_inventory_sha256"
        ],
        "runtime_trust_sha256": hashlib.sha256(catalog).hexdigest(),
        "manifest": {
            "bundle_sha256": hashlib.sha256(b"runtime bundle").hexdigest(),
            "key_id": "test-key",
        },
    }
    (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")
    verified = RuntimeGeneration(
        descriptor["generation_id"],
        generation,
        generation / "environment/bin/python",
        descriptor["dependency_inventory_sha256"],
    )
    monkeypatch.setattr(runtime_image_bootstrap, "UPDATE_PUBLIC_KEYS", public)
    monkeypatch.setattr(
        runtime_image_bootstrap, "_assert_image_authorities", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_image_bootstrap,
        "detect_runtime_platform",
        lambda **_kwargs: RuntimePlatform(
            "amd64",
            sys.implementation.cache_tag,
            "glibc",
            (2, 36),
            frozenset({"isolated_dependencies_v1"}),
        ),
    )
    monkeypatch.setattr(
        runtime_image_bootstrap,
        "verify_runtime_generation",
        lambda value: value == verified or (_ for _ in ()).throw(AssertionError(value)),
    )
    return runtime_image_bootstrap, runtime_dir, generation, active, trust_path


def test_signed_active_generation_is_verified_and_supervised_without_importing_it(
    tmp_path: Path, monkeypatch
):
    module, runtime_dir, generation, _active, _trust_path = _runtime_state(
        tmp_path, monkeypatch
    )
    marker = tmp_path / "generation-imported"
    (generation / "core/__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        module, "supervise_image_worker", lambda **kwargs: calls.append(kwargs)
    )

    assert module.maybe_supervise(
        image_app_dir=tmp_path / "image",
        runtime_dir=runtime_dir,
        app_dir=generation,
        launcher_protocol=4,
        mode="core",
        app_args=("--stay-alive",),
    )

    assert not marker.exists()
    assert calls[0]["generation"] == generation.resolve()
    assert calls[0]["image_app_dir"] == (tmp_path / "image").resolve()
    assert calls[0]["mode"] == "core"


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("trust-bytes", "trust"),
        ("descriptor", "descriptor"),
        ("active-identity", "identity"),
        ("activation-protocol", "activation"),
        ("wrong-version", "compatible|version"),
        ("wrong-variant", "compatible"),
    ],
)
def test_dependency_startup_fails_closed_for_tampered_or_mismatched_state(
    tmp_path: Path, monkeypatch, mutation: str, match: str
):
    module, runtime_dir, generation, active, trust_path = _runtime_state(
        tmp_path, monkeypatch
    )
    if mutation == "trust-bytes":
        trust_path.write_bytes(trust_path.read_bytes() + b" ")
    elif mutation == "descriptor":
        (generation / "channelwatch-bundle.json").write_text("{}", encoding="utf-8")
    elif mutation == "active-identity":
        active["dependency_inventory_sha256"] = "f" * 64
        (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")
    elif mutation == "activation-protocol":
        active["activation_protocol"] = 3
        (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")
    else:
        raw = json.loads(trust_path.read_bytes())
        release = raw["payload"]["releases"][0]
        if mutation == "wrong-version":
            release["version"] = "1.5.1"
            release["version_tag"] = "v1.5.1"
            for variant in release["runtime_variants"]:
                changed = variant["runtime_descriptor"]
                changed["application_version"] = "1.5.1"
                changed["generation_id"] = hashlib.sha256(
                    canonical_payload_bytes(
                        {
                            key: value
                            for key, value in changed.items()
                            if key != "generation_id"
                        }
                    )
                ).hexdigest()
        else:
            release["runtime_variants"][0]["runtime_descriptor"][
                "python_cache_tag"
            ] = "cpython-999"
        changed_descriptor = release["runtime_variants"][0]["runtime_descriptor"]
        changed_descriptor["generation_id"] = hashlib.sha256(
            canonical_payload_bytes(
                {
                    key: value
                    for key, value in changed_descriptor.items()
                    if key != "generation_id"
                }
            )
        ).hexdigest()
        private, public = _key_pair()
        module.UPDATE_PUBLIC_KEYS = public
        raw["signature"]["value"] = base64.b64encode(
            private.sign(canonical_payload_bytes(raw["payload"]))
        ).decode()
        raw["signature"]["key_id"] = "test-key"
        trust = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        trust_path.write_bytes(trust)
        active["runtime_trust_sha256"] = hashlib.sha256(trust).hexdigest()
        (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")

    with pytest.raises(RuntimeError, match=match):
        module.maybe_supervise(
            image_app_dir=tmp_path / "image",
            runtime_dir=runtime_dir,
            app_dir=generation,
            launcher_protocol=4,
            mode="ui",
            app_args=(),
            host="127.0.0.1",
            port=8501,
            log_level="warning",
        )


def test_runtime_identity_is_rejected_before_protocol_four(tmp_path: Path):
    from core.runtime_image_bootstrap import maybe_supervise

    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    generation = tmp_path / "generation"
    generation.mkdir()
    (runtime_dir / "active.json").write_text(
        json.dumps({"path": str(generation), "runtime_generation_id": "a" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="protocol 4"):
        maybe_supervise(
            image_app_dir=tmp_path / "image",
            runtime_dir=runtime_dir,
            app_dir=generation,
            launcher_protocol=3,
            mode="core",
            app_args=(),
        )


def test_active_selection_change_before_spawn_is_rejected(tmp_path: Path, monkeypatch):
    module, runtime_dir, generation, active, _trust_path = _runtime_state(
        tmp_path, monkeypatch
    )
    real_active_state = module._active_state
    reads = 0

    def changing_active(path: Path):
        nonlocal reads
        reads += 1
        if reads == 1:
            return real_active_state(path)
        return {**active, "activation_id": "activation-2"}

    monkeypatch.setattr(module, "_active_state", changing_active)
    monkeypatch.setattr(
        module,
        "supervise_image_worker",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    with pytest.raises(RuntimeError, match="changed during startup"):
        module.maybe_supervise(
            image_app_dir=tmp_path / "image",
            runtime_dir=runtime_dir,
            app_dir=generation,
            launcher_protocol=4,
            mode="core",
            app_args=(),
        )


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
@pytest.mark.parametrize("target", ["runtime", "releases", "generation"])
def test_dependency_startup_rejects_writable_runtime_storage(
    tmp_path: Path, monkeypatch, target: str
):
    module, runtime_dir, generation, _active, _trust_path = _runtime_state(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(module, "_assert_image_authorities", lambda _path: None)
    selected = {
        "runtime": runtime_dir,
        "releases": runtime_dir / "releases",
        "generation": generation,
    }[target]
    selected.chmod(0o777)

    with pytest.raises(RuntimeError, match="trusted|protected"):
        module.maybe_supervise(
            image_app_dir=tmp_path / "image",
            runtime_dir=runtime_dir,
            app_dir=generation,
            launcher_protocol=4,
            mode="core",
            app_args=(),
        )


def test_generation_swap_after_verification_is_rejected(tmp_path: Path, monkeypatch):
    module, runtime_dir, generation, _active, _trust_path = _runtime_state(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(module, "_assert_image_authorities", lambda _path: None)

    def swap_generation(_generation):
        displaced = generation.with_name(f".{generation.name}.displaced")
        generation.rename(displaced)
        generation.mkdir(mode=0o755)

    monkeypatch.setattr(module, "verify_runtime_generation", swap_generation)
    monkeypatch.setattr(
        module,
        "supervise_image_worker",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    with pytest.raises(RuntimeError, match="changed"):
        module.maybe_supervise(
            image_app_dir=tmp_path / "image",
            runtime_dir=runtime_dir,
            app_dir=generation,
            launcher_protocol=4,
            mode="core",
            app_args=(),
        )


@pytest.mark.skipif(os.name == "nt", reason="symlink validation is POSIX-only")
def test_dependency_startup_rejects_original_symlinked_generation_path(
    tmp_path: Path, monkeypatch
):
    module, runtime_dir, generation, _active, _trust_path = _runtime_state(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(module, "_assert_image_authorities", lambda _path: None)
    selected = generation.with_name("selected-generation")
    selected.symlink_to(generation, target_is_directory=True)

    with pytest.raises(RuntimeError, match="trusted"):
        module.maybe_supervise(
            image_app_dir=tmp_path / "image",
            runtime_dir=runtime_dir,
            app_dir=selected,
            launcher_protocol=4,
            mode="core",
            app_args=(),
        )


def test_image_and_legacy_app_selection_do_not_enter_dependency_worker(
    tmp_path: Path,
):
    from core.runtime_image_bootstrap import maybe_supervise

    image = tmp_path / "image"
    image.mkdir()
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    (runtime_dir / "active.json").write_text(
        json.dumps({"version": "1.4.2", "path": str(ordinary)}), encoding="utf-8"
    )

    assert not maybe_supervise(
        image_app_dir=image,
        runtime_dir=runtime_dir,
        app_dir=image,
        launcher_protocol=4,
        mode="core",
        app_args=(),
    )
    assert not maybe_supervise(
        image_app_dir=image,
        runtime_dir=runtime_dir,
        app_dir=ordinary,
        launcher_protocol=3,
        mode="core",
        app_args=(),
    )


@pytest.mark.parametrize("state", [None, {"version": "1.5.1"}])
def test_protocol_four_cannot_downgrade_missing_runtime_identity(
    tmp_path: Path, state,
):
    from core.runtime_image_bootstrap import maybe_supervise

    image = tmp_path / "image"
    image.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    selected = runtime / "releases" / ("a" * 64)
    selected.mkdir(parents=True)
    if state is not None:
        (runtime / "active.json").write_text(
            json.dumps({**state, "path": str(selected)}), encoding="utf-8"
        )
    with pytest.raises(RuntimeError, match="identity is missing"):
        maybe_supervise(
            image_app_dir=image, runtime_dir=runtime, app_dir=selected,
            launcher_protocol=4, mode="core", app_args=(),
        )


def test_launcher_calls_dependency_gate_before_import_path_inside_rollback_scope(
    tmp_path: Path, monkeypatch
):
    from core import runtime_image_bootstrap, runtime_launcher

    image = tmp_path / "image"
    image.mkdir()
    generation = tmp_path / "generation"
    generation.mkdir()
    calls = []
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: generation)
    monkeypatch.setattr(
        runtime_launcher, "start_restart_required_watchdog", lambda: None
    )
    monkeypatch.setattr(
        runtime_launcher,
        "start_activation_watchdog",
        lambda path: calls.append(("watchdog", path)),
    )
    monkeypatch.setattr(
        runtime_image_bootstrap,
        "maybe_supervise",
        lambda **kwargs: calls.append(("dependency", kwargs["app_dir"])) or True,
    )
    monkeypatch.setattr(
        runtime_launcher,
        "prepare_import_path",
        lambda path: calls.append(("import", path)),
    )

    assert runtime_launcher.main(["core", "--stay-alive"]) == 0
    assert calls == [("watchdog", generation), ("dependency", generation)]
