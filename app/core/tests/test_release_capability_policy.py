from __future__ import annotations

import base64
import copy
import datetime as datetime_module
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
TRACKED_DISTRIBUTIONS = {
    "requests": "requests",
    "cryptography": "cryptography",
    "sqlmodel": "sqlmodel",
    "uvicorn": "uvicorn",
    "pydantic_core": "pydantic-core",
    "sqlalchemy": "sqlalchemy",
    "httptools": "httptools",
}
SELECTED_VERSIONS = {
    "requests": "2.34.0",
    "cryptography": "46.0.5",
    "sqlmodel": "0.0.37",
    "uvicorn": "0.41.0",
    "pydantic-core": "2.42.0",
    "sqlalchemy": "2.1.4",
    "httptools": "0.9.0",
}
IMAGE_BOOTSTRAP_PATHS = (
    "/usr/local/bin/channelwatch",
    "/app/channelwatch-image.json",
    "/app/core/docker-entrypoint.py",
    "/app/core/runtime_launcher.py",
    "/app/core/runtime_image_bootstrap.py",
    "/app/core/runtime_release_trust.py",
    "/app/core/runtime_generation.py",
    "/app/core/runtime_dependency_bridge.py",
    "/app/core/runtime_worker.py",
    "/etc/supervisor/conf.d/supervisord.conf.template",
)


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


def _file_record(path: str) -> dict:
    return {
        "path": path,
        "sha256": hashlib.sha256(path.encode()).hexdigest(),
        "mode": "0644",
        "size": len(path.encode()),
    }


def _descriptor_and_inventory(architecture: str) -> tuple[dict, bytes]:
    descriptor = _descriptor(architecture)
    native_suffix = (
        "x86_64-linux-gnu.so" if architecture == "amd64" else "aarch64-linux-gnu.so"
    )
    dependency_paths = [
        f"dependencies/site-packages/{module}/__init__.py"
        for module in TRACKED_DISTRIBUTIONS
    ] + [
        "dependencies/site-packages/httptools/parser/"
        f"parser.cpython-314-{native_suffix}"
    ]
    descriptor["files"] = sorted(
        [
            _file_record("core/main.py"),
            _file_record("ui/backend/main.py"),
            *(_file_record(path) for path in dependency_paths),
        ],
        key=lambda item: item["path"],
    )
    descriptor["expanded_size"] = sum(item["size"] for item in descriptor["files"])
    locked_names = sorted(SELECTED_VERSIONS)
    inventory_files = [
        item for item in descriptor["files"] if item["path"].startswith("dependencies/")
    ]
    inventory = {
        "schema": 1,
        "build": {
            "builder_image_digest": "sha256:" + "9" * 64,
            "python_version": descriptor["python_version"],
            "pip_version": "26.0.1",
        },
        "platform": {
            "architecture": architecture,
            "python_implementation": descriptor["python_implementation"],
            "python_cache_tag": descriptor["python_cache_tag"],
            "python_version": descriptor["python_version"],
            "os_family": descriptor["os_family"],
            "libc_family": descriptor["libc_family"],
            "minimum_libc_version": ".".join(
                str(part) for part in descriptor["minimum_libc_version"]
            ),
            "wheel_platform": f"manylinux_2_36_{architecture}",
        },
        "requirements": {
            "direct": locked_names,
            "locked": [
                {
                    "name": name,
                    "version": SELECTED_VERSIONS[name],
                    "hashes": [hashlib.sha256((name + architecture).encode()).hexdigest()],
                }
                for name in locked_names
            ],
        },
        "artifacts": [
            {
                "filename": f"dependencies-{architecture}.whl",
                "sha256": hashlib.sha256(architecture.encode()).hexdigest(),
                "size": 1,
                "source": f"https://example.invalid/dependencies-{architecture}.whl",
            }
        ],
        "dependencies": [
            {
                "name": name,
                "version": SELECTED_VERSIONS[name],
                "license_expression": "MIT",
                "license_files": [],
            }
            for name in locked_names
        ],
        "files": inventory_files,
        "counts": {
            "files": len(inventory_files),
            "bytes": sum(item["size"] for item in inventory_files),
        },
    }
    raw = canonical_payload_bytes(inventory)
    descriptor["dependency_inventory_sha256"] = hashlib.sha256(raw).hexdigest()
    descriptor["application_version"] = "1.6.0"
    descriptor["source_commit"] = SOURCE_COMMIT
    descriptor["generation_id"] = descriptor_generation_id(descriptor)
    return descriptor, raw


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
    inventories = {}
    for architecture in ("amd64", "arm64"):
        descriptor, inventory_raw = _descriptor_and_inventory(architecture)
        descriptors[architecture] = descriptor
        inventories[architecture] = inventory_raw
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
    return path, {"test-key": public}, descriptors, inventories


def _image_inventory(architecture: str) -> dict:
    entries = []
    directories = {
        "/venv",
        "/venv/bin",
        "/venv/lib",
        "/venv/lib/python3.14",
        "/venv/lib/python3.14/site-packages",
        *(
            "/venv/lib/python3.14/site-packages/" + module
            for module in TRACKED_DISTRIBUTIONS
        ),
        "/venv/lib/python3.14/site-packages/httptools/parser",
    }
    for path in directories:
        entries.append({"path": path, "type": "directory", "mode": "0755", "size": 1})
    for path in IMAGE_BOOTSTRAP_PATHS:
        entries.append({**_file_record(path), "type": "file"})
    for module in TRACKED_DISTRIBUTIONS:
        path = f"/venv/lib/python3.14/site-packages/{module}/__init__.py"
        entries.append({**_file_record(path), "type": "file"})
    native_suffix = (
        "x86_64-linux-gnu.so" if architecture == "amd64" else "aarch64-linux-gnu.so"
    )
    parser_path = (
        "/venv/lib/python3.14/site-packages/httptools/parser/"
        f"parser.cpython-314-{native_suffix}"
    )
    entries.append({**_file_record(parser_path), "type": "file"})
    for name in ("python", "python3", "python3.14"):
        entries.append(
            {
                "path": f"/venv/bin/{name}",
                "type": "symlink",
                "mode": "0777",
                "size": len(f"/usr/local/bin/{name}"),
                "link_target": f"/usr/local/bin/{name}",
            }
        )
    entries.sort(key=lambda item: item["path"])
    encoded = canonical_payload_bytes(entries)
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "path_count": len(entries),
        "regular_file_bytes": sum(
            item["size"] for item in entries if item["type"] == "file"
        ),
        "entries": entries,
    }


def _refresh_image_inventory(qualification: dict, mutate) -> None:
    inventory = qualification["image_inventory_before"]
    mutate(inventory["entries"])
    inventory["path_count"] = len(inventory["entries"])
    inventory["regular_file_bytes"] = sum(
        item["size"] for item in inventory["entries"] if item.get("type") == "file"
    )
    inventory["sha256"] = hashlib.sha256(
        canonical_payload_bytes(inventory["entries"])
    ).hexdigest()
    summary = {
        key: inventory[key] for key in ("sha256", "path_count", "regular_file_bytes")
    }
    for stage in ("after_apply", "after_restart", "after_rollback", "after_reapply"):
        qualification["image_inventory_" + stage] = copy.deepcopy(summary)


def _evidence(
    catalog_path: Path,
    descriptors: dict[str, dict],
    inventories: dict[str, bytes],
):
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
        dependencies = {
            module: {
                "loaded": True,
                "path": root
                + "/dependencies/site-packages/"
                + module
                + "/__init__.py",
                "version": SELECTED_VERSIONS[distribution],
            }
            for module, distribution in TRACKED_DISTRIBUTIONS.items()
        }
        components = {
            component: {
                "runtime_generation_id": descriptor["generation_id"],
                "runtime_descriptor_sha256": descriptor["generation_id"],
                "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
                "process_runtime": {
                    "pid": pid,
                    "python_executable": root + "/environment/bin/python",
                    "python_prefix": root + "/environment",
                    "python_version": descriptor["python_version"],
                    "dependencies": copy.deepcopy(dependencies),
                },
            }
            for component, pid in (("core", 123), ("ui", 124))
        }
        reapply_components = copy.deepcopy(components)
        for component, pid in (("core", 223), ("ui", 224)):
            reapply_components[component]["process_runtime"]["pid"] = pid
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
                "reapply_runtime_components": copy.deepcopy(reapply_components),
                "signed_dependency_inventory": {
                    "sha256": descriptor["dependency_inventory_sha256"],
                    "size": len(inventories[architecture]),
                    "json_base64": base64.b64encode(inventories[architecture]).decode(),
                },
                "image_imports_before": None,
                "selected_native_imports": None,
                "image_imports_after_rollback": None,
                "supervisor_processes": {
                    "core": (
                        f"{root}/environment/bin/python -I -B -S "
                        "/app/core/runtime_worker.py --image-app-dir /app "
                        f"--mode core --generation {root} --activation-id activation-123 "
                        f"--descriptor-sha256 {descriptor['generation_id']} -- --stay-alive"
                    ),
                    "ui": (
                        f"{root}/environment/bin/python -I -B -S "
                        "/app/core/runtime_worker.py --image-app-dir /app "
                        f"--mode ui --generation {root} --activation-id activation-123 "
                        f"--descriptor-sha256 {descriptor['generation_id']} --host 0.0.0.0 "
                        "--port 8501 --log-level warning --"
                    ),
                },
                "persistent_hashes_before": {path: "c" * 64 for path in ("/config/settings.json", "/config/encryption.key")},
                "container_restart": {"status": "passed", "current_version": "1.6.0"},
                "rollback": {"operation": "rollback", "status": "restarting", "version": "1.6.0", "rolled_back_from": "1.6.0", "restart_required": True, "job_id": "c" * 32},
                "rollback_status": None,
            },
        })
        qualification = architectures[architecture]["qualification"]
        qualification.update(native_host_architecture=architecture, docker_engine_architecture=architecture,
                             container_identity_before="d" * 64, container_identity_after="d" * 64)
        commands = qualification["supervisor_processes"]
        qualification["supervisor_processes_after_restart"] = copy.deepcopy(commands)
        qualification["rollback_supervisor_processes"] = {
            "core": (
                "/venv/bin/python -u /app/core/runtime_launcher.py "
                "core --stay-alive"
            ),
            "ui": (
                "/venv/bin/python -u /app/core/runtime_launcher.py ui "
                "--host 0.0.0.0 --port 8501 --log-level warning"
            ),
        }
        qualification["rollback_supervisor_process_identities"] = {
            component: {
                "pid": pid,
                "command": qualification["rollback_supervisor_processes"][component],
            }
            for component, pid in (("core", 456), ("ui", 457))
        }
        qualification["rollback_supervisor_services"] = {
            component: {
                "status": "RUNNING",
                "pid": pid,
                "uptime": "0:00:05",
            }
            for component, pid in (("core", 456), ("ui", 457))
        }
        qualification["rollback_health"] = {
            "startup": {"status": "ready"},
            "ready": {"status": "ready", "ready": True},
        }
        qualification["supervisor_processes_after_reapply"] = copy.deepcopy(commands)
        preserved = qualification["persistent_hashes_before"]
        for stage in ("after_apply", "after_restart", "after_rollback", "after_reapply"):
            qualification["persistent_hashes_" + stage] = copy.deepcopy(preserved)
        image_inventory = _image_inventory(architecture)
        qualification["image_inventory_before"] = image_inventory
        image_summary = {
            key: image_inventory[key]
            for key in ("sha256", "path_count", "regular_file_bytes")
        }
        for stage in ("after_apply", "after_restart", "after_rollback", "after_reapply"):
            qualification["image_inventory_" + stage] = copy.deepcopy(image_summary)
        image_root = "/venv/lib/python3.14/site-packages"
        image_parser = next(
            item["path"]
            for item in image_inventory["entries"]
            if item["path"].startswith(
                image_root + "/httptools/parser/parser.cpython-314-"
            )
        )
        image_imports = {
            "sqlalchemy": {
                "path": image_root + "/sqlalchemy/__init__.py",
                "version": "2.0.54",
            },
            "httptools": {
                "path": image_root + "/httptools/__init__.py",
                "version": "0.8.0",
            },
            "httptools.parser.parser": {
                "path": image_parser,
                "version": "0.8.0",
                "sha256": hashlib.sha256(image_parser.encode()).hexdigest(),
            },
        }
        selected_parser = next(
            item
            for item in descriptor["files"]
            if item["path"].startswith(
                "dependencies/site-packages/httptools/parser/parser.cpython-314-"
            )
        )
        selected_imports = {
            "sqlalchemy": {
                "path": root + "/dependencies/site-packages/sqlalchemy/__init__.py",
                "version": "2.1.4",
            },
            "httptools": {
                "path": root + "/dependencies/site-packages/httptools/__init__.py",
                "version": "0.9.0",
            },
            "httptools.parser.parser": {
                "path": root + "/" + selected_parser["path"],
                "version": "0.9.0",
                "sha256": selected_parser["sha256"],
            },
        }
        qualification["image_imports_before"] = image_imports
        qualification["selected_native_imports"] = selected_imports
        qualification["selected_native_imports_after_restart"] = copy.deepcopy(
            selected_imports
        )
        qualification["selected_native_imports_after_reapply"] = copy.deepcopy(
            selected_imports
        )
        qualification["image_imports_after_rollback"] = copy.deepcopy(image_imports)
        image_dependencies = {
            module: {
                "loaded": True,
                "path": image_root + f"/{module}/__init__.py",
                "version": SELECTED_VERSIONS[distribution],
            }
            for module, distribution in TRACKED_DISTRIBUTIONS.items()
            if module
            in {"requests", "cryptography", "sqlmodel", "uvicorn", "pydantic_core"}
        }
        rollback_worker = {
            "python_executable": "/venv/bin/python",
            "python_version": descriptor["python_version"],
            "verified_generation": None,
        }
        qualification["rollback_status"] = {
            "current_version": "1.5.0",
            "launcher_protocol": 4,
            "active_bundle": None,
            "operation_busy": False,
            "worker_runtime": copy.deepcopy(rollback_worker),
        }
        qualification["rollback_runtime_components"] = {
            "ui": copy.deepcopy(rollback_worker)
        }
        qualification["rollback_runtime_component_scope"] = (
            "frozen_image_ui_status_interpreter_only"
        )
        qualification["rollback_runtime_info"] = {
            "diagnostic_source": "image",
            "application_dir": "/app",
            "application_version": "1.5.0",
            "process_runtime": {
                "pid": 457,
                "python_executable": "/venv/bin/python",
                "python_prefix": "/venv",
                "python_version": descriptor["python_version"],
                "dependencies": image_dependencies,
            },
            "verified_generation": None,
        }
        qualification["initial_status"]["worker_runtime"] = copy.deepcopy(
            rollback_worker
        )

        def image_operational_state() -> dict:
            return {
                "status_worker_scope": "frozen_image_ui_status_interpreter_only",
                "status_worker": copy.deepcopy(rollback_worker),
                "commands": copy.deepcopy(
                    qualification["rollback_supervisor_processes"]
                ),
                "processes": copy.deepcopy(
                    qualification["rollback_supervisor_process_identities"]
                ),
                "services": copy.deepcopy(
                    qualification["rollback_supervisor_services"]
                ),
                "health": copy.deepcopy(qualification["rollback_health"]),
            }

        def selected_operational_state(
            runtime_components: dict | None,
            worker_pids: tuple[int, int],
            launcher_pids: tuple[int, int],
        ) -> dict:
            current_components = (
                copy.deepcopy(runtime_components)
                if runtime_components is not None
                else None
            )
            process_runtimes = (
                {
                    component: copy.deepcopy(record["process_runtime"])
                    for component, record in runtime_components.items()
                }
                if runtime_components is not None
                else {
                    component: copy.deepcopy(components[component]["process_runtime"])
                    for component in ("core", "ui")
                }
            )
            for component, pid in zip(("core", "ui"), worker_pids):
                process_runtimes[component]["pid"] = pid
            launchers = {
                component: {
                    "pid": pid,
                    "command": qualification["rollback_supervisor_processes"][
                        component
                    ],
                }
                for component, pid in zip(("core", "ui"), launcher_pids)
            }
            services = {
                component: {
                    "status": "RUNNING",
                    "pid": launcher_pids[index],
                    "uptime": "0:00:05",
                }
                for index, component in enumerate(("core", "ui"))
            }
            commands = copy.deepcopy(qualification["supervisor_processes"])
            processes = {
                component: {
                    "pid": worker_pids[index],
                    "ppid": launcher_pids[index],
                    "command": commands[component],
                }
                for index, component in enumerate(("core", "ui"))
            }
            if current_components is not None:
                for component in ("core", "ui"):
                    current_components[component]["process_runtime"] = copy.deepcopy(
                        process_runtimes[component]
                    )
            return {
                "runtime_components": current_components,
                "status_worker_scope": "ui_status_handler_only",
                "status_worker": {
                    **copy.deepcopy(process_runtimes["ui"]),
                    "verified_generation": {
                        "path": root,
                        "runtime_generation_id": descriptor["generation_id"],
                        "runtime_descriptor_sha256": descriptor["generation_id"],
                        "dependency_inventory_sha256": descriptor[
                            "dependency_inventory_sha256"
                        ],
                    },
                },
                "commands": commands,
                "processes": processes,
                "launchers": launchers,
                "services": services,
                "health": {
                    "startup": {"status": "ready"},
                    "ready": {"status": "ready", "ready": True},
                },
            }

        qualification["initial_operational_state"] = image_operational_state()
        qualification["apply_operational_state"] = selected_operational_state(
            components, (123, 124), (301, 302)
        )
        qualification["restart_operational_state"] = selected_operational_state(
            None, (323, 324), (401, 402)
        )
        qualification["reapply_operational_state"] = selected_operational_state(
            reapply_components, (223, 224), (501, 502)
        )
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
    catalog_path, public_keys, descriptors, inventories = _signed_catalog(tmp_path)
    evidence = _evidence(catalog_path, descriptors, inventories)
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


def _patch_two_recovery_config(**overrides):
    config = _recovery_config(
        version="1.5.2",
        minimum_image_version="1.5.2",
        recommended_image_version="1.5.2",
        unpublished_predecessor_tags=["v1.5.1", "v1.5.0"],
        unpublished_release_tags=[
            "v1.4.2",
            "v1.4.3",
            "v1.4.4",
            "v1.5.0",
            "v1.5.1",
        ],
    )
    config.update(overrides)
    return config


def _patch_three_recovery_config(**overrides):
    config = _recovery_config(
        version="1.5.3",
        minimum_image_version="1.5.3",
        recommended_image_version="1.5.3",
        unpublished_predecessor_tags=["v1.5.2", "v1.5.1", "v1.5.0"],
        unpublished_release_tags=[
            "v1.4.2",
            "v1.4.3",
            "v1.4.4",
            "v1.5.0",
            "v1.5.1",
            "v1.5.2",
        ],
    )
    config.update(overrides)
    return config


def test_patch_three_recovery_accepts_the_exact_descending_predecessor_chain():
    module = _load_script(
        "release_version_policy_patch_three_recovery",
        "scripts/release/release_version_policy.py",
    )

    policy = module.validate_release_config(_patch_three_recovery_config())

    assert policy.image_milestone is True
    assert policy.minimum_image_version == "1.5.3"
    assert policy.retained_capability_floor is False


def test_current_release_config_retains_the_published_dependency_image_floor():
    module = _load_script(
        "release_version_policy_current_dependency_image_floor",
        "scripts/release/release_version_policy.py",
    )
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )

    policy = module.validate_release_config(config)

    assert config["version"] == "1.6.1"
    assert config["minimum_image_version"] == "1.5.3"
    assert config["recommended_image_version"] == "1.6.1"
    assert config["publication_time"].endswith("Z")
    assert config["automatic_install_after"].endswith("Z")
    publication_time = datetime_module.datetime.fromisoformat(
        config["publication_time"].removesuffix("Z") + "+00:00"
    )
    automatic_install_after = datetime_module.datetime.fromisoformat(
        config["automatic_install_after"].removesuffix("Z") + "+00:00"
    )
    assert publication_time.tzinfo is datetime_module.UTC
    assert automatic_install_after.tzinfo is datetime_module.UTC
    assert automatic_install_after - publication_time == datetime_module.timedelta(
        days=7
    )
    assert config["automatic_install_allowed"] is False
    assert config["compatible_source_application_versions"] == ["1.5.3", "1.6.0"]
    assert config["runtime_generation_required"] is True
    assert config["runtime_compatibility_evidence"] == {}
    assert "recovers_unpublished_image_milestone" not in config
    assert config["unpublished_predecessor_tags"] == []
    assert config["unpublished_release_tags"] == [
        "v1.4.2",
        "v1.4.3",
        "v1.4.4",
        "v1.5.0",
        "v1.5.1",
        "v1.5.2",
        "v1.5.4",
    ]
    assert policy.image_milestone is False
    assert policy.minimum_image_version == "1.5.3"
    assert policy.retained_capability_floor is True


@pytest.mark.parametrize(
    "predecessors",
    [
        ["v1.5.1", "v1.5.0"],
        ["v1.5.0", "v1.5.1", "v1.5.2"],
    ],
)
def test_patch_three_recovery_rejects_missing_or_reversed_predecessors(predecessors):
    module = _load_script(
        "release_version_policy_invalid_patch_three_recovery",
        "scripts/release/release_version_policy.py",
    )

    with pytest.raises(ValueError, match="exact contiguous unpublished predecessors"):
        module.validate_release_config(
            _patch_three_recovery_config(unpublished_predecessor_tags=predecessors)
        )


def test_patch_two_recovery_accepts_the_exact_descending_predecessor_chain():
    module = _load_script(
        "release_version_policy_patch_two_recovery",
        "scripts/release/release_version_policy.py",
    )

    policy = module.validate_release_config(_patch_two_recovery_config())

    assert policy.image_milestone is True
    assert policy.minimum_image_version == "1.5.2"
    assert policy.retained_capability_floor is False


@pytest.mark.parametrize(
    "predecessors",
    [
        ["v1.5.0"],
        ["v1.5.0", "v1.5.1"],
    ],
)
def test_patch_two_recovery_rejects_missing_or_reversed_predecessors(predecessors):
    module = _load_script(
        "release_version_policy_invalid_patch_two_recovery",
        "scripts/release/release_version_policy.py",
    )

    with pytest.raises(ValueError, match="exact contiguous unpublished predecessors"):
        module.validate_release_config(
            _patch_two_recovery_config(unpublished_predecessor_tags=predecessors)
        )


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
    catalog_path, public_keys, descriptors, inventories = _signed_catalog(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(_evidence(catalog_path, descriptors, inventories)), encoding="utf-8"
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
    "mutation",
    [
        lambda component, _state: component["process_runtime"].pop(
            "python_prefix"
        ),
        lambda component, state: component["process_runtime"].update(
            verified_generation=copy.deepcopy(
                state["status_worker"]["verified_generation"]
            )
        ),
        lambda component, _state: component.update(
            dependency_inventory_sha256="e" * 64
        ),
    ],
)
def test_runtime_classifier_requires_exact_component_producer_shape(
    tmp_path: Path, mutation
):
    module = _load_script(
        "classify_release_capability_component_shape",
        "scripts/release/classify-release-impact.py",
    )

    def mutate(evidence, _catalog):
        qualification = evidence["architectures"]["arm64"]["qualification"]
        state = qualification["apply_operational_state"]
        mutation(qualification["apply_runtime_components"]["core"], state)
        mutation(state["runtime_components"]["core"], state)

    result = _apply(
        module, tmp_path, paths=("deploy/requirements/runtime.txt",), mutate=mutate
    )

    assert result.delivery_mode == "image_required"
    assert module.RUNTIME_CAPABILITY_EVIDENCE_TRIGGER in result.triggering_paths


def test_selected_dependency_version_must_match_the_signed_inventory(tmp_path: Path):
    module = _load_script(
        "classify_release_capability_dependency_version",
        "scripts/release/classify-release-impact.py",
    )

    def mutate(evidence, _catalog):
        evidence["architectures"]["amd64"]["qualification"][
            "apply_runtime_components"
        ]["core"]["process_runtime"]["dependencies"]["requests"]["version"] = "999.0"

    result = _apply(
        module, tmp_path, paths=("deploy/requirements/runtime.txt",), mutate=mutate
    )

    assert result.delivery_mode == "image_required"
    assert module.RUNTIME_CAPABILITY_EVIDENCE_TRIGGER in result.triggering_paths


@pytest.mark.parametrize("component", ["core", "ui"])
def test_loaded_sqlalchemy_is_mandatory_in_each_selected_worker(
    tmp_path: Path, component: str
):
    module = _load_script(
        "classify_release_capability_selected_sqlalchemy_" + component,
        "scripts/release/classify-release-impact.py",
    )

    def mutate(evidence, _catalog):
        evidence["architectures"]["amd64"]["qualification"][
            "apply_runtime_components"
        ][component]["process_runtime"]["dependencies"]["sqlalchemy"] = {
            "loaded": False
        }

    result = _apply(
        module, tmp_path, paths=("deploy/requirements/runtime.txt",), mutate=mutate
    )

    assert result.delivery_mode == "image_required"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda qualification: qualification["supervisor_processes"].update(
            core="/tmp/evil --pretend " + qualification["supervisor_processes"]["core"]
        ),
        lambda qualification: (
            qualification["rollback_supervisor_processes"].update(
                core=(
                    "/tmp/evil --pretend "
                    + qualification["rollback_supervisor_processes"]["core"]
                )
            ),
            qualification["rollback_supervisor_process_identities"]["core"].update(
                command=qualification["rollback_supervisor_processes"]["core"]
            ),
        ),
        lambda qualification: qualification["rollback_runtime_info"][
            "process_runtime"
        ].update(verified_generation={"runtime_generation_id": "a" * 64}),
        lambda qualification: _refresh_image_inventory(
            qualification,
            lambda entries: entries.append(
                {
                    "path": "/app/escape",
                    "type": "symlink",
                    "mode": "0777",
                    "size": 5,
                    "link_target": "/host",
                }
            ),
        ),
    ],
)
def test_adversarial_classifier_receipts_fail_closed(
    tmp_path: Path, mutate
):
    module = _load_script(
        "classify_release_capability_adversarial",
        "scripts/release/classify-release-impact.py",
    )

    result = _apply(
        module,
        tmp_path,
        paths=("deploy/requirements/runtime.txt",),
        mutate=lambda evidence, _catalog: mutate(
            evidence["architectures"]["amd64"]["qualification"]
        ),
    )

    assert result.delivery_mode == "image_required"


def test_unloaded_optional_httptools_worker_record_remains_app_update(tmp_path: Path):
    module = _load_script(
        "classify_release_capability_optional_httptools",
        "scripts/release/classify-release-impact.py",
    )

    def mutate(evidence, _catalog):
        for architecture in evidence["architectures"].values():
            qualification = architecture["qualification"]
            for field in ("apply_runtime_components", "reapply_runtime_components"):
                for component in qualification[field].values():
                    component["process_runtime"]["dependencies"]["httptools"] = {
                        "loaded": False
                    }
            for field, components_field in (
                ("apply_operational_state", "apply_runtime_components"),
                ("reapply_operational_state", "reapply_runtime_components"),
            ):
                state = qualification[field]
                state["runtime_components"] = copy.deepcopy(
                    qualification[components_field]
                )
                state["status_worker"]["dependencies"]["httptools"] = {
                    "loaded": False
                }
            qualification["restart_operational_state"]["status_worker"][
                "dependencies"
            ]["httptools"] = {"loaded": False}

    result = _apply(
        module, tmp_path, paths=("deploy/requirements/runtime.txt",), mutate=mutate
    )

    assert result.delivery_mode == "app_update"
    assert result.triggering_paths == ()


def test_publication_only_runtime_proofs_are_not_classifier_requirements(
    tmp_path: Path,
):
    module = _load_script(
        "classify_release_capability_without_publication_proofs",
        "scripts/release/classify-release-impact.py",
    )

    def mutate(evidence, _catalog):
        assert all(
            "release_qualification" not in architecture
            for architecture in evidence["architectures"].values()
        )

    result = _apply(
        module, tmp_path, paths=("deploy/requirements/runtime.txt",), mutate=mutate
    )

    assert result.delivery_mode == "app_update"
    assert result.triggering_paths == ()


def _mutate_image_entry(evidence, path: str, **updates) -> None:
    qualification = evidence["architectures"]["amd64"]["qualification"]

    def mutate(entries):
        entry = next(item for item in entries if item["path"] == path)
        entry.update(updates)

    _refresh_image_inventory(qualification, mutate)


def _remove_image_entry(evidence, path: str) -> None:
    qualification = evidence["architectures"]["amd64"]["qualification"]
    _refresh_image_inventory(
        qualification,
        lambda entries: entries.__setitem__(
            slice(None), [item for item in entries if item["path"] != path]
        ),
    )


def _duplicate_image_entry(evidence) -> None:
    qualification = evidence["architectures"]["amd64"]["qualification"]
    _refresh_image_inventory(
        qualification, lambda entries: entries.append(copy.deepcopy(entries[0]))
    )


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
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["apply_runtime_components"]["core"]["process_runtime"]["dependencies"]["sqlalchemy"].update({"loaded": False}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["reapply_runtime_components"]["ui"].update({"runtime_generation_id": "0" * 64}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["reapply_runtime_components"]["ui"]["process_runtime"]["dependencies"].update(extra={"loaded": False}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].pop("initial_operational_state"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["apply_operational_state"]["processes"]["core"].update(ppid=999),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["apply_operational_state"]["launchers"]["core"].update(command="/venv/bin/python /tmp/evil --mode core"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["apply_operational_state"]["runtime_components"]["ui"]["process_runtime"].update(pid=999),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["restart_operational_state"].update(runtime_components={}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["restart_operational_state"]["status_worker"].update(pid=999),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["restart_operational_state"]["health"]["ready"].update(ready=False),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["reapply_operational_state"]["services"]["ui"].update(status="STOPPED"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["selected_native_imports_after_restart"]["sqlalchemy"].update(version="0.0"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].pop("selected_native_imports_after_reapply"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].pop("signed_dependency_inventory"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["signed_dependency_inventory"].update(sha256="0" * 64),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["signed_dependency_inventory"].update(size=0),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["signed_dependency_inventory"].update(json_base64="not-base64"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["selected_native_imports"]["httptools"].update(version="0.8.0"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["selected_native_imports"]["httptools"].update(path="/venv/lib/python3.14/site-packages/httptools/__init__.py"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["selected_native_imports"]["httptools.parser.parser"].update(path="/config/channelwatch-runtime/releases/" + "0" * 64 + "/dependencies/site-packages/httptools/parser/parser.py"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["selected_native_imports"]["httptools.parser.parser"].update(sha256="0" * 64),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["image_imports_before"]["sqlalchemy"].update(version="2.1.4"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["image_imports_after_rollback"]["httptools"].update(path="/config/channelwatch-runtime/releases/bad/httptools/__init__.py"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["persistent_hashes_before"].pop("/config/encryption.key"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(native_host_architecture="arm64"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["persistent_hashes_after_restart"].update({"/config/settings.json": "0" * 64}),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(container_identity_after="0" * 64),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(reapply_job_id="a" * 32),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["supervisor_processes_after_restart"].update(core="/venv/bin/python --mode core"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].pop("rollback_runtime_components"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_runtime_components"]["ui"].update(python_executable="/config/channelwatch-runtime/releases/bad/environment/bin/python"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"].update(rollback_runtime_component_scope="core_and_ui"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_supervisor_processes"].pop("ui"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_supervisor_process_identities"]["ui"].update(pid=456),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_supervisor_process_identities"]["core"].update(command="/venv/bin/python --mode ui"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_supervisor_services"]["core"].update(status="STOPPED"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_supervisor_services"]["ui"].update(pid=456),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_health"]["ready"].update(ready=False),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["rollback_runtime_info"].update(application_dir="/config/channelwatch-runtime/releases/bad/app"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["image_inventory_after_apply"].update(sha256="0" * 64),
        lambda evidence, _catalog: _remove_image_entry(evidence, "/app/core/runtime_launcher.py"),
        lambda evidence, _catalog: _duplicate_image_entry(evidence),
        lambda evidence, _catalog: _mutate_image_entry(evidence, "/app/core/runtime_worker.py", type="socket"),
        lambda evidence, _catalog: _mutate_image_entry(evidence, "/app/core/runtime_worker.py", mode="0999"),
        lambda evidence, _catalog: _mutate_image_entry(evidence, "/app/core/runtime_worker.py", size=-1),
        lambda evidence, _catalog: _mutate_image_entry(evidence, "/app/core/runtime_worker.py", sha256="not-a-digest"),
        lambda evidence, _catalog: _mutate_image_entry(evidence, "/venv/bin/python", link_target="/tmp/python"),
        lambda evidence, _catalog: evidence["architectures"]["amd64"]["qualification"]["image_inventory_before"].update(path_count=100001),
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
        "app/bin/channelwatch",
        "app/core/docker-entrypoint.py",
        "app/core/runtime_launcher.py",
        "app/core/runtime_image_bootstrap.py",
        "app/core/runtime_release_trust.py",
        "app/core/runtime_generation.py",
        "app/core/runtime_dependency_bridge.py",
        "app/core/runtime_worker.py",
        "deploy/config/supervisor/supervisord.conf.template",
        "deploy/docker/Dockerfile.dockerignore",
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
