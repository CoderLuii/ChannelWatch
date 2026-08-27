import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_DIR = Path(__file__).resolve().parents[3]
_APP_DIR = _REPO_DIR / "app"
_ENTRYPOINT = _APP_DIR / "core" / "docker-entrypoint.py"
_HELM_DEPLOYMENT = (
    _REPO_DIR / "deploy" / "helm" / "channelwatch" / "templates" / "deployment.yaml"
)
_DOCKERFILE = _REPO_DIR / "deploy" / "docker" / "Dockerfile"


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location("channelwatch_entrypoint", _ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _point_entrypoint_at_config(module, config_dir: Path) -> None:
    module.CONFIG_DIR = config_dir
    module.SETTINGS_FILE = config_dir / "settings.json"


def test_drop_privileges_aborts_when_supplemental_groups_cannot_be_cleared(
    monkeypatch,
):
    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: True)
    monkeypatch.setattr(
        entrypoint.os,
        "setgroups",
        lambda _groups: (_ for _ in ()).throw(OSError("denied")),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="supplemental groups"):
        entrypoint.drop_privileges(1000, 1000)


@pytest.mark.parametrize("uid,gid", [(0, 1000), (1000, 0), (0, 0)])
def test_drop_privileges_rejects_any_root_identity(uid, gid, monkeypatch):
    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: False)

    with pytest.raises(RuntimeError, match="must both be greater than zero"):
        entrypoint.drop_privileges(uid, gid)


def test_drop_privileges_verifies_effective_ids_and_groups(monkeypatch):
    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: True)
    monkeypatch.setattr(entrypoint.os, "setgroups", lambda _groups: None, raising=False)
    monkeypatch.setattr(entrypoint.os, "setgid", lambda _gid: None, raising=False)
    monkeypatch.setattr(entrypoint.os, "setuid", lambda _uid: None, raising=False)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(entrypoint.os, "getegid", lambda: 1000, raising=False)
    monkeypatch.setattr(entrypoint.os, "getgroups", lambda: [20], raising=False)

    with pytest.raises(RuntimeError, match="effective identity"):
        entrypoint.drop_privileges(1000, 1000)


def test_drop_privileges_root_path_clears_groups_before_ids(monkeypatch):
    entrypoint = _load_entrypoint()
    calls = []
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: True)
    monkeypatch.setattr(
        entrypoint.os, "setgroups", lambda groups: calls.append(("groups", groups))
    )
    monkeypatch.setattr(
        entrypoint.os, "setgid", lambda gid: calls.append(("gid", gid))
    )
    monkeypatch.setattr(
        entrypoint.os, "setuid", lambda uid: calls.append(("uid", uid))
    )
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(entrypoint.os, "getegid", lambda: 1000)
    monkeypatch.setattr(entrypoint.os, "getgroups", lambda: [])

    entrypoint.drop_privileges(1000, 1000)

    assert calls == [("groups", []), ("gid", 1000), ("uid", 1000)]


@pytest.mark.parametrize(
    "effective_uid,effective_gid,supplemental_groups",
    [
        (1001, 1000, []),
        (1000, 0, []),
        (1000, 1000, [20]),
        (1000, 1000, [0]),
        (1000, 1000, [1000, 0]),
        (1000, 1000, [1000, 2000]),
    ],
)
def test_drop_privileges_verifies_identity_when_already_non_root(
    effective_uid, effective_gid, supplemental_groups, monkeypatch
):
    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: False)
    monkeypatch.setattr(
        entrypoint.os, "geteuid", lambda: effective_uid, raising=False
    )
    monkeypatch.setattr(
        entrypoint.os, "getegid", lambda: effective_gid, raising=False
    )
    monkeypatch.setattr(
        entrypoint.os, "getgroups", lambda: supplemental_groups, raising=False
    )

    with pytest.raises(RuntimeError, match="effective identity"):
        entrypoint.drop_privileges(1000, 1000)


@pytest.mark.parametrize(
    "uid,gid,supplemental_groups",
    [
        (1000, 1000, []),
        (1000, 1000, [1000]),
        (1000, 1000, [1000, 1000]),
        (501, 20, [20]),
        (99, 100, [100]),
    ],
)
def test_drop_privileges_accepts_supported_non_root_identity(
    uid, gid, supplemental_groups, monkeypatch
):
    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: False)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: uid, raising=False)
    monkeypatch.setattr(entrypoint.os, "getegid", lambda: gid, raising=False)
    monkeypatch.setattr(
        entrypoint.os, "getgroups", lambda: supplemental_groups, raising=False
    )
    monkeypatch.setattr(
        entrypoint.os,
        "setgroups",
        lambda _groups: pytest.fail("non-root path must not call setgroups"),
        raising=False,
    )
    monkeypatch.setattr(
        entrypoint.os,
        "setgid",
        lambda _gid: pytest.fail("non-root path must not call setgid"),
        raising=False,
    )
    monkeypatch.setattr(
        entrypoint.os,
        "setuid",
        lambda _uid: pytest.fail("non-root path must not call setuid"),
        raising=False,
    )

    entrypoint.drop_privileges(uid, gid)


@pytest.mark.parametrize(
    "environment,expected",
    [
        ({}, (501, 20)),
        ({"PUID": "501", "PGID": "20"}, (501, 20)),
        ({"PUID": "1000", "PGID": "1000"}, (1000, 1000)),
    ],
)
def test_runtime_identity_accepts_defaults_and_non_root_ids(
    environment, expected, monkeypatch
):
    entrypoint = _load_entrypoint()
    monkeypatch.delenv("PUID", raising=False)
    monkeypatch.delenv("PGID", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    uid = entrypoint.parse_id("PUID", entrypoint.DEFAULT_RUNTIME_UID)
    gid = entrypoint.parse_id("PGID", entrypoint.DEFAULT_RUNTIME_GID)

    assert (uid, gid) == expected
    entrypoint.validate_runtime_identity(uid, gid)


@pytest.mark.parametrize(
    "puid,pgid",
    [("0", "1000"), ("1000", "0"), ("0", "0")],
)
def test_main_rejects_root_identity_before_mutating_runtime(
    puid, pgid, monkeypatch
):
    entrypoint = _load_entrypoint()
    monkeypatch.setenv("PUID", puid)
    monkeypatch.setenv("PGID", pgid)
    unexpected_calls = []

    def unexpected(name):
        def fail(*_args, **_kwargs):
            unexpected_calls.append(name)
            raise AssertionError(f"{name} must not run for a root identity")

        return fail

    for name in (
        "ensure_settings",
        "merge_bootstrap_env",
        "chown_tree",
        "chmod_config_tree",
        "render_supervisor_config",
        "prepare_standard_streams",
        "drop_privileges",
        "verify_config_tree_writable",
        "acquire_container_instance_lock",
    ):
        monkeypatch.setattr(entrypoint, name, unexpected(name))
    monkeypatch.setattr(entrypoint.os, "execvp", unexpected("execvp"))

    with pytest.raises(RuntimeError, match="must both be greater than zero"):
        entrypoint.main()

    assert unexpected_calls == []


def test_entrypoint_default_settings_bootstrap_creates_valid_json_atomically(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TZ", raising=False)
    entrypoint = _load_entrypoint()
    _point_entrypoint_at_config(entrypoint, tmp_path)
    settings_file = tmp_path / "settings.json"

    created = entrypoint.ensure_settings(uid=1000, gid=1000)

    assert created is True
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert settings["tz"] == "America/Los_Angeles"
    assert settings["dvr_servers"] == []
    assert settings["api_key"] == ""
    assert settings["_version"] == 7
    assert not (tmp_path / "settings.json.tmp").exists()
    if os.name != "nt":
        assert stat.S_IMODE(settings_file.stat().st_mode) == 0o640


def test_entrypoint_fresh_settings_use_important_only_alert_policy(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TZ", raising=False)
    entrypoint = _load_entrypoint()
    _point_entrypoint_at_config(entrypoint, tmp_path)

    assert entrypoint.ensure_settings(uid=1000, gid=1000) is True

    settings = json.loads(
        (tmp_path / "settings.json").read_text(encoding="utf-8")
    )
    assert settings["notification_preferences_version"] == 1
    assert {
        key: settings[key]
        for key in (
            "alert_channel_watching",
            "alert_vod_watching",
            "rd_alert_scheduled",
            "rd_alert_started",
            "rd_alert_completed",
        )
    } == {
        "alert_channel_watching": False,
        "alert_vod_watching": False,
        "rd_alert_scheduled": False,
        "rd_alert_started": False,
        "rd_alert_completed": False,
    }
    assert all(
        settings[key]
        for key in (
            "alert_disk_space",
            "alert_recording_events",
            "alert_dvr_health",
            "rd_alert_cancelled",
            "rd_alert_failed",
            "rd_alert_skipped",
            "rd_alert_missed",
            "rd_alert_interrupted",
            "dvr_alert_unreachable",
            "dvr_alert_recovered",
        )
    )


def test_entrypoint_default_settings_bootstrap_honors_config_path_without_config_dir(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path))
    monkeypatch.delenv("TZ", raising=False)
    entrypoint = _load_entrypoint()
    settings_file = tmp_path / "settings.json"

    created = entrypoint.ensure_settings(uid=1000, gid=1000)

    assert created is True
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert settings["tz"] == "America/Los_Angeles"
    if os.name != "nt":
        assert stat.S_IMODE(settings_file.stat().st_mode) == 0o640
    assert not (tmp_path / "settings.json.tmp").exists()


def test_entrypoint_default_settings_bootstrap_does_not_overwrite_existing_file(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TZ", raising=False)
    entrypoint = _load_entrypoint()
    _point_entrypoint_at_config(entrypoint, tmp_path)
    settings_file = tmp_path / "settings.json"
    original = {"sentinel": True}
    settings_file.write_text(json.dumps(original), encoding="utf-8")

    created = entrypoint.ensure_settings(uid=1000, gid=1000)

    assert created is False
    assert json.loads(settings_file.read_text(encoding="utf-8")) == original


def test_entrypoint_preserves_historical_alert_settings_without_new_fields(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TZ", raising=False)
    entrypoint = _load_entrypoint()
    _point_entrypoint_at_config(entrypoint, tmp_path)
    settings_file = tmp_path / "settings.json"
    historical = {
        "_version": 7,
        "dvr_servers": [],
        "alert_channel_watching": True,
        "alert_vod_watching": False,
        "alert_disk_space": False,
        "alert_recording_events": True,
        "rd_alert_scheduled": True,
        "rd_alert_started": False,
        "rd_alert_completed": True,
        "rd_alert_cancelled": False,
    }
    original_bytes = json.dumps(historical, sort_keys=True).encode("utf-8")
    settings_file.write_bytes(original_bytes)

    assert entrypoint.ensure_settings(uid=1000, gid=1000) is False

    assert settings_file.read_bytes() == original_bytes
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted == historical
    assert "alert_dvr_health" not in persisted
    assert "rd_alert_failed" not in persisted
    assert "notification_preferences_version" not in persisted


def test_entrypoint_env_merge_uses_atomic_replace_and_seeds_dvr(tmp_path, monkeypatch):
    content = _ENTRYPOINT.read_text(encoding="utf-8")
    assert 'with open(settings_file, "w")' not in content
    assert "os.replace(temp_path, path)" in content

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"dvr_servers": [], "tz": "America/Los_Angeles", "_version": 3}),
        encoding="utf-8",
    )
    entrypoint = _load_entrypoint()
    _point_entrypoint_at_config(entrypoint, tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path))
    monkeypatch.setenv("CW_API_KEY", "seeded-key")
    monkeypatch.setenv("CHANNELS_DVR_SERVERS", "Living Room@192.168.1.10:8089")
    monkeypatch.setenv("TZ", "UTC")

    entrypoint.merge_bootstrap_env(settings_created=True)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert settings["tz"] == "UTC"
    assert settings["api_key"] == "seeded-key"
    assert settings["dvr_servers"][0]["name"] == "Living Room"
    assert settings["dvr_servers"][0]["host"] == "192.168.1.10"
    assert json.loads(
        (tmp_path / "env_overrides.json").read_text(encoding="utf-8")
    ) == [
        "api_key",
        "dvr_servers",
        "tz",
    ]


def test_entrypoint_file_permissions_keep_secret_settings_restricted(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"api_key": "secret"}), encoding="utf-8")
    public_state_file = tmp_path / "env_overrides.json"
    public_state_file.write_text("[]", encoding="utf-8")
    encryption_key = tmp_path / "encryption.key"
    encryption_key.write_text("key", encoding="utf-8")
    entrypoint = _load_entrypoint()

    entrypoint.chmod_config_tree(tmp_path)

    if os.name == "nt":
        assert settings_file.exists()
        assert public_state_file.exists()
        assert encryption_key.exists()
        return

    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(public_state_file.stat().st_mode) == 0o640
    assert stat.S_IMODE(encryption_key.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o750


def test_entrypoint_keeps_credential_backup_and_transaction_trees_private(tmp_path):
    backups = tmp_path / "backups"
    recovery = backups / "key-recovery"
    transactions = tmp_path / ".channelwatch-transactions" / "transaction" / "new"
    recovery.mkdir(parents=True)
    transactions.mkdir(parents=True)
    pre_restore = backups / "pre-restore.20260824T120000Z.deadbeef.zip"
    pre_update = backups / "pre-update.v0.9.18.1.deadbeef.zip"
    recovery_settings = recovery / "reset-20260824T120000Z-deadbeef" / "settings.json"
    recovery_settings.parent.mkdir()
    journal = transactions.parent / "journal.json"
    for path in (pre_restore, pre_update, recovery_settings, journal):
        path.write_bytes(b"credential-bearing")
        path.chmod(0o666)
    for path in (backups, recovery, recovery_settings.parent, transactions):
        path.chmod(0o777)

    entrypoint = _load_entrypoint()
    entrypoint.chmod_config_tree(tmp_path)

    if os.name == "nt":
        return
    for path in (pre_restore, pre_update, recovery_settings, journal):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for path in (
        backups,
        recovery,
        recovery_settings.parent,
        tmp_path / ".channelwatch-transactions",
        transactions.parent,
        transactions,
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_entrypoint_chown_noops_when_started_non_root(tmp_path, monkeypatch):
    entrypoint = _load_entrypoint()
    calls = []

    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(
        entrypoint.os,
        "chown",
        lambda *args: calls.append(args),
        raising=False,
    )

    entrypoint.chown_path(tmp_path, 0, 1000)

    assert calls == []


def test_filesystem_type_uses_deepest_mount_and_decodes_paths(tmp_path):
    entrypoint = _load_entrypoint()
    mountinfo = "\n".join(
        [
            "1 0 0:1 / / rw - overlay overlay rw",
            "2 1 0:2 /shared /tmp/shared\\040config rw - virtiofs virtiofs0 rw",
        ]
    )

    assert (
        entrypoint.filesystem_type_for_path(
            Path("/tmp/shared config/settings.json"),
            mountinfo_text=mountinfo,
        )
        == "virtiofs"
    )
    assert (
        entrypoint.filesystem_type_for_path(
            Path("/tmp/other/settings.json"),
            mountinfo_text=mountinfo,
        )
        == "overlay"
    )


def test_chown_path_defers_virtualized_metadata_to_access_check(
    tmp_path, monkeypatch
):
    entrypoint = _load_entrypoint()
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: True)
    monkeypatch.setattr(entrypoint.os, "chown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        entrypoint, "ownership_metadata_is_virtualized", lambda _path: True
    )
    monkeypatch.setattr(
        entrypoint, "filesystem_type_for_path", lambda _path: "virtiofs"
    )

    assert entrypoint.chown_path(target, 4242, 4343) is True


def test_chown_path_keeps_native_ownership_mismatch_fail_closed(
    tmp_path, monkeypatch
):
    entrypoint = _load_entrypoint()
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: True)
    monkeypatch.setattr(entrypoint.os, "chown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        entrypoint, "ownership_metadata_is_virtualized", lambda _path: False
    )

    assert entrypoint.chown_path(target, 4242, 4343) is False


def test_config_access_check_opens_files_for_write_and_leaves_no_probes(tmp_path):
    entrypoint = _load_entrypoint()
    nested = tmp_path / "channelwatch-runtime"
    nested.mkdir()
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (nested / "active.json").write_text("{}", encoding="utf-8")

    entrypoint.verify_config_tree_writable(tmp_path)

    assert list(tmp_path.rglob(".channelwatch-access-*")) == []


def test_config_access_check_fails_when_existing_file_is_not_writable(
    tmp_path, monkeypatch
):
    entrypoint = _load_entrypoint()
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    original_open = entrypoint.os.open

    def reject_write_open(path, flags, *args, **kwargs):
        if path == "settings.json" and flags & os.O_RDWR:
            raise PermissionError("target identity cannot write settings")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(entrypoint.os, "open", reject_write_open)

    with pytest.raises(RuntimeError, match="changed or became unsafe"):
        entrypoint.verify_config_tree_writable(tmp_path)


def test_helm_api_key_bootstrap_is_handled_by_entrypoint_not_partial_init_container():
    deployment = _HELM_DEPLOYMENT.read_text(encoding="utf-8")
    entrypoint = _ENTRYPOINT.read_text(encoding="utf-8")

    assert "seed-api-key" not in deployment
    assert "initContainers:" not in deployment
    assert '"CW_API_KEY": ("api_key", str)' in entrypoint
    assert "secretRef:" in deployment


def test_docker_entrypoint_runs_with_venv_python():
    content = _DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = _ENTRYPOINT.read_text(encoding="utf-8")

    assert (
        'ENTRYPOINT ["/venv/bin/python", "/app/core/docker-entrypoint.py"]' in content
    )
    assert "chown_tree(CONFIG_DIR, uid, gid)" in entrypoint
    assert "chown_tree(IMAGE_APP_DIR, uid, gid)" not in entrypoint
    assert entrypoint.index("drop_privileges(uid, gid)") < entrypoint.index(
        "verify_config_tree_writable(CONFIG_DIR)"
    ) < entrypoint.index("os.execvp(sys.argv[1], sys.argv[1:])")


def test_helm_defaults_use_non_root_read_only_runtime_mounts():
    deployment = _HELM_DEPLOYMENT.read_text(encoding="utf-8")
    values = (
        _REPO_DIR / "deploy" / "helm" / "channelwatch" / "values.yaml"
    ).read_text(encoding="utf-8")

    assert "runAsNonRoot: true" in values
    assert "runAsUser: 1000" in values
    assert "runAsGroup: 1000" in values
    assert "readOnlyRootFilesystem: true" in values
    assert "allowPrivilegeEscalation: false" in values
    assert "fsGroup: 1000" in values
    assert "mountPath: /tmp" in deployment
    assert "emptyDir: {}" in deployment


def test_helm_rendered_identity_satisfies_predropped_entrypoint_contract(monkeypatch):
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not installed")

    chart_dir = _REPO_DIR / "deploy" / "helm" / "channelwatch"
    result = subprocess.run(
        [
            helm,
            "template",
            "channelwatch",
            str(chart_dir),
            "--set-string",
            "secretConfig.secretStorageKey=0123456789abcdef0123456789abcdef",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    resources = {
        resource["kind"]: resource
        for resource in yaml.safe_load_all(result.stdout)
        if resource
    }
    deployment = resources["Deployment"]
    config_map = resources["ConfigMap"]
    service = resources["Service"]
    pod_spec = deployment["spec"]["template"]["spec"]
    container_security = pod_spec["containers"][0]["securityContext"]
    uid = int(container_security["runAsUser"])
    gid = int(container_security["runAsGroup"])
    fs_group = int(pod_spec["securityContext"]["fsGroup"])

    assert int(config_map["data"]["PUID"]) == uid
    assert int(config_map["data"]["PGID"]) == gid == fs_group
    assert service["spec"]["publishNotReadyAddresses"] is True

    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: False)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: uid, raising=False)
    monkeypatch.setattr(entrypoint.os, "getegid", lambda: gid, raising=False)
    # Some OCI runtimes expose the primary group again when fsGroup equals it.
    monkeypatch.setattr(
        entrypoint.os, "getgroups", lambda: [fs_group, fs_group], raising=False
    )

    entrypoint.drop_privileges(uid, gid)


def test_helm_can_disable_publishing_not_ready_address_when_explicitly_requested():
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not installed")

    result = subprocess.run(
        [
            helm,
            "template",
            "channelwatch",
            str(_REPO_DIR / "deploy" / "helm" / "channelwatch"),
            "--set-string",
            "secretConfig.secretStorageKey=0123456789abcdef0123456789abcdef",
            "--set",
            "service.publishNotReadyAddresses=false",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    resources = {
        resource["kind"]: resource
        for resource in yaml.safe_load_all(result.stdout)
        if resource
    }
    assert resources["Service"]["spec"]["publishNotReadyAddresses"] is False


def test_helm_ingress_template_renders_class_annotation_tls_and_backend():
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not installed")

    chart_dir = _REPO_DIR / "deploy" / "helm" / "channelwatch"
    result = subprocess.run(
        [
            helm,
            "template",
            str(chart_dir),
            "--set",
            "ingress.enabled=true",
            "--set",
            "ingress.className=nginx",
            "--set",
            r"ingress.annotations.nginx\.ingress\.kubernetes\.io/proxy-body-size=64m",
            "--set",
            "ingress.tls[0].secretName=channelwatch-tls",
            "--set",
            "ingress.tls[0].hosts[0]=channelwatch.local",
            "--set-string",
            "secretConfig.secretStorageKey=0123456789abcdef0123456789abcdef",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    assert rendered.count("kind: Ingress") == 1
    assert "apiVersion: networking.k8s.io/v1" in rendered
    assert "ingressClassName: nginx" in rendered
    assert "nginx.ingress.kubernetes.io/proxy-body-size: 64m" in rendered
    assert 'host: "channelwatch.local"' in rendered
    assert "secretName: channelwatch-tls" in rendered
    assert "number: 8501" in rendered
