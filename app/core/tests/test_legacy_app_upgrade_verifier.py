import copy
import io
import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/verify-legacy-app-upgrade.py"


def load_module():
    spec = importlib.util.spec_from_file_location("legacy_app_upgrade", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def image_process_runtime():
    return {
        "pid": 123,
        "python_executable": "/venv/bin/python",
        "python_prefix": "/venv",
        "python_version": "3.14.8",
        "dependencies": {
            "requests": {"loaded": False},
            "cryptography": {
                "loaded": True,
                "path": "/venv/lib/python3.14/site-packages/cryptography/__init__.py",
                "version": "50.0.2",
            },
            "sqlmodel": {
                "loaded": True,
                "path": "/venv/lib/python3.14/site-packages/sqlmodel/__init__.py",
                "version": "0.0.48",
            },
            "uvicorn": {"loaded": False},
            "pydantic_core": {
                "loaded": True,
                "path": "/venv/lib/python3.14/site-packages/pydantic_core/__init__.py",
                "version": "2.50.0",
            },
            "sqlalchemy": {
                "loaded": True,
                "path": "/venv/lib/python3.14/site-packages/sqlalchemy/__init__.py",
                "version": "2.0.54",
            },
            "httptools": {"loaded": False},
        },
    }


def selected_status(version="1.6.0"):
    return {
        "current_version": version,
        "image_version": "1.4.0",
        "launcher_protocol": 3,
        "runtime_source": "app_bundle",
        "operation_busy": False,
        "active_bundle": {
            "version": version,
            "path": f"/config/channelwatch-runtime/releases/v{version}",
            "manifest": {"bundle_sha256": "a" * 64},
        },
        "worker_runtime": {**image_process_runtime(), "verified_generation": None},
    }


def overlay_status(*, job_id="b" * 32, busy=False, state="idle"):
    return {
        "current_version": "1.4.5",
        "image_version": "1.4.1",
        "launcher_protocol": 3,
        "runtime_source": "app_bundle",
        "active_bundle": {"version": "1.4.5"},
        "last_job": {
            "job_id": job_id,
            "operation": "apply",
            "status": "success",
            "version": "1.4.5",
            "bundle_sha256": "9" * 64,
        },
        "operation_busy": busy,
        "operation_state": state,
    }


def overlay_scenario():
    return {
        "image_version": "1.4.1",
        "overlay_version": "1.4.5",
        "source_application_version": "1.4.5",
    }


def overlay_job():
    return {
        "job_id": "b" * 32,
        "operation": "apply",
        "status": "success",
        "version": "1.4.5",
        "bundle_sha256": "9" * 64,
    }


def available_source_status(*, busy=False, state="idle", job_id="c" * 32):
    return {
        "current_version": "1.4.5",
        "image_version": "1.4.1",
        "launcher_protocol": 3,
        "runtime_source": "app_bundle",
        "active_bundle": {"version": "1.4.5"},
        "trusted_target": {
            "version": "1.6.0",
            "bundle_sha256": "a" * 64,
        },
        "update_available": True,
        "image_required": False,
        "last_job": {
            "job_id": job_id,
            "operation": "check",
            "status": "available",
            "version": "1.6.0",
        },
        "operation_busy": busy,
        "operation_state": state,
    }


def test_overlay_source_waits_for_exact_successful_job_and_idle_operation(monkeypatch):
    module = load_module()
    statuses = [
        overlay_status(busy=True, state="checking"),
        overlay_status(busy=False, state="idle"),
    ]

    class Client:
        @staticmethod
        def request(path):
            assert path == "/api/v1/update/status"
            return statuses.pop(0)

    def wait_until(predicate, timeout, label):
        assert timeout == 41
        assert label == "overlay operation idle"
        while statuses:
            result = predicate()
            if result is not None:
                return result
        raise AssertionError("overlay status never became idle")

    monkeypatch.setattr(module.image_tool, "wait_until", wait_until)
    result = module.wait_for_overlay_idle(
        Client(),
        scenario=overlay_scenario(),
        overlay_job=overlay_job(),
        bundle_sha256="9" * 64,
        timeout=41,
    )

    assert result["operation_busy"] is False
    assert result["operation_state"] == "idle"
    assert statuses == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda status: status.pop("operation_busy"),
        lambda status: status.update(operation_busy=0),
        lambda status: status.update(operation_state="checking"),
        lambda status: status["last_job"].update(job_id="c" * 32),
        lambda status: status["last_job"].update(status="restarting"),
        lambda status: status["active_bundle"].update(version="1.4.1"),
        lambda status: status.update(runtime_source="image"),
    ],
)
def test_overlay_source_rejects_ambiguous_or_foreign_idle_status(monkeypatch, mutation):
    module = load_module()
    status = overlay_status()
    mutation(status)

    class Client:
        @staticmethod
        def request(_path):
            return status

    monkeypatch.setattr(
        module.image_tool,
        "wait_until",
        lambda predicate, _timeout, _label: predicate(),
    )
    with pytest.raises(module.LegacyAppUpgradeError):
        module.wait_for_overlay_idle(
            Client(),
            scenario=overlay_scenario(),
            overlay_job=overlay_job(),
            bundle_sha256="9" * 64,
            timeout=41,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda job: job.update(job_id="not-a-job"),
        lambda job: job.update(operation="rollback"),
        lambda job: job.update(status="restarting"),
        lambda job: job.update(version="1.4.1"),
    ],
)
def test_overlay_source_rejects_unbound_completed_job(monkeypatch, mutation):
    module = load_module()
    job = overlay_job()
    mutation(job)
    monkeypatch.setattr(
        module.image_tool,
        "wait_until",
        lambda *_args: pytest.fail("invalid overlay job must fail before polling"),
    )

    with pytest.raises(module.LegacyAppUpgradeError, match="exact successful job"):
        module.wait_for_overlay_idle(
            object(),
            scenario=overlay_scenario(),
            overlay_job=job,
            bundle_sha256="9" * 64,
            timeout=41,
        )


def test_overlay_source_busy_state_must_reach_idle_within_existing_timeout(monkeypatch):
    module = load_module()
    status = overlay_status(busy=True, state="checking")

    class Client:
        @staticmethod
        def request(_path):
            return status

    def wait_until(predicate, timeout, label):
        assert timeout == 41
        assert label == "overlay operation idle"
        assert predicate() is None
        raise TimeoutError("existing update timeout expired")

    monkeypatch.setattr(module.image_tool, "wait_until", wait_until)
    with pytest.raises(TimeoutError, match="existing update timeout"):
        module.wait_for_overlay_idle(
            Client(),
            scenario=overlay_scenario(),
            overlay_job=overlay_job(),
            bundle_sha256="9" * 64,
            timeout=41,
        )


def test_overlay_idle_gate_precedes_target_feed_switch():
    source = SCRIPT.read_text(encoding="utf-8")
    overlay_apply = source.index("overlay_job = wait_job(")
    idle_gate = source.index("wait_for_overlay_idle(", overlay_apply)
    feed_switch = source.index("capability_tool.publish_feed_catalog(args.catalog, catalog_asset)", idle_gate)

    assert overlay_apply < idle_gate < feed_switch


def test_target_check_waits_past_cached_busy_response_for_exact_idle_job(monkeypatch):
    module = load_module()
    checked = available_source_status(busy=True, state="checking")
    checked["last_job"] = overlay_job()
    checked["trusted_target"] = None
    checked["update_available"] = False
    statuses = [
        copy.deepcopy(checked),
        available_source_status(),
    ]

    class Client:
        @staticmethod
        def request(path):
            assert path == "/api/v1/update/status"
            return statuses.pop(0)

    def wait_until(predicate, timeout, label):
        assert timeout == 41
        assert label == "target update check idle"
        while statuses:
            result = predicate()
            if result is not None:
                return result
        raise AssertionError("target check never became idle")

    monkeypatch.setattr(module.image_tool, "wait_until", wait_until)
    result = module.wait_for_target_check_idle(
        Client(),
        checked=checked,
        scenario=overlay_scenario(),
        target_version="1.6.0",
        bundle_sha256="a" * 64,
        timeout=41,
    )

    assert result["last_job"]["job_id"] == "c" * 32
    assert result["operation_state"] == "idle"


def test_target_check_orchestrator_passes_raw_cached_busy_response_to_fence(
    monkeypatch,
):
    module = load_module()
    raw_busy = available_source_status(busy=True, state="checking")
    raw_busy["trusted_target"] = None
    raw_busy["update_available"] = False
    idle = available_source_status()
    observed = []

    class Client:
        @staticmethod
        def request(path, payload):
            assert path == "/api/v1/update/check"
            assert payload == {}
            return raw_busy

    def fence(client, **kwargs):
        observed.append((client, kwargs))
        assert kwargs["checked"] is raw_busy
        return idle

    monkeypatch.setattr(module, "wait_for_target_check_idle", fence)
    result = module.check_target_until_idle(
        Client(),
        scenario=overlay_scenario(),
        target_version="1.6.0",
        bundle_sha256="a" * 64,
        timeout=41,
    )

    assert result is idle
    assert len(observed) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda status: status.update(current_version="1.4.1"),
        lambda status: status.update(image_version="1.4.0"),
        lambda status: status["trusted_target"].update(version="1.6.1"),
        lambda status: status["trusted_target"].update(bundle_sha256="f" * 64),
        lambda status: status["last_job"].update(operation="apply"),
        lambda status: status["last_job"].update(status="success"),
        lambda status: status["last_job"].update(version="1.5.3"),
        lambda status: status.update(operation_state="checking"),
    ],
)
def test_target_check_rejects_foreign_idle_identity(monkeypatch, mutation):
    module = load_module()
    checked = available_source_status()
    status = available_source_status()
    mutation(status)

    class Client:
        @staticmethod
        def request(_path):
            return status

    monkeypatch.setattr(
        module.image_tool, "wait_until", lambda predicate, _timeout, _label: predicate()
    )
    with pytest.raises(module.LegacyAppUpgradeError):
        module.wait_for_target_check_idle(
            Client(),
            checked=checked,
            scenario=overlay_scenario(),
            target_version="1.6.0",
            bundle_sha256="a" * 64,
            timeout=41,
        )


def test_target_check_busy_state_uses_existing_timeout(monkeypatch):
    module = load_module()
    checked = available_source_status(busy=True, state="checking")

    class Client:
        @staticmethod
        def request(_path):
            return checked

    def wait_until(predicate, timeout, label):
        assert timeout == 41
        assert label == "target update check idle"
        assert predicate() is None
        raise TimeoutError("existing update timeout expired")

    monkeypatch.setattr(module.image_tool, "wait_until", wait_until)
    with pytest.raises(TimeoutError, match="existing update timeout"):
        module.wait_for_target_check_idle(
            Client(),
            checked=checked,
            scenario=overlay_scenario(),
            target_version="1.6.0",
            bundle_sha256="a" * 64,
            timeout=41,
        )


@pytest.mark.parametrize(
    ("operation", "status", "version", "bundle_sha256", "description"),
    [
        ("apply", "success", "1.6.0", "a" * 64, "legacy apply operation idle"),
        ("check", "current", "1.6.0", None, "post-restart update check idle"),
        ("rollback", "success", "1.6.0", None, "legacy rollback operation idle"),
        ("apply", "success", "1.6.0", None, "legacy reapply operation idle"),
    ],
)
def test_operation_fence_binds_each_transition_and_existing_timeout(
    monkeypatch, operation, status, version, bundle_sha256, description
):
    module = load_module()
    job = {
        "job_id": "d" * 32,
        "operation": operation,
        "status": status,
        "version": version,
    }
    if bundle_sha256 is not None:
        job["bundle_sha256"] = bundle_sha256
    busy = {
        "source_identity": "expected",
        "operation_busy": True,
        "operation_state": (
            "rolling_back" if operation == "rollback" else "checking"
        ),
        "last_job": overlay_job(),
    }
    idle = {
        "source_identity": "expected",
        "operation_busy": False,
        "operation_state": "idle",
        "last_job": copy.deepcopy(job),
    }
    statuses = [busy, idle]

    class Client:
        @staticmethod
        def request(_path):
            return statuses.pop(0)

    def wait_until(predicate, timeout, label):
        assert timeout == 41
        assert label == description
        while statuses:
            result = predicate()
            if result is not None:
                return result
        raise AssertionError("operation did not become idle")

    monkeypatch.setattr(module.image_tool, "wait_until", wait_until)
    result = module.wait_for_job_idle(
        Client(),
        job=job,
        operation=operation,
        status=status,
        version=version,
        bundle_sha256=bundle_sha256,
        timeout=41,
        description=description,
        status_validator=lambda value: (
            value
            if value.get("source_identity") == "expected"
            else pytest.fail("foreign source identity")
        ),
    )

    assert result["last_job"] == job


@pytest.mark.parametrize(
    ("operation", "busy_state"),
    [("apply", "rolling_back"), ("check", "rolling_back"), ("rollback", "checking")],
)
def test_operation_fence_rejects_wrong_operation_specific_busy_state(
    monkeypatch, operation, busy_state
):
    module = load_module()
    version = "1.6.0"
    job = {
        "job_id": "d" * 32,
        "operation": operation,
        "status": "current" if operation == "check" else "success",
        "version": version,
    }
    current = {
        "operation_busy": True,
        "operation_state": busy_state,
        "last_job": copy.deepcopy(job),
    }

    class Client:
        @staticmethod
        def request(_path):
            return current

    monkeypatch.setattr(
        module.image_tool, "wait_until", lambda predicate, _timeout, _label: predicate()
    )
    with pytest.raises(module.LegacyAppUpgradeError, match="state is ambiguous"):
        module.wait_for_job_idle(
            Client(),
            job=job,
            operation=operation,
            status=job["status"],
            version=version,
            timeout=41,
            description="operation idle",
            status_validator=lambda _value: None,
        )


def test_post_restart_fence_binds_durable_post_activation_check_not_old_apply(
    monkeypatch,
):
    module = load_module()
    applied = {
        "job_id": "d" * 32,
        "operation": "apply",
        "status": "success",
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
    }
    prior_check = {
        "job_id": "e" * 32,
        "operation": "check",
        "status": "current",
        "version": "1.6.0",
    }
    current = {
        "source_identity": "selected-target",
        "operation_busy": False,
        "operation_state": "idle",
        "last_job": copy.deepcopy(prior_check),
    }

    class Client:
        @staticmethod
        def request(_path):
            return current

    monkeypatch.setattr(
        module.image_tool, "wait_until", lambda predicate, _timeout, _label: predicate()
    )
    validate = lambda value: value if value["source_identity"] == "selected-target" else None

    assert module.wait_for_job_idle(
        Client(),
        job=prior_check,
        operation="check",
        status="current",
        version="1.6.0",
        timeout=41,
        description="post-restart prior check idle",
        status_validator=validate,
    )["last_job"] == prior_check
    with pytest.raises(module.LegacyAppUpgradeError, match="expected job"):
        module.wait_for_job_idle(
            Client(),
            job=applied,
            operation="apply",
            status="success",
            version="1.6.0",
            bundle_sha256="a" * 64,
            timeout=41,
            description="wrong old apply fence",
            status_validator=validate,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(source_identity="foreign"),
        lambda value: value["last_job"].update(job_id="e" * 32),
        lambda value: value["last_job"].update(operation="rollback"),
        lambda value: value["last_job"].update(status="failed"),
        lambda value: value["last_job"].update(version="1.5.3"),
        lambda value: value["last_job"].update(bundle_sha256="f" * 64),
    ],
)
def test_operation_fence_rejects_foreign_source_target_job_or_digest(
    monkeypatch, mutation
):
    module = load_module()
    job = {
        "job_id": "d" * 32,
        "operation": "apply",
        "status": "success",
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
    }
    current = {
        "source_identity": "expected",
        "operation_busy": False,
        "operation_state": "idle",
        "last_job": copy.deepcopy(job),
    }
    mutation(current)

    class Client:
        @staticmethod
        def request(_path):
            return current

    monkeypatch.setattr(
        module.image_tool, "wait_until", lambda predicate, _timeout, _label: predicate()
    )

    def validate(value):
        if value.get("source_identity") != "expected":
            raise module.LegacyAppUpgradeError("Foreign source identity.")

    with pytest.raises(module.LegacyAppUpgradeError):
        module.wait_for_job_idle(
            Client(),
            job=job,
            operation="apply",
            status="success",
            version="1.6.0",
            bundle_sha256="a" * 64,
            timeout=41,
            description="legacy apply operation idle",
            status_validator=validate,
        )


@pytest.mark.parametrize("version", ["1.4.0", "1.4.1", "1.4.5"])
def test_scheduler_seed_is_compatible_with_each_legacy_state_schema(
    monkeypatch, version
):
    module = load_module()
    observed = []

    def run(command, *, timeout):
        observed.append((command, timeout))
        return type(
            "Result",
            (),
            {
                "stdout": json.dumps(
                    {
                        "schema": 1,
                        "next_check_at": "2026-10-16T23:00:00Z",
                        "cadence_seconds": 21600,
                        "policy_unchanged": True,
                        "automatic_scheduler_enabled": True,
                        "policy_mode": "automatic",
                    }
                )
            },
        )()

    monkeypatch.setattr(module.image_tool, "run", run)
    evidence = module.seed_scheduler_between_checks(
        f"ghcr.io/coderluii/channelwatch:{version}", "legacy-config"
    )

    command, timeout = observed[0]
    assert timeout == 60
    assert command[command.index("--entrypoint") + 1] == "/venv/bin/python"
    assert f"ghcr.io/coderluii/channelwatch:{version}" in command
    assert "type=volume,source=legacy-config,target=/config" in command
    code = command[-1]
    assert "UpdatePolicyStore" in code
    assert "next_check_at" in code
    assert "mode') != 'automatic'" in code
    assert evidence["policy_unchanged"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence.update(schema="1"),
        lambda evidence: evidence.update(next_check_at="not-a-timestamp"),
        lambda evidence: evidence.update(cadence_seconds=60),
        lambda evidence: evidence.update(policy_unchanged=False),
        lambda evidence: evidence.update(automatic_scheduler_enabled=False),
        lambda evidence: evidence.update(policy_mode="notify_only"),
    ],
)
def test_scheduler_seed_fails_closed_on_unexpected_state(monkeypatch, mutation):
    module = load_module()
    evidence = {
        "schema": 1,
        "next_check_at": "2026-10-16T23:00:00Z",
        "cadence_seconds": 21600,
        "policy_unchanged": True,
        "automatic_scheduler_enabled": True,
        "policy_mode": "automatic",
    }
    mutation(evidence)
    monkeypatch.setattr(
        module.image_tool,
        "run",
        lambda *_args, **_kwargs: type(
            "Result", (), {"stdout": json.dumps(evidence)}
        )(),
    )

    with pytest.raises(module.LegacyAppUpgradeError, match="seed evidence"):
        module.seed_scheduler_between_checks("legacy:1.4.0", "legacy-config")


def test_scheduler_checkpoint_requires_exact_future_six_hour_cadence(monkeypatch):
    module = load_module()
    evidence = {
        "schema": 1,
        "next_check_at": "2026-10-17T06:00:00Z",
        "last_success_at": "2026-10-17T00:00:00Z",
        "cadence_seconds": 21600,
        "policy_mode": "automatic",
    }
    observed = []
    monkeypatch.setattr(
        module.image_tool,
        "_docker_json",
        lambda container, code: observed.append((container, code)) or evidence,
    )

    assert module.require_scheduler_checkpoint(
        "legacy-app", expected_schema=1
    ) == evidence
    assert observed[0][0] == "legacy-app"
    assert "next_check - last_success" in observed[0][1]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema=2),
        lambda value: value.update(cadence_seconds=60),
        lambda value: value.update(policy_mode="notify_only"),
    ],
)
def test_scheduler_checkpoint_rejects_foreign_state(monkeypatch, mutation):
    module = load_module()
    evidence = {
        "schema": 1,
        "next_check_at": "2026-10-17T06:00:00Z",
        "last_success_at": "2026-10-17T00:00:00Z",
        "cadence_seconds": 21600,
        "policy_mode": "automatic",
    }
    mutation(evidence)
    monkeypatch.setattr(
        module.image_tool, "_docker_json", lambda *_args: evidence
    )

    with pytest.raises(module.LegacyAppUpgradeError, match="between-checks checkpoint"):
        module.require_scheduler_checkpoint("legacy-app", expected_schema=1)


def test_scheduler_seed_and_strict_idle_fences_cover_every_mutation_boundary():
    source = SCRIPT.read_text(encoding="utf-8")
    seed = source.index("receipt[\"scheduler_seed\"]")
    app_start = source.index('"docker", "run", "-d", "--pull=never"', seed)
    overlay_check = source.index("description=\"overlay update check idle\"")
    overlay_apply = source.index("overlay_job = wait_job(", overlay_check)
    overlay_reconciliation = source.index(
        "overlay_reconciled_attempt = restart_after_reconciliation(", overlay_apply
    )
    overlay_checkpoint = source.index(
        'receipt["overlay_scheduler_checkpoint"]', overlay_reconciliation
    )
    overlay_second_idle = source.index(
        "wait_for_overlay_idle(", overlay_checkpoint
    )
    feed_switch = source.index(
        "capability_tool.publish_feed_catalog(args.catalog, catalog_asset)", overlay_second_idle
    )
    target_check = source.index('receipt["target_check_idle_checkpoint"]')
    target_apply = source.index("applied = wait_job(", target_check)
    apply_idle = source.index('description="legacy apply operation idle"', target_apply)
    apply_reconciliation = source.index(
        "checked_current_after_reconciliation(", apply_idle
    )
    post_apply_check = source.index('description="post-activation update check idle"')
    restart_idle = source.index('description="post-restart prior check idle"')
    post_restart_check = source.index('description="post-restart update check idle"')
    rollback = source.index('client, "/api/v1/update/rollback"', post_restart_check)
    rollback_idle = source.index('description="legacy rollback operation idle"', rollback)
    retry = source.index('endpoint="/api/v1/update/retry"', rollback_idle)
    retry_idle = source.index('description="legacy reapply operation idle"', retry)
    rollback_fence = source[rollback:rollback_idle]
    reapply_fence = source[retry:retry_idle]

    assert seed < app_start
    assert (
        overlay_check
        < overlay_apply
        < overlay_reconciliation
        < overlay_checkpoint
        < overlay_second_idle
        < feed_switch
    )
    assert (
        target_check
        < target_apply
        < apply_idle
        < apply_reconciliation
        < post_apply_check
    )
    assert restart_idle < post_restart_check < rollback < rollback_idle < retry < retry_idle
    assert 'version=args.target_version' in rollback_fence
    assert 'validate_source_status(' in source[rollback_idle:retry]
    assert 'bundle_sha256=receipt["bundle_sha256"]' in reapply_fence


def test_selected_status_requires_protocol3_image_owned_python_without_generation():
    module = load_module()
    status = selected_status()
    assert module.validate_selected_status(
        status,
        target_version="1.6.0",
        image_version="1.4.0",
        bundle_sha256="a" * 64,
    ) == status


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(launcher_protocol=4),
        lambda value: value.update(runtime_source="image"),
        lambda value: value["worker_runtime"].update(verified_generation="fake"),
        lambda value: value["worker_runtime"].update(python_executable="/config/python"),
        lambda value: value["worker_runtime"].update(python_version="3.15.0"),
        lambda value: value["active_bundle"].update(version="1.5.3"),
        lambda value: value["active_bundle"].update(runtime_generation_id="f" * 64),
        lambda value: value.update(image_version="1.6.0"),
    ],
)
def test_selected_status_rejects_bootstrap_or_image_substitution(mutation):
    module = load_module()
    status = selected_status()
    mutation(status)
    with pytest.raises(module.LegacyAppUpgradeError):
        module.validate_selected_status(
            status,
            target_version="1.6.0",
            image_version="1.4.0",
            bundle_sha256="a" * 64,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda worker: worker.update(pid=True),
        lambda worker: worker.update(pid=0),
        lambda worker: worker.update(python_prefix="/config/python"),
        lambda worker: worker["dependencies"].pop("sqlalchemy"),
        lambda worker: worker["dependencies"].update(extra={"loaded": False}),
        lambda worker: worker["dependencies"]["cryptography"].update(loaded=False),
        lambda worker: worker["dependencies"]["sqlmodel"].update(path="/tmp/sqlmodel.py"),
        lambda worker: worker["dependencies"]["pydantic_core"].update(
            path="/venv/lib/python3.14/site-packages/../outside.py"
        ),
        lambda worker: worker["dependencies"]["requests"].update(version="2.32.5"),
    ],
)
def test_selected_status_rejects_unbound_worker_process_or_imports(mutation):
    module = load_module()
    status = selected_status()
    mutation(status["worker_runtime"])

    with pytest.raises(module.LegacyAppUpgradeError):
        module.validate_selected_status(
            status,
            target_version="1.6.0",
            image_version="1.4.0",
            bundle_sha256="a" * 64,
        )


def test_same_container_and_image_identity_is_fail_closed():
    module = load_module()
    identity = {
        "container_id": "c" * 64,
        "image_id": "sha256:" + "d" * 64,
        "repo_digest": "coderluii/channelwatch@sha256:" + "e" * 64,
    }
    module.validate_identity_invariant([identity, copy.deepcopy(identity)])
    changed = copy.deepcopy(identity)
    changed["container_id"] = "f" * 64
    with pytest.raises(module.LegacyAppUpgradeError):
        module.validate_identity_invariant([identity, changed])


def test_fixture_defines_exact_six_scenario_matrix_for_both_qualification_modes():
    module = load_module()
    fixture = json.loads(
        (ROOT / "scripts/release/legacy-app-upgrade-baselines.json").read_text()
    )
    scenarios = module.validate_baselines(fixture, target_version="1.6.2")
    assert [row["id"] for row in scenarios] == [
        "stock-1.4.0",
        "overlay-1.4.0-to-1.4.1",
        "overlay-1.4.0-to-1.4.5",
        "stock-1.4.1",
        "overlay-1.4.1-to-1.4.5",
        "stock-1.4.5",
    ]
    broken = copy.deepcopy(fixture)
    broken["scenarios"].pop()
    with pytest.raises(module.LegacyAppUpgradeError):
        module.validate_baselines(broken, target_version="1.6.2")


def test_fixture_binds_exact_immutable_160_bridge():
    module = load_module()
    fixture = json.loads(
        (ROOT / "scripts/release/legacy-app-upgrade-baselines.json").read_text()
    )
    module.validate_baselines(fixture, target_version="1.6.2")
    assert fixture["qualification_modes"] == ["direct", "chained"]
    assert fixture["bridge_release"] == {
        "version": "1.6.0",
        "source_commit": "b48057184752c912e4ae9b3a474374704d896073",
        "catalog_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.0/channelwatch-catalog-v1.6.0.json",
        "catalog_sha256": "bc9bcdb38c44af581835fdd4f29e2a2550eb8bb5d51b8308395a803110feb185",
        "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.0/channelwatch-app-v1.6.0.zip",
        "bundle_sha256": "f8cbdca71ef254df84b0e3b472facce8ece201b4928218caf757f4c5ddf53914",
    }


def test_release_config_requires_exact_legacy_source_contract():
    module = load_module()
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text()
    )
    assert module.validate_release_config(config, target_version="1.6.2") == [
        "1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1"
    ]
    config["legacy_app_update_sources"].pop()
    with pytest.raises(module.LegacyAppUpgradeError):
        module.validate_release_config(config, target_version="1.6.2")

    historical = {
        "version": "1.6.0",
        "legacy_app_update_sources": ["1.4.0", "1.4.1", "1.4.5"],
    }
    assert module.validate_release_config(
        historical, target_version="1.6.0"
    ) == ["1.4.0", "1.4.1", "1.4.5"]


def test_post_activation_check_requires_a_fresh_v2_feed_request(monkeypatch):
    module = load_module()
    logs = iter([
        '"GET /updates/v2/stable.json HTTP/1.1" 200 -\n',
        '"GET /updates/v2/stable.json HTTP/1.1" 200 -\n'
        '"GET /updates/v2/stable.json HTTP/1.1" 200 -\n',
    ])

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    monkeypatch.setattr(
        module.image_tool,
        "run",
        lambda *_args, **_kwargs: Result(next(logs)),
    )

    class Client:
        @staticmethod
        def request(path, payload):
            assert path == "/api/v1/update/check"
            assert payload == {}
            return {
                "current_version": "1.6.0",
                "update_available": False,
                "last_job": {
                    "operation": "check",
                    "status": "current",
                    "version": "1.6.0",
                },
            }

    assert module.checked_current_on_v2(
        Client(), "feed", target_version="1.6.0"
    )["current_version"] == "1.6.0"


def test_post_activation_check_rejects_status_without_v2_network_fetch(monkeypatch):
    module = load_module()

    class Result:
        returncode = 0
        stderr = ""
        stdout = '"GET /updates/v2/stable.json HTTP/1.1" 200 -\n'

    monkeypatch.setattr(module.image_tool, "run", lambda *_args, **_kwargs: Result())

    class Client:
        @staticmethod
        def request(_path, _payload):
            return {
                "current_version": "1.6.0",
                "update_available": False,
                "last_job": {
                    "operation": "check",
                    "status": "current",
                    "version": "1.6.0",
                },
            }

    with pytest.raises(module.LegacyAppUpgradeError):
        module.checked_current_on_v2(Client(), "feed", target_version="1.6.0")


def test_reconciled_attempt_waits_for_exact_terminal_scheduler_state(monkeypatch):
    module = load_module()
    pending = {
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
        "job_id": "b" * 32,
        "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
        "phase": "activation_pending",
    }
    success = {
        **pending,
        "phase": "success",
        "completed_at": "2026-10-16T17:00:00Z",
        "terminal_job_status": "success",
    }
    states = iter([{"last_attempt": pending}, {"last_attempt": success}])
    observed = []
    observation = {}

    monkeypatch.setattr(
        module.image_tool,
        "_docker_json",
        lambda container, code: observed.append((container, code)) or next(states),
    )
    monkeypatch.setattr(
        module.image_tool,
        "wait_until",
        lambda callback, _timeout, _description: callback() or callback(),
    )

    assert module.wait_reconciled_attempt(
        "legacy-app",
        version="1.6.0",
        bundle_sha256="a" * 64,
        job_id="b" * 32,
        attempt_id="manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
        timeout=60,
        observation=observation,
    ) == success
    assert len(observed) == 2
    assert all(container == "legacy-app" for container, _code in observed)
    assert all("update-scheduler.json" in code for _container, code in observed)
    assert observation == {"state": "present", **success}


def test_reconciled_attempt_records_bounded_missing_observation(monkeypatch):
    module = load_module()
    observation = {"stale": "value"}
    monkeypatch.setattr(
        module.image_tool,
        "_docker_json",
        lambda *_args, **_kwargs: {
            "last_attempt": None,
            "private_scheduler_state": "must-not-be-retained",
        },
    )

    assert module.reconciled_attempt(
        "legacy-app",
        version="1.6.0",
        bundle_sha256="a" * 64,
        job_id="b" * 32,
        attempt_id="manual-apply@expected",
        observation=observation,
    ) is None
    assert observation == {"state": "missing"}


def test_post_activation_check_waits_for_exact_apply_reconciliation(monkeypatch):
    module = load_module()
    attempt_id = "manual-apply@2026-10-17T00:22:02Z#bbbbbbbb"
    applied = {
        "job_id": "b" * 32,
        "operation": "apply",
        "status": "success",
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
        "scheduler_attempt_id": attempt_id,
    }
    reconciled = {
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
        "job_id": "b" * 32,
        "attempt_id": attempt_id,
        "phase": "success",
        "completed_at": "2026-10-17T00:22:08Z",
        "terminal_job_status": "success",
    }
    checked = {
        "current_version": "1.6.0",
        "update_available": False,
        "last_job": {
            "job_id": "c" * 32,
            "operation": "check",
            "status": "current",
            "version": "1.6.0",
        },
    }
    observation = {}
    events = []

    def wait_reconciled(container, **kwargs):
        events.append("reconciled")
        assert container == "legacy-app"
        assert kwargs == {
            "version": "1.6.0",
            "bundle_sha256": "a" * 64,
            "job_id": "b" * 32,
            "attempt_id": attempt_id,
            "timeout": 60,
            "observation": observation,
        }
        return reconciled

    def check_current(client, feed, *, target_version):
        events.append("checked")
        assert events == ["reconciled", "checked"]
        assert client == "client"
        assert feed == "feed"
        assert target_version == "1.6.0"
        return checked

    monkeypatch.setattr(module, "wait_reconciled_attempt", wait_reconciled)
    monkeypatch.setattr(module, "checked_current_on_v2", check_current)

    assert module.checked_current_after_reconciliation(
        "client",
        "feed",
        container="legacy-app",
        applied_job=applied,
        target_version="1.6.0",
        bundle_sha256="a" * 64,
        timeout=60,
        observation=observation,
    ) == (reconciled, checked)
    assert events == ["reconciled", "checked"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda job: job.update(job_id="foreign"),
        lambda job: job.update(operation="check"),
        lambda job: job.update(status="restarting"),
        lambda job: job.update(version="1.5.3"),
        lambda job: job.update(bundle_sha256="c" * 64),
        lambda job: job.update(scheduler_attempt_id=""),
    ],
)
def test_post_activation_reconciliation_rejects_foreign_apply_identity(
    monkeypatch, mutation
):
    module = load_module()
    applied = {
        "job_id": "b" * 32,
        "operation": "apply",
        "status": "success",
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
        "scheduler_attempt_id": "manual-apply@expected",
    }
    mutation(applied)
    monkeypatch.setattr(
        module,
        "wait_reconciled_attempt",
        lambda *_args, **_kwargs: pytest.fail("foreign apply reached reconciliation"),
    )
    monkeypatch.setattr(
        module,
        "checked_current_on_v2",
        lambda *_args, **_kwargs: pytest.fail("foreign apply reached check"),
    )

    with pytest.raises(module.LegacyAppUpgradeError):
        module.checked_current_after_reconciliation(
            "client",
            "feed",
            container="legacy-app",
            applied_job=applied,
            target_version="1.6.0",
            bundle_sha256="a" * 64,
            timeout=60,
        )


@pytest.mark.parametrize(
    "attempt",
    [
        {
            "version": "1.6.0",
            "bundle_sha256": "c" * 64,
            "job_id": "b" * 32,
            "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
            "phase": "success",
            "completed_at": "2026-10-16T17:00:00Z",
            "terminal_job_status": "success",
        },
        {
            "version": "1.5.3",
            "bundle_sha256": "a" * 64,
            "job_id": "b" * 32,
            "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
            "phase": "success",
            "completed_at": "2026-10-16T17:00:00Z",
            "terminal_job_status": "success",
        },
        {
            "version": "1.6.0",
            "bundle_sha256": "a" * 64,
            "job_id": "c" * 32,
            "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
            "phase": "success",
            "completed_at": "2026-10-16T17:00:00Z",
            "terminal_job_status": "success",
        },
        {
            "version": "1.6.0",
            "bundle_sha256": "a" * 64,
            "job_id": "b" * 32,
            "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
            "phase": "failed",
            "completed_at": "2026-10-16T17:00:00Z",
            "terminal_job_status": "failed",
        },
        {
            "version": "1.6.0",
            "bundle_sha256": "a" * 64,
            "job_id": "b" * 32,
            "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
            "phase": "success",
            "completed_at": "",
            "terminal_job_status": "success",
        },
    ],
)
def test_reconciled_attempt_rejects_substitution_or_failure(monkeypatch, attempt):
    module = load_module()
    monkeypatch.setattr(
        module.image_tool,
        "_docker_json",
        lambda *_args, **_kwargs: {"last_attempt": attempt},
    )

    with pytest.raises(module.LegacyAppUpgradeError):
        module.wait_reconciled_attempt(
            "legacy-app",
            version="1.6.0",
            bundle_sha256="a" * 64,
            job_id="b" * 32,
            attempt_id="manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
            timeout=60,
        )


def test_restart_waits_for_exact_reconciliation_before_restarting(monkeypatch):
    module = load_module()
    attempt = {
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
        "job_id": "b" * 32,
        "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
        "phase": "success",
        "completed_at": "2026-10-16T17:00:02Z",
        "terminal_job_status": "success",
    }
    observed = []

    monkeypatch.setattr(
        module,
        "wait_reconciled_attempt",
        lambda *_args, **_kwargs: observed.append("reconciled") or attempt,
    )
    monkeypatch.setattr(
        module.image_tool,
        "run",
        lambda command, **kwargs: observed.append((command, kwargs)),
    )

    assert module.restart_after_reconciliation(
        "legacy-app",
        version="1.6.0",
        bundle_sha256="a" * 64,
        job_id="b" * 32,
        attempt_id=attempt["attempt_id"],
        timeout=60,
    ) == attempt
    assert observed == [
        "reconciled",
        (["docker", "restart", "legacy-app"], {"timeout": 120}),
    ]


@pytest.mark.parametrize("persisted", [None, {"phase": "success"}])
def test_restart_requires_identical_reconciliation_to_persist(monkeypatch, persisted):
    module = load_module()
    expected = {
        "version": "1.6.0",
        "bundle_sha256": "a" * 64,
        "job_id": "b" * 32,
        "attempt_id": "manual-apply@2026-10-16T17:00:00Z#bbbbbbbb",
        "phase": "success",
        "completed_at": "2026-10-16T17:00:02Z",
        "terminal_job_status": "success",
    }
    monkeypatch.setattr(
        module,
        "reconciled_attempt",
        lambda *_args, **_kwargs: persisted,
    )

    with pytest.raises(module.LegacyAppUpgradeError, match="did not persist"):
        module.require_persisted_reconciliation(
            "legacy-app",
            version="1.6.0",
            bundle_sha256="a" * 64,
            job_id="b" * 32,
            attempt_id=expected["attempt_id"],
            expected=expected,
        )


def test_reconciled_attempt_rejects_attempt_identity_substitution(monkeypatch):
    module = load_module()
    monkeypatch.setattr(
        module.image_tool,
        "_docker_json",
        lambda *_args, **_kwargs: {
            "last_attempt": {
                "version": "1.6.0",
                "bundle_sha256": "a" * 64,
                "job_id": "b" * 32,
                "attempt_id": "manual-apply@foreign",
                "phase": "success",
                "completed_at": "2026-10-16T17:00:02Z",
                "terminal_job_status": "success",
            }
        },
    )

    with pytest.raises(module.LegacyAppUpgradeError, match="different"):
        module.reconciled_attempt(
            "legacy-app",
            version="1.6.0",
            bundle_sha256="a" * 64,
            job_id="b" * 32,
            attempt_id="manual-apply@expected",
        )


def test_selected_status_evidence_is_bounded_to_validated_runtime_identity():
    module = load_module()
    status = selected_status()
    status.update(
        latest={"highlights": ["large public catalog"]},
        private_value="must-not-be-retained",
    )
    status["active_bundle"]["private_value"] = "must-not-be-retained"

    evidence = module.selected_status_evidence(status)

    assert set(evidence) == {
        "current_version",
        "image_version",
        "launcher_protocol",
        "runtime_source",
        "operation_busy",
        "active_bundle",
        "worker_runtime",
    }
    assert set(evidence["active_bundle"]) == {
        "version",
        "path",
        "activation_id",
        "activation_protocol",
        "bundle_sha256",
        "git_sha",
    }
    assert "latest" not in evidence
    assert "private_value" not in evidence["active_bundle"]


def test_current_check_evidence_keeps_only_reconciliation_diagnostics():
    module = load_module()
    checked = {
        "current_version": "1.6.0",
        "image_version": "1.4.0",
        "launcher_protocol": 3,
        "runtime_source": "app_bundle",
        "catalog_state": "current",
        "catalog_checked_at": "2026-10-16T17:00:03Z",
        "update_available": False,
        "image_required": False,
        "private_value": "must-not-be-retained",
        "last_job": {
            "job_id": "c" * 32,
            "operation": "check",
            "status": "current",
            "version": "1.6.0",
            "updated_at": "2026-10-16T17:00:03Z",
            "private_value": "must-not-be-retained",
        },
    }

    evidence = module.current_check_evidence(checked)

    assert "private_value" not in evidence
    assert evidence["last_job"] == {
        "job_id": "c" * 32,
        "operation": "check",
        "status": "current",
        "version": "1.6.0",
        "bundle_sha256": None,
        "scheduler_attempt_id": None,
        "created_at": None,
        "updated_at": "2026-10-16T17:00:03Z",
        "completed_at": None,
    }


def test_retained_diagnostics_bound_oversized_and_nested_values(monkeypatch):
    module = load_module()
    oversized = "x" * 10_000
    status = selected_status()
    status["active_bundle"].update(
        path="/config/channelwatch-runtime/releases/" + oversized,
        activation_id={"nested": oversized},
    )
    status["worker_runtime"]["dependencies"]["cryptography"].update(
        path="/venv/lib/python3.14/site-packages/" + oversized,
        version=oversized,
    )
    module.validate_selected_status(
        status,
        target_version="1.6.0",
        image_version="1.4.0",
        bundle_sha256="a" * 64,
    )

    checked = {
        "current_version": oversized,
        "image_version": "1.4.0",
        "launcher_protocol": 3,
        "runtime_source": "app_bundle",
        "catalog_state": {"nested": oversized},
        "catalog_checked_at": oversized,
        "update_available": False,
        "image_required": False,
        "last_job": {
            "job_id": oversized,
            "operation": "check",
            "status": "current",
            "version": {"nested": oversized},
            "private_value": oversized,
        },
    }
    apply_job = module.update_job_evidence(
        {
            "job_id": oversized,
            "operation": "apply",
            "status": "success",
            "version": "1.6.0",
            "detail": {"secret": oversized},
        }
    )
    rollback_response = module.rollback_response_evidence(
        {
            "job_id": oversized,
            "operation": "rollback",
            "status": "success",
            "version": "1.6.0",
            "rolled_back_from": "1.6.0",
            "restored_version": {"nested": oversized},
            "rollback_applied": True,
            "restart_required": False,
            "validated_at": oversized,
            "startup_components": {
                "core": {
                    "healthy": True,
                    "version": oversized,
                    "ready_at": oversized,
                    "private": oversized,
                },
                "foreign": {"secret": oversized},
            },
            "private_value": oversized,
        }
    )
    observation = {}
    monkeypatch.setattr(
        module.image_tool,
        "_docker_json",
        lambda *_args: {
            "last_attempt": {
                "version": {"nested": oversized},
                "bundle_sha256": oversized,
                "job_id": oversized,
                "attempt_id": oversized,
                "phase": oversized,
                "completed_at": oversized,
                "terminal_job_status": oversized,
            }
        },
    )
    with pytest.raises(module.LegacyAppUpgradeError):
        module.reconciled_attempt(
            "legacy-app",
            version="1.6.0",
            bundle_sha256="a" * 64,
            job_id="b" * 32,
            attempt_id="manual-apply@expected",
            observation=observation,
        )

    retained = {
        "apply_job": apply_job,
        "selected": module.selected_status_evidence(status),
        "checked": module.current_check_evidence(checked),
        "rollback_response": rollback_response,
        "scheduler": observation,
    }

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from strings(nested)

    assert max(map(len, strings(retained))) <= module.DIAGNOSTIC_TEXT_LIMIT
    assert len(json.dumps(retained)) < 12_000
    assert "private_value" not in apply_job
    assert "detail" not in apply_job
    assert retained["checked"]["catalog_state"] == "<invalid>"
    assert retained["checked"]["last_job"]["version"] == "<invalid>"
    assert retained["selected"]["active_bundle"]["activation_id"] == "<invalid>"
    assert rollback_response["restored_version"] == "<invalid>"
    assert rollback_response["startup_components"]["unexpected_components"] is True
    assert "private_value" not in rollback_response
    assert "private" not in rollback_response["startup_components"]["core"]
    assert observation["version"] == "<invalid>"
    assert len(status["active_bundle"]["path"]) > module.DIAGNOSTIC_TEXT_LIMIT


def test_rollback_response_is_checkpointed_before_strict_validation():
    source = SCRIPT.read_text(encoding="utf-8")
    checkpoint = source.index('receipt["rollback_response_checkpoint"] =')
    validation = source.index(
        "rollback = capability_tool.validate_rollback_response(", checkpoint
    )

    assert checkpoint < validation


def test_update_operation_preserves_bounded_sanitized_http_error_context():
    module = load_module()
    calls = []

    class Client:
        @staticmethod
        def request(path, payload):
            calls.append((path, payload))
            body = json.dumps(
                {
                    "detail": {
                        "code": "ERR_UPDATE_LOCKED",
                        "message": "A prior update activation is pending; token=secret-value ",
                    }
                }
            ).encode()
            raise urllib.error.HTTPError(
                "http://local/api/v1/update/retry",
                409,
                "Conflict",
                {},
                io.BytesIO(body),
            )

    with pytest.raises(module.LegacyAppUpgradeError) as raised:
        module.request_update_operation(
            Client(),
            "/api/v1/update/retry",
            {"version": "1.6.0", "job_id": "b" * 32},
        )

    summary = str(raised.value)
    assert "POST /api/v1/update/retry returned HTTP 409" in summary
    assert "ERR_UPDATE_LOCKED" in summary
    assert "A prior update activation is pending" in summary
    assert "secret-value" not in summary
    assert "token=[redacted]" in summary
    assert calls == [
        (
            "/api/v1/update/retry",
            {"version": "1.6.0", "job_id": "b" * 32},
        )
    ]


def test_update_operation_does_not_echo_unstructured_or_oversized_error_body():
    module = load_module()

    class Client:
        @staticmethod
        def request(_path, _payload):
            raise urllib.error.HTTPError(
                "http://local/api/v1/update/apply",
                409,
                "Conflict",
                {},
                io.BytesIO(b"private=" + b"x" * 10000),
            )

    with pytest.raises(module.LegacyAppUpgradeError) as raised:
        module.request_update_operation(
            Client(), "/api/v1/update/apply", {"version": "1.6.0"}
        )

    assert str(raised.value) == "Update API POST /api/v1/update/apply returned HTTP 409."


@pytest.mark.parametrize(
    ("stored_evidence", "masked_api"),
    [
        pytest.param(False, True, id="unencrypted-or-undecryptable-storage"),
        pytest.param(True, False, id="broken-masked-credential-api"),
    ],
)
def test_legacy_encryption_check_rejects_incomplete_credential_evidence(
    monkeypatch, stored_evidence, masked_api
):
    module = load_module()
    monkeypatch.setattr(
        module.image_tool,
        "encrypted_settings_usable",
        lambda container: container == "app" and stored_evidence,
    )
    monkeypatch.setattr(
        module.image_tool,
        "credential_api_semantics",
        lambda client: client == "client" and masked_api,
    )

    with pytest.raises(module.LegacyAppUpgradeError, match="credential evidence"):
        module.require_legacy_encrypted_settings("app", "client")


def test_legacy_encryption_check_accepts_exact_storage_and_masked_api_evidence(
    monkeypatch,
):
    module = load_module()
    monkeypatch.setattr(
        module.image_tool, "encrypted_settings_usable", lambda container: container == "app"
    )
    monkeypatch.setattr(
        module.image_tool,
        "credential_api_semantics",
        lambda client: client == "client",
    )

    module.require_legacy_encrypted_settings("app", "client")


@pytest.mark.parametrize("image_version", ["1.4.0", "1.4.1", "1.4.5"])
def test_legacy_inventory_keeps_full_venv_with_legacy_bootstrap_paths(
    monkeypatch, image_version
):
    module = load_module()
    observed = []

    def fake_inventory(container, *, bootstrap_paths=None):
        observed.append((container, bootstrap_paths))
        return {"sha256": "a" * 64, "entries": [{"path": "/venv"}]}

    monkeypatch.setattr(module.capability_tool, "image_inventory", fake_inventory)

    assert module.legacy_image_inventory("app", image_version=image_version) == {
        "sha256": "a" * 64,
        "entries": [{"path": "/venv"}],
    }
    assert observed == [("app", module.LEGACY_IMAGE_BOOTSTRAP_PATHS)]


def test_legacy_inventory_rejects_unqualified_source_version():
    module = load_module()

    with pytest.raises(module.LegacyAppUpgradeError, match="unsupported"):
        module.legacy_image_inventory("app", image_version="1.3.0")


def test_selected_runtime_info_invokes_selected_doctor_with_image_python(monkeypatch):
    module = load_module()
    application_dir = "/config/channelwatch-runtime/releases/v1.6.0"
    process_runtime = image_process_runtime()
    observed = []

    class Result:
        stdout = json.dumps(
            {
                "diagnostic_source": "selected_application",
                "application_dir": application_dir,
                "application_version": "1.6.0",
                "process_runtime": process_runtime,
                "verified_generation": None,
            }
        )

    def fake_run(command, **kwargs):
        observed.append((command, kwargs))
        return Result()

    monkeypatch.setattr(module.image_tool, "run", fake_run)

    assert module.selected_runtime_info(
        "legacy-app", version="1.6.0", application_dir=application_dir
    )["process_runtime"] == process_runtime
    command, kwargs = observed[0]
    assert command[:5] == [
        "docker",
        "exec",
        "--env",
        f"CHANNELWATCH_APP_DIR={application_dir}",
        "legacy-app",
    ]
    assert command[5:9] == ["/venv/bin/python", "-I", "-B", "-c"]
    assert command[-2:] == [application_dir, "runtime-info"]
    assert "/usr/local/bin/channelwatch" not in command
    assert kwargs == {"timeout": 60}


def test_selected_runtime_info_rejects_mismatched_selected_application(monkeypatch):
    module = load_module()
    application_dir = "/config/channelwatch-runtime/releases/v1.6.0"

    class Result:
        stdout = json.dumps(
            {
                "diagnostic_source": "selected_application",
                "application_dir": "/config/channelwatch-runtime/releases/substitution",
                "application_version": "1.6.0",
                "process_runtime": image_process_runtime(),
                "verified_generation": None,
            }
        )

    monkeypatch.setattr(module.image_tool, "run", lambda *_args, **_kwargs: Result())

    with pytest.raises(module.LegacyAppUpgradeError, match="image Python closure"):
        module.selected_runtime_info(
            "legacy-app", version="1.6.0", application_dir=application_dir
        )


def test_capture_logs_keeps_partial_evidence_after_one_container_fails(
    monkeypatch, tmp_path: Path
):
    module = load_module()
    seen = []

    class Result:
        returncode = 0
        stdout = "feed output\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        label = command[-1]
        seen.append(label)
        if label == "app-container":
            raise TimeoutError("docker logs timed out")
        return Result()

    monkeypatch.setattr(module.image_tool, "run", fake_run)

    hashes, empty_logs, failures = module._capture_logs(
        {"app": "app-container", "feed": "feed-container"}, tmp_path
    )

    assert seen == ["app-container", "feed-container"]
    assert set(hashes) == {"app", "feed"}
    assert empty_logs == []
    assert failures == [
        {
            "container": "app",
            "failure_type": "TimeoutError",
            "failure_summary": "docker logs timed out",
        }
    ]
    assert "TimeoutError: docker logs timed out" in (tmp_path / "app.log").read_text(
        encoding="utf-8"
    )
    assert "feed output" in (tmp_path / "feed.log").read_text(encoding="utf-8")


def test_finalize_preserves_primary_when_logs_and_cleanup_also_fail(
    monkeypatch, tmp_path: Path
):
    module = load_module()
    receipt = {
        "status": "failed",
        "failure_type": "RuntimeError",
        "failure_summary": "primary startup failure",
        "checks": {"cleanup": False},
    }
    monkeypatch.setattr(
        module,
        "_capture_logs",
        lambda *_args, **_kwargs: (
            {"app": "a" * 64, "feed": "b" * 64},
            ["feed"],
            [
                {
                    "container": "app",
                    "returncode": 1,
                    "failure_summary": "permission denied",
                }
            ],
        ),
    )

    class Resources:
        @staticmethod
        def cleanup():
            return ["container:app:inspect"]

    module._finalize_receipt(
        receipt,
        containers={"app": "app", "feed": "feed"},
        log_directory=tmp_path / "logs",
        resources=Resources(),
        root=tmp_path / "root",
    )

    assert receipt["failure_type"] == "RuntimeError"
    assert receipt["failure_summary"] == "primary startup failure"
    assert receipt["logs_sha256"] == {"app": "a" * 64, "feed": "b" * 64}
    assert receipt["log_capture"] == {
        "status": "failed",
        "empty_containers": ["feed"],
        "command_failures": [
            {
                "container": "app",
                "returncode": 1,
                "failure_summary": "permission denied",
            }
        ],
        "failure_type": "LegacyAppUpgradeError",
        "failure_summary": "Container log evidence failed: 1 command failure; 1 empty log.",
    }
    assert receipt["cleanup"] == {
        "status": "failed",
        "failures": ["container:app:inspect"],
    }
    assert receipt["checks"]["cleanup"] is False


def test_finalize_fails_passed_receipt_when_required_log_is_empty(
    monkeypatch, tmp_path: Path
):
    module = load_module()
    receipt = {"status": "passed", "checks": {"cleanup": False}}
    monkeypatch.setattr(
        module,
        "_capture_logs",
        lambda *_args, **_kwargs: (
            {"app": "a" * 64, "feed": "b" * 64},
            ["feed"],
            [],
        ),
    )

    class Resources:
        @staticmethod
        def cleanup():
            return []

    module._finalize_receipt(
        receipt,
        containers={"app": "app", "feed": "feed"},
        log_directory=tmp_path / "logs",
        resources=Resources(),
        root=tmp_path / "root",
    )

    assert receipt["status"] == "failed"
    assert receipt["failure_type"] == "LegacyAppUpgradeError"
    assert receipt["failure_summary"] == "Container log evidence failed: 1 empty log."
    assert receipt["cleanup"] == {"status": "passed", "failures": []}
    assert receipt["checks"]["cleanup"] is True


def test_finalize_records_cleanup_exception_without_losing_logs(
    monkeypatch, tmp_path: Path
):
    module = load_module()
    receipt = {"status": "passed", "checks": {"cleanup": False}}
    monkeypatch.setattr(
        module,
        "_capture_logs",
        lambda *_args, **_kwargs: (
            {"app": "a" * 64, "feed": "b" * 64},
            [],
            [],
        ),
    )

    class Resources:
        @staticmethod
        def cleanup():
            raise RuntimeError("docker cleanup unavailable")

    module._finalize_receipt(
        receipt,
        containers={"app": "app", "feed": "feed"},
        log_directory=tmp_path / "logs",
        resources=Resources(),
        root=tmp_path / "root",
    )

    assert receipt["status"] == "failed"
    assert receipt["failure_type"] == "RuntimeError"
    assert receipt["failure_summary"] == "docker cleanup unavailable"
    assert receipt["logs_sha256"] == {"app": "a" * 64, "feed": "b" * 64}
    assert receipt["log_capture"] == {
        "status": "passed",
        "empty_containers": [],
        "command_failures": [],
    }
    assert receipt["cleanup"] == {
        "status": "failed",
        "failures": [],
        "failure_type": "RuntimeError",
        "failure_summary": "docker cleanup unavailable",
    }
    assert receipt["checks"]["cleanup"] is False


def test_finalize_keeps_passed_receipt_when_logs_and_cleanup_pass(
    monkeypatch, tmp_path: Path
):
    module = load_module()
    receipt = {"status": "passed", "checks": {"cleanup": False}}
    monkeypatch.setattr(
        module,
        "_capture_logs",
        lambda *_args, **_kwargs: (
            {"app": "a" * 64, "feed": "b" * 64},
            [],
            [],
        ),
    )

    class Resources:
        @staticmethod
        def cleanup():
            return []

    module._finalize_receipt(
        receipt,
        containers={"app": "app", "feed": "feed"},
        log_directory=tmp_path / "logs",
        resources=Resources(),
        root=tmp_path / "root",
    )

    assert receipt == {
        "status": "passed",
        "checks": {"cleanup": True},
        "logs_sha256": {"app": "a" * 64, "feed": "b" * 64},
        "log_capture": {
            "status": "passed",
            "empty_containers": [],
            "command_failures": [],
        },
        "cleanup": {"status": "passed", "failures": []},
    }
