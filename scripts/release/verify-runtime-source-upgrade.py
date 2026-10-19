#!/usr/bin/env python3
"""Prove the signed v1.6.0 runtime can update to the candidate in place."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_TOOL = ROOT / "scripts/release/verify-runtime-capability.py"
SOURCE_VERSION = "1.6.0"
SOURCE_COMMIT = "b48057184752c912e4ae9b3a474374704d896073"
SOURCE_CATALOG_SHA256 = "f20578206578901c9a28486c28e2a6f8c2e7974bad4ec314d55a3bcba53e1b39"
SOURCE_BUNDLE_SHA256 = {
    "amd64": "865a6054f0fbddd213fe5f2e2578de5da8bc4453f322a991003516cc1bd9cadc",
    "arm64": "60f0a7a97433778ac89e17bab90ef6e83566159649697f90e2f5918afd17c2ec",
}
SHA40 = re.compile(r"[0-9a-f]{40}")


def _load():
    spec = importlib.util.spec_from_file_location(
        "runtime_source_capability", CAPABILITY_TOOL
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the runtime capability verifier.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def identity(container: str, *, image_id: str, manifest_digest: str) -> dict[str, str]:
    current_container = tool.run(
        ["docker", "inspect", "--format", "{{.Id}}", container]
    ).stdout.strip()
    current_image = tool.run(
        ["docker", "inspect", "--format", "{{.Image}}", container]
    ).stdout.strip()
    if current_image != image_id or re.fullmatch(r"[0-9a-f]{64}", current_container) is None:
        raise tool.CapabilityError("Container or image identity changed.")
    return {
        "container_id": current_container,
        "image_id": current_image,
        "manifest_digest": manifest_digest,
    }


def selected_status(
    container: str,
    client: Any,
    *,
    version: str,
    descriptor: dict[str, Any],
    inventory: dict[str, Any],
    completed_job: dict[str, Any] | None,
) -> dict[str, Any]:
    return tool.capture_selected_operational_state(
        container,
        client,
        descriptor=descriptor,
        inventory=inventory,
        version=version,
        completed_job=completed_job,
    )


def qualify(args: argparse.Namespace) -> dict[str, Any]:
    architecture = tool.native_architecture(args.architecture)
    if sha256_file(args.source_catalog) != SOURCE_CATALOG_SHA256:
        raise tool.CapabilityError("The v1.6.0 source catalog digest is not immutable.")
    source_bundles = {
        "amd64": args.source_amd64_bundle.resolve(),
        "arm64": args.source_arm64_bundle.resolve(),
    }
    for name, path in source_bundles.items():
        if sha256_file(path) != SOURCE_BUNDLE_SHA256[name]:
            raise tool.CapabilityError(
                f"The v1.6.0 {name} runtime bundle digest is not immutable."
            )
    source_catalog_bytes, _source_release, source_variants = tool.verify_assets(
        args.source_catalog.resolve(),
        source_bundles,
        version=SOURCE_VERSION,
        source_commit=SOURCE_COMMIT,
    )
    target_bundles = {
        "amd64": args.target_amd64_bundle.resolve(),
        "arm64": args.target_arm64_bundle.resolve(),
    }
    target_catalog_bytes, _target_release, target_variants = tool.verify_assets(
        args.target_catalog.resolve(),
        target_bundles,
        version=args.target_version,
        source_commit=args.target_commit,
    )
    source_descriptor = source_variants[architecture]["runtime_descriptor"]
    target_descriptor = target_variants[architecture]["runtime_descriptor"]
    _, source_inventory = tool.load_signed_dependency_inventory(
        source_bundles[architecture], source_descriptor
    )
    _, target_inventory = tool.load_signed_dependency_inventory(
        target_bundles[architecture], target_descriptor
    )
    reference, manifest_digest, image_id, _index, _inspection = tool.pinned_native_image(
        args.image_repository, args.image_index_digest, architecture
    )
    prefix = f"cw-runtime-chain-{architecture}-{secrets.token_hex(5)}"
    resources = tool.DockerResources(prefix)
    root = Path(tempfile.mkdtemp(prefix=prefix + "-"))
    app = resources.container("app")
    feed = resources.container("feed")
    receipt: dict[str, Any] = {
        "schema": 1,
        "status": "running",
        "evidence_kind": "native",
        "architecture": architecture,
        "source_application_version": SOURCE_VERSION,
        "source_commit": SOURCE_COMMIT,
        "target_application_version": args.target_version,
        "target_commit": args.target_commit,
        "source_catalog_sha256": hashlib.sha256(source_catalog_bytes).hexdigest(),
        "target_catalog_sha256": hashlib.sha256(target_catalog_bytes).hexdigest(),
        "source_runtime_generation_id": source_descriptor["generation_id"],
        "target_runtime_generation_id": target_descriptor["generation_id"],
        "image_index_digest": args.image_index_digest,
        "image_manifest_digest": manifest_digest,
    }
    try:
        ca_path, _cert, _key = tool.create_tls_material(root)
        active_catalog = root / "active-runtime-catalog.json"
        shutil.copyfile(args.source_catalog, active_catalog)
        routes = {tool.DEFAULT_CATALOG_URL: active_catalog}
        for variants, bundles in (
            (source_variants, source_bundles),
            (target_variants, target_bundles),
        ):
            for name in ("amd64", "arm64"):
                routes[variants[name]["bundle_url"]] = bundles[name]
        tool.write_feed(root, routes)
        network = resources.create_network()
        volume = resources.create_volume()
        tool.seed_synthetic_rbac_settings(reference, volume)
        tool.run([
            "docker", "run", "-d", "--name", feed, "--label", tool.LABEL,
            "--network", network, "--network-alias", "github.com",
            "--network-alias", "channelwatch.coderluii.dev",
            "--network-alias", "synthetic-dvr",
            "--mount", f"type=bind,source={root},target=/feed,readonly",
            "--entrypoint", "/usr/local/bin/python", reference, "/feed/feed.py",
        ])
        username, password = "capability-admin", secrets.token_urlsafe(36)
        tool.run([
            "docker", "run", "-d", "--name", app, "--label", tool.LABEL,
            "--network", network, "--platform", f"linux/{architecture}",
            "--mount", f"type=volume,source={volume},target=/config",
            "--mount", f"type=bind,source={ca_path},target=/canary/ca.pem,readonly",
            "--env", "SSL_CERT_FILE=/canary/ca.pem",
            "--env", f"CW_ADMIN_USER={username}",
            "--env", f"CW_ADMIN_PASS={password}",
            "--env", "PUID=1000", "--env", "PGID=1000",
            "-p", "127.0.0.1::8501", reference,
        ])
        client = tool.ApiClient(tool.published_base_url(app), username, password)
        tool.wait_for_image_health(client, args.startup_timeout, "initial readiness")
        client.login()
        before_hashes = tool.persistent_hashes(app)
        before_inventory = tool.image_inventory(app)
        before_identity = identity(
            app, image_id=image_id, manifest_digest=manifest_digest
        )

        source_check = tool.validate_update_check(
            client.request("/api/v1/update/check", {}),
            version=SOURCE_VERSION,
            descriptor=source_descriptor,
            bundle_sha256=source_variants[architecture]["bundle_sha256"],
        )
        source_job = tool.apply_once_and_wait(
            client,
            "/api/v1/update/apply",
            version=SOURCE_VERSION,
            job_id=secrets.token_hex(16),
            timeout=args.update_timeout,
        )
        tool.wait_for_image_health(client, args.update_timeout, "v1.6.0 readiness")
        source_apply = selected_status(
            app, client, version=SOURCE_VERSION, descriptor=source_descriptor,
            inventory=source_inventory, completed_job=source_job,
        )
        tool.run(["docker", "restart", app], timeout=120)
        client.set_base_url(tool.published_base_url(app))
        tool.wait_for_image_health(client, args.startup_timeout, "v1.6.0 restart")
        client.login()
        source_restart = selected_status(
            app, client, version=SOURCE_VERSION, descriptor=source_descriptor,
            inventory=source_inventory, completed_job=None,
        )
        shutil.copyfile(args.target_catalog, active_catalog)

        target_check = tool.validate_update_check(
            client.request("/api/v1/update/check", {}),
            version=args.target_version,
            descriptor=target_descriptor,
            bundle_sha256=target_variants[architecture]["bundle_sha256"],
        )
        target_job = tool.apply_once_and_wait(
            client,
            "/api/v1/update/apply",
            version=args.target_version,
            job_id=secrets.token_hex(16),
            timeout=args.update_timeout,
        )
        tool.wait_for_image_health(client, args.update_timeout, "candidate readiness")
        target_apply = selected_status(
            app, client, version=args.target_version, descriptor=target_descriptor,
            inventory=target_inventory, completed_job=target_job,
        )
        tool.run(["docker", "restart", app], timeout=120)
        client.set_base_url(tool.published_base_url(app))
        tool.wait_for_image_health(client, args.startup_timeout, "candidate restart")
        client.login()
        target_restart = selected_status(
            app, client, version=args.target_version, descriptor=target_descriptor,
            inventory=target_inventory, completed_job=None,
        )
        rollback_response = tool.validate_rollback_response(
            client.request("/api/v1/update/rollback", {}),
            source_version=args.target_version,
            target_version=SOURCE_VERSION,
        )
        tool.wait_until(
            lambda: client.request("/api/v1/update/status").get("current_version")
            == SOURCE_VERSION,
            args.update_timeout,
            "rollback to v1.6.0",
        )
        tool.wait_for_image_health(client, args.update_timeout, "v1.6.0 rollback readiness")
        client.login()
        rollback_job = tool.wait_for_successful_job(
            client,
            job_id=rollback_response["job_id"],
            timeout=args.update_timeout,
            description="rollback to v1.6.0",
        )
        rollback_status = selected_status(
            app, client, version=SOURCE_VERSION, descriptor=source_descriptor,
            inventory=source_inventory, completed_job=None,
        )
        reapply_job = tool.apply_once_and_wait(
            client,
            "/api/v1/update/retry",
            version=args.target_version,
            job_id=secrets.token_hex(16),
            timeout=args.update_timeout,
        )
        tool.wait_for_image_health(client, args.update_timeout, "candidate reapply")
        reapply_status = selected_status(
            app, client, version=args.target_version, descriptor=target_descriptor,
            inventory=target_inventory, completed_job=reapply_job,
        )

        after_identity = identity(
            app, image_id=image_id, manifest_digest=manifest_digest
        )
        after_inventory = tool.image_inventory(app)
        tool.validate_image_inventory(before_inventory, after_inventory)
        tool.validate_preservation(before_hashes, tool.persistent_hashes(app))
        if before_identity != after_identity:
            raise tool.CapabilityError("Container or image identity changed.")
        receipt.update({
            "status": "passed",
            "source_check": source_check,
            "source_apply": source_apply,
            "source_restart": source_restart,
            "target_check": target_check,
            "target_apply": target_apply,
            "target_restart": target_restart,
            "rollback_status": rollback_status,
            "reapply_status": reapply_status,
            "identity": {"before": before_identity, "after": after_identity},
            "image_inventory_sha256": before_inventory["sha256"],
            "checks": {
                "signed_source": True,
                "signed_target": True,
                "source_apply_restart": True,
                "target_apply_restart": True,
                "rollback_to_1.6.0": True,
                "reapply": True,
                "same_container": True,
                "same_image": True,
                "image_packages_unchanged": True,
                "persistent_state": True,
            },
        })
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["failure"] = str(exc)[:1000]
    finally:
        logs = {}
        log_failures = []
        for name, container in (("app", app), ("feed", feed)):
            result = tool.run(["docker", "logs", container], check=False, timeout=30)
            path = args.evidence_json.parent / f"{args.evidence_json.stem}-{name}.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.stdout + result.stderr, encoding="utf-8")
            logs[name] = {
                "file": path.name,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            if result.returncode != 0 or path.stat().st_size == 0:
                log_failures.append(f"docker_logs:{name}")
        receipt["logs"] = logs
        cleanup_failures = resources.cleanup()
        shutil.rmtree(root, ignore_errors=True)
        receipt["cleanup"] = {
            "status": "passed" if not cleanup_failures else "failed",
            "failures": cleanup_failures,
        }
        if (cleanup_failures or log_failures) and receipt.get("status") == "passed":
            receipt["status"] = "failed"
            receipt["failure"] = (
                "Native Docker evidence retention failed: "
                + ", ".join(log_failures + cleanup_failures)
            )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-catalog", type=Path, required=True)
    parser.add_argument("--source-amd64-bundle", type=Path, required=True)
    parser.add_argument("--source-arm64-bundle", type=Path, required=True)
    parser.add_argument("--target-catalog", type=Path, required=True)
    parser.add_argument("--target-amd64-bundle", type=Path, required=True)
    parser.add_argument("--target-arm64-bundle", type=Path, required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--image-repository", default="coderluii/channelwatch")
    parser.add_argument("--image-index-digest", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--update-timeout", type=int, default=900)
    args = parser.parse_args()
    args.target_version = args.target_version.strip().lstrip("v")
    if args.target_version != "1.6.1" or SHA40.fullmatch(args.target_commit) is None:
        raise SystemExit("This verifier is bounded to the exact v1.6.1 candidate.")
    args.evidence_json.parent.mkdir(parents=True, exist_ok=True)
    receipt = qualify(args)
    args.evidence_json.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
