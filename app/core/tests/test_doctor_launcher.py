import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


_REPO_DIR = Path(__file__).resolve().parents[3]
_APP_DIR = _REPO_DIR / "app"
_WRAPPER = _APP_DIR / "bin" / "channelwatch"


def _write_image_python(tmp_path: Path) -> Path:
    image_python = tmp_path / "image-python"
    image_python.write_text(
        "#!/bin/sh\n"
        f"export LD_LIBRARY_PATH={shlex.quote(str(Path(sys.executable).parents[1] / 'lib'))}\n"
        f"exec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    image_python.chmod(0o755)
    return image_python


def _write_probe_app(app_dir: Path) -> None:
    (app_dir / "core" / "cli").mkdir(parents=True)
    (app_dir / "core" / "__init__.py").write_text("\n", encoding="utf-8")
    (app_dir / "core" / "cli" / "__init__.py").write_text("\n", encoding="utf-8")
    (app_dir / "core" / "cli" / "doctor.py").write_text(
        """
import json
import os
import sys

print(json.dumps({
    "argv": sys.argv,
    "executable": sys.executable,
    "prefix": sys.prefix,
    "pythonhome": os.environ.get("PYTHONHOME"),
    "pythonpath": os.environ.get("PYTHONPATH"),
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "ld_audit": os.environ.get("LD_AUDIT"),
    "ld_library_path": os.environ.get("LD_LIBRARY_PATH"),
    "ld_preload": os.environ.get("LD_PRELOAD"),
    "selected_app": os.environ.get("CHANNELWATCH_APP_DIR"),
    "core_file": __import__("core").__file__,
}))
""".lstrip(),
        encoding="utf-8",
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_probe(image_dir: Path, config_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        CHANNELWATCH_IMAGE_APP_DIR=str(image_dir),
        CHANNELWATCH_IMAGE_PYTHON=str(_write_image_python(config_dir)),
        CHANNELWATCH_APP_DIR=str(config_dir / "selected"),
        CONFIG_PATH=str(config_dir),
        PYTHONPATH=str(config_dir / "selected"),
        PYTHONHOME=str(config_dir / "selected-python"),
        VIRTUAL_ENV=str(config_dir / "selected-python"),
        LD_AUDIT=str(config_dir / "selected-audit.so"),
        LD_LIBRARY_PATH=os.pathsep.join(
            filter(
                None,
                (os.environ.get("LD_LIBRARY_PATH"), str(config_dir / "selected-libs")),
            )
        ),
        LD_PRELOAD=str(config_dir / "selected-preload.so"),
    )
    return subprocess.run(
        [sys.executable, "-I", str(_WRAPPER), "doctor", "runtime-info"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_DIR,
        check=False,
    )


def test_channelwatch_wrapper_dispatches_to_doctor_help(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_APP_DIR)
    env["CONFIG_PATH"] = str(tmp_path)
    env["CHANNELWATCH_IMAGE_APP_DIR"] = str(_APP_DIR)
    env["CHANNELWATCH_IMAGE_PYTHON"] = str(_write_image_python(tmp_path))

    result = subprocess.run(
        [sys.executable, "-I", str(_WRAPPER), "doctor", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_DIR,
        check=False,
    )

    assert result.returncode == 0
    assert "ChannelWatch diagnostics and health checks." in result.stdout
    assert "diagnose" in result.stdout


def test_channelwatch_runtime_info_is_offline_and_does_not_mutate_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"_version": 7, "dvr_servers": []}), encoding="utf-8"
    )
    (config_dir / "encryption.key").write_bytes(b"private-key-material")
    before = _tree_snapshot(config_dir)
    env = os.environ.copy()
    env.update(
        CHANNELWATCH_IMAGE_APP_DIR=str(_APP_DIR),
        CHANNELWATCH_IMAGE_PYTHON=str(_write_image_python(tmp_path)),
        CHANNELWATCH_IMAGE_VERSION="spoofed",
        CONFIG_PATH=str(config_dir),
        HTTP_PROXY="http://127.0.0.1:1",
        HTTPS_PROXY="http://127.0.0.1:1",
    )

    result = subprocess.run(
        [sys.executable, "-I", str(_WRAPPER), "doctor", "runtime-info"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_DIR,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["diagnostic_source"] == "image"
    assert payload["application_dir"] == str(_APP_DIR.resolve())
    assert payload["application_version"] != "spoofed"
    assert payload["verified_generation"] is None
    assert _tree_snapshot(config_dir) == before


def test_channelwatch_wrapper_uses_only_image_code_and_interpreter(tmp_path):
    image_dir = tmp_path / "image"
    config_dir = tmp_path / "config"
    selected_dir = config_dir / "selected"
    marker = tmp_path / "selected-core-imported"
    _write_probe_app(image_dir)
    (selected_dir / "core").mkdir(parents=True)
    (selected_dir / "core" / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    (config_dir / "channelwatch-runtime").mkdir(parents=True)
    (config_dir / "channelwatch-runtime" / "active.json").write_text(
        json.dumps({"path": str(selected_dir), "version": "9.9.9"}),
        encoding="utf-8",
    )

    result = _run_probe(image_dir, config_dir)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["core_file"]).is_relative_to(image_dir)
    assert Path(payload["executable"]).resolve() == Path(sys.executable).resolve()
    assert payload["argv"][1:] == ["runtime-info"]
    for field in (
        "pythonhome",
        "pythonpath",
        "virtual_env",
        "ld_audit",
        "ld_preload",
        "selected_app",
    ):
        assert payload[field] is None
    assert "selected-libs" not in (payload["ld_library_path"] or "")
    assert not marker.exists()


def test_channelwatch_wrapper_identity_is_unchanged_after_rollback(tmp_path):
    image_dir = tmp_path / "image"
    config_dir = tmp_path / "config"
    runtime_dir = config_dir / "channelwatch-runtime"
    runtime_dir.mkdir(parents=True)
    _write_probe_app(image_dir)

    active_path = runtime_dir / "active.json"
    active_path.write_text(
        json.dumps({"path": str(tmp_path / "generation-a"), "version": "1.5.4"}),
        encoding="utf-8",
    )
    before = _run_probe(image_dir, config_dir)
    active_path.write_text(
        json.dumps({"path": str(tmp_path / "generation-b"), "version": "1.5.3"}),
        encoding="utf-8",
    )
    after = _run_probe(image_dir, config_dir)

    assert before.returncode == after.returncode == 0
    assert json.loads(before.stdout) == json.loads(after.stdout)


def test_channelwatch_wrapper_preserves_image_virtual_environment_symlink(tmp_path):
    image_dir = tmp_path / "image"
    config_dir = tmp_path / "config"
    image_venv = tmp_path / "image-venv"
    config_dir.mkdir()
    _write_probe_app(image_dir)
    venv_python = Path("/usr/bin/python3")
    if not venv_python.is_file():
        venv_python = Path(sys.executable)
    subprocess.run(
        [str(venv_python), "-m", "venv", "--without-pip", str(image_venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    image_python = image_venv / "bin" / "python"
    env = os.environ.copy()
    env.update(
        CHANNELWATCH_IMAGE_APP_DIR=str(image_dir),
        CHANNELWATCH_IMAGE_PYTHON=str(image_python),
        CONFIG_PATH=str(config_dir),
    )

    result = subprocess.run(
        [sys.executable, "-I", str(_WRAPPER), "doctor", "runtime-info"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_DIR,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["executable"]) == image_python
    assert Path(payload["prefix"]) == image_venv


def test_channelwatch_wrapper_rejects_missing_image_app(tmp_path):
    env = os.environ.copy()
    env.update(
        CHANNELWATCH_IMAGE_APP_DIR=str(tmp_path / "missing"),
        CHANNELWATCH_IMAGE_PYTHON=str(_write_image_python(tmp_path)),
    )

    result = subprocess.run(
        [sys.executable, "-I", str(_WRAPPER), "doctor", "runtime-info"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_DIR,
        check=False,
    )

    assert result.returncode != 0
    assert "image application directory is unavailable" in result.stderr.lower()


def test_channelwatch_wrapper_rejects_unknown_top_level_command():
    result = subprocess.run(
        [sys.executable, str(_WRAPPER), "unknown"],
        capture_output=True,
        text=True,
        cwd=_REPO_DIR,
        check=False,
    )

    assert result.returncode == 1
    assert "Usage: channelwatch doctor [ARGS...]" in result.stderr


def test_dockerfile_installs_channelwatch_wrapper():
    dockerfile = _REPO_DIR / "deploy" / "docker" / "Dockerfile"

    with open(dockerfile, encoding="utf-8") as handle:
        contents = handle.read()

    assert "COPY app/bin/channelwatch /usr/local/bin/channelwatch" in contents
    assert "os.chmod('/usr/local/bin/channelwatch', 0o755)" in contents
