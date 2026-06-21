import os
import subprocess
import sys
from pathlib import Path


_REPO_DIR = Path(__file__).resolve().parents[3]
_APP_DIR = _REPO_DIR / "app"
_WRAPPER = _APP_DIR / "bin" / "channelwatch"


def test_channelwatch_wrapper_dispatches_to_doctor_help(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_APP_DIR)
    env["CONFIG_PATH"] = str(tmp_path)
    env["CHANNELWATCH_APP_DIR"] = str(_APP_DIR)

    result = subprocess.run(
        [sys.executable, str(_WRAPPER), "doctor", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_DIR,
        check=False,
    )

    assert result.returncode == 0
    assert "ChannelWatch diagnostics and health checks." in result.stdout
    assert "diagnose" in result.stdout


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
