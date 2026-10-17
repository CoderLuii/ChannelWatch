from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "release" / "verify-minimum-image-app-update.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("minimum_image_canary", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_identity() -> dict[str, object]:
    generation = "a" * 64
    return {
        "version": "1.6.0",
        "path": f"/config/channelwatch-runtime/releases/{generation}",
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "runtime_generation_id": generation,
        "runtime_descriptor_sha256": generation,
        "dependency_inventory_sha256": "b" * 64,
        "runtime_trust_sha256": "c" * 64,
        "manifest": {
            "release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v1.6.0",
            "bundle_sha256": "d" * 64,
            "key_id": "channelwatch-release-2026",
        },
        "metadata": {
            "version": "1.6.0",
            "version_tag": "v1.6.0",
            "runtime_abi": "channelwatch-runtime-v1",
            "settings_schema_version": 7,
            "git_sha": "e" * 40,
            "bundle_type": "channelwatch-runtime-v1",
        },
    }


def _active_identity() -> dict[str, object]:
    return {
        **_expected_identity(),
        "activation_protocol": 4,
        "activation_id": "f" * 32,
        "activated_at": "2026-10-17T00:00:00Z",
    }


def _converged_state() -> dict[str, object]:
    return {
        "health": {"live": True, "startup": True},
        "supervisor": (
            "core RUNNING pid 101, uptime 0:00:05\n"
            "ui RUNNING pid 102, uptime 0:00:05\n"
        ),
        "active": _active_identity(),
        "job": {
            "job_id": "apply-job",
            "operation": "apply",
            "status": "success",
            "version": "1.6.0",
            "bundle_sha256": "d" * 64,
        },
        "restart_required": False,
        "handoff": False,
        "activation_artifacts": [],
    }


def test_referenced_static_assets_include_only_local_script_and_link_paths():
    canary = _load_script()

    paths = canary.referenced_static_assets(b"""
        <html><head>
          <link rel="stylesheet" href="/_next/static/app.css?build=1">
          <link rel="preconnect" href="https://example.com">
        </head><body>
          <script src="/_next/static/app.js"></script>
          <script src="https://example.com/external.js"></script>
          <img src="/images/not-a-script-or-link.png">
        </body></html>
        """)

    assert paths == ["/_next/static/app.css", "/_next/static/app.js"]


def test_referenced_static_assets_deduplicate_repeated_members():
    canary = _load_script()

    paths = canary.referenced_static_assets(
        b'<script src="/_next/static/app.js"></script>'
        b'<script src="/_next/static/app.js"></script>'
    )

    assert paths == ["/_next/static/app.js"]


def test_protocol_four_update_is_applied_as_runtime_owner(monkeypatch):
    canary = _load_script()
    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        canary,
        "docker_exec",
        lambda name, code, user=None: calls.append((name, code, user))
        or json.dumps(
            {"job": {
                "job_id": "apply-job",
                "operation": "apply",
                "status": "restarting",
                "version": "1.6.0",
                "bundle_sha256": "d" * 64,
            }, "active": _active_identity()}
        ),
    )

    active, job_id = canary.apply_runtime_update(
        "canary", "1.5.3", _expected_identity()
    )

    assert active["activation_protocol"] == 4
    assert job_id
    assert calls == [("canary", calls[0][1], "501:20")]
    code = calls[0][1]
    assert "UpdateManager" in code
    assert "launcher_protocol=4" in code
    assert "Path('/config/minimum-image-canary')" in code
    assert "root/'catalog.json'" in code
    assert "root/'runtime.zip'" in code
    assert "manager.check()" in code
    assert "manager.apply('1.6.0')" in code
    assert "request_container_restart()" in code
    assert "restart_callable=lambda: True" not in code
    compile(code, "<minimum-image-canary>", "exec")


@pytest.mark.parametrize(
    "mutation",
    (
        lambda state: state["health"].update(live=False),
        lambda state: state["health"].update(startup=False),
        lambda state: state.update(supervisor="core RUNNING pid 101, uptime 0:00:05\nui STARTING"),
        lambda state: state.update(active={**_active_identity(), "activation_id": "other"}),
        lambda state: state["job"].update(job_id="stale-apply-job"),
        lambda state: state["job"].update(operation="check"),
        lambda state: state["job"].update(status="restarting"),
        lambda state: state["job"].update(version="1.5.3"),
        lambda state: state["job"].update(bundle_sha256="9" * 64),
        lambda state: state.update(restart_required=True),
        lambda state: state.update(handoff=True),
        lambda state: state.update(activation_artifacts=["activation-ui-ready.json"]),
    ),
)
def test_activation_convergence_rejects_partial_or_mismatched_state(mutation):
    canary = _load_script()
    state = _converged_state()
    mutation(state)

    assert not canary._activation_state_converged(
        state,
        _active_identity(),
        _expected_identity(),
        "apply-job",
    )


def test_activation_convergence_accepts_exact_durable_success():
    canary = _load_script()

    assert canary._activation_state_converged(
        _converged_state(),
        _active_identity(),
        _expected_identity(),
        "apply-job",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version", "1.5.3"),
        ("runtime_generation_id", "9" * 64),
        ("runtime_descriptor_sha256", "9" * 64),
        ("dependency_inventory_sha256", "9" * 64),
        ("runtime_trust_sha256", "9" * 64),
        ("manifest.bundle_sha256", "9" * 64),
    ),
)
def test_protocol_four_active_identity_rejects_signed_target_mismatch(field, value):
    canary = _load_script()
    active = _active_identity()
    if field.startswith("manifest."):
        active["manifest"] = {**active["manifest"], field.split(".")[1]: value}
    else:
        active[field] = value

    with pytest.raises(canary.CanaryError, match="identity"):
        canary._validated_protocol_four_active(active, _expected_identity())


def test_expected_identity_binds_signed_catalog_bundle_and_descriptor(
    tmp_path, monkeypatch
):
    canary = _load_script()
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(b'{"schema":3,"signed":"raw"}')
    descriptor = {
        "application_version": "1.6.0",
        "architecture": "amd64",
        "source_commit": "e" * 40,
        "generation_id": "a" * 64,
        "dependency_inventory_sha256": "b" * 64,
        "bundle_format": "channelwatch-runtime-v1",
    }
    bundle = tmp_path / "runtime.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("channelwatch-bundle.json", json.dumps(descriptor))
    bundle_digest = canary.hashlib.sha256(bundle.read_bytes()).hexdigest()
    selected = {
        "payload": {
            "version": "1.6.0",
            "version_tag": "v1.6.0",
            "runtime_abi": "channelwatch-runtime-v1",
            "settings_schema_version": 7,
            "release_url": "https://example.invalid/v1.6.0",
            "bundle_sha256": bundle_digest,
            "bundle_signature": "signed-bundle",
            "key_id": "release-key",
            "runtime_descriptor": descriptor,
        },
        "signature": {"key_id": "catalog-key"},
    }
    import core.runtime_generation as runtime_generation
    import core.update_center as update_center

    monkeypatch.setattr(
        update_center, "read_update_document_bytes", lambda *_args, **_kwargs: selected
    )
    signature_calls = []
    monkeypatch.setattr(
        update_center,
        "verify_ed25519_signature",
        lambda *args: signature_calls.append(args),
    )
    monkeypatch.setattr(
        runtime_generation, "descriptor_generation_id", lambda value: value["generation_id"]
    )
    monkeypatch.setattr(runtime_generation, "validate_runtime_descriptor", lambda *_: None)

    expected = canary.expected_runtime_identity(
        catalog, bundle, "1.5.3", "1.6.0"
    )

    assert expected["runtime_generation_id"] == "a" * 64
    assert expected["dependency_inventory_sha256"] == "b" * 64
    assert expected["runtime_trust_sha256"] == canary.hashlib.sha256(
        catalog.read_bytes()
    ).hexdigest()
    assert expected["manifest"]["bundle_sha256"] == bundle_digest
    assert signature_calls[0][1:] == (
        "release-key",
        "signed-bundle",
        bytes.fromhex(bundle_digest),
    )


def test_protocol_four_active_reapply_preserves_verified_identity(monkeypatch):
    canary = _load_script()
    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        canary,
        "docker_exec",
        lambda name, code, user=None: calls.append((name, code, user)) or "",
    )
    active = _active_identity()

    canary.write_active("canary", active, _expected_identity())
    canary.remove_active("canary")

    assert [user for _, _, user in calls] == ["501:20", "501:20"]
    assert '"activation_protocol":4' in calls[0][1]
    assert '"runtime_generation_id":"' + "a" * 64 in calls[0][1]
    assert "atomic_write_json" in calls[0][1]
    assert ".unlink()" in calls[1][1]


def test_protocol_four_active_reapply_rejects_app_only_identity(monkeypatch):
    canary = _load_script()
    monkeypatch.setattr(canary, "docker_exec", lambda *_args, **_kwargs: "")

    try:
        canary.write_active(
            "canary",
            {"version": "1.6.0", "activation_protocol": 3},
            _expected_identity(),
        )
    except canary.CanaryError as exc:
        assert "protocol-4" in str(exc)
    else:
        raise AssertionError("app-only active identity was accepted")


def test_failure_diagnostics_are_retained_before_container_cleanup(tmp_path, monkeypatch):
    canary = _load_script()

    def fake_run(command, *, check=True):
        del check
        if command[:2] == ["docker", "logs"]:
            return canary.subprocess.CompletedProcess(command, 0, "runtime stderr\n", "")
        return canary.subprocess.CompletedProcess(command, 0, "core FATAL\n", "")

    monkeypatch.setattr(canary, "run", fake_run)
    evidence = tmp_path / "canary.json"

    retained = canary.retain_failure_diagnostics(
        "canary", evidence, canary.CanaryError("restart failed")
    )

    assert retained["error"] == "restart failed"
    assert (tmp_path / "canary-docker.log").read_text() == "runtime stderr\n"
    assert (tmp_path / "canary-supervisor.log").read_text() == "core FATAL\n"
    assert json.loads((tmp_path / "canary-failure.json").read_text())["error"] == (
        "restart failed"
    )


def test_main_revalidates_exact_identity_after_every_restart(tmp_path, monkeypatch):
    canary = _load_script()
    app_bundle = tmp_path / "app.zip"
    runtime_bundle = tmp_path / "runtime.zip"
    catalog = tmp_path / "catalog.json"
    evidence = tmp_path / "evidence.json"
    for path in (app_bundle, runtime_bundle, catalog):
        path.write_bytes(b"fixture")
    expected = _expected_identity()
    selected = _active_identity()
    active_reads = []
    supervisor_restarts = []
    orchestration = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canary",
            "--bundle",
            str(app_bundle),
            "--runtime-bundle",
            str(runtime_bundle),
            "--runtime-catalog",
            str(catalog),
            "--version",
            "1.6.0",
            "--minimum-image-version",
            "1.5.3",
            "--image-index-digest",
            "sha256:" + "1" * 64,
            "--evidence-json",
            str(evidence),
        ],
    )
    monkeypatch.setattr(
        canary,
        "pinned_image",
        lambda *_: (
            "repository@sha256:" + "2" * 64,
            "sha256:" + "2" * 64,
        ),
    )
    monkeypatch.setattr(
        canary,
        "run",
        lambda command, check=True: canary.subprocess.CompletedProcess(
            command, 0, "", ""
        ),
    )
    monkeypatch.setattr(
        canary,
        "verify_pulled_image",
        lambda *_: "sha256:" + "3" * 64,
    )
    monkeypatch.setattr(canary, "wait_for_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(canary, "expected_runtime_identity", lambda *_: expected)
    monkeypatch.setattr(
        canary, "apply_runtime_update", lambda *_: (selected, "apply-job")
    )
    monkeypatch.setattr(
        canary,
        "wait_for_activation_convergence",
        lambda *_args, **_kwargs: orchestration.append("settled"),
    )
    monkeypatch.setattr(
        canary,
        "restart",
        lambda _name, target="all": (
            orchestration.append(f"restart:{target}"),
            supervisor_restarts.append(target),
        ),
    )
    monkeypatch.setattr(canary, "remove_active", lambda *_: None)
    monkeypatch.setattr(canary, "write_active", lambda *_: None)
    monkeypatch.setattr(
        canary,
        "read_active",
        lambda name, identity: active_reads.append((name, identity)) or selected,
    )
    monkeypatch.setattr(
        canary,
        "verify_frontend",
        lambda *_: {"index_matches": True, "referenced_assets_verified": 2},
    )
    monkeypatch.setattr(canary.secrets, "token_hex", lambda *_: "fixed")

    assert canary.main() == 0
    assert active_reads == [("cw-minimum-image-canary-fixed", expected)] * 4
    assert supervisor_restarts == ["ui", "all", "all"]
    assert orchestration[:2] == ["settled", "restart:ui"]
