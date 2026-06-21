import json
import os
import shutil
import stat
import subprocess
import importlib.util
from pathlib import Path

import pytest

_REPO_DIR = Path(__file__).resolve().parents[3]
_APP_DIR = _REPO_DIR / "app"
_ENTRYPOINT = _APP_DIR / "core" / "docker-entrypoint.py"
_HELM_DEPLOYMENT = (
    _REPO_DIR / "deploy" / "helm" / "channelwatch" / "templates" / "deployment.yaml"
)


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location("channelwatch_entrypoint", _ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _point_entrypoint_at_config(module, config_dir: Path) -> None:
    module.CONFIG_DIR = config_dir
    module.SETTINGS_FILE = config_dir / "settings.json"


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

    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o640
    assert stat.S_IMODE(public_state_file.stat().st_mode) == 0o640
    assert stat.S_IMODE(encryption_key.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o750


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


def test_helm_api_key_bootstrap_is_handled_by_entrypoint_not_partial_init_container():
    deployment = _HELM_DEPLOYMENT.read_text(encoding="utf-8")
    entrypoint = _ENTRYPOINT.read_text(encoding="utf-8")

    assert "seed-api-key" not in deployment
    assert "initContainers:" not in deployment
    assert '"CW_API_KEY": ("api_key", str)' in entrypoint
    assert "secretRef:" in deployment


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
        ],
        capture_output=True,
        text=True,
        timeout=20,
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
