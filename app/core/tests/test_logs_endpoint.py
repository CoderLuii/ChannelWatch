import json
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient


class _LogFileGuard:
    def __init__(self, path):
        self._path = path

    def is_file(self):
        return self._path.is_file()

    def open(self, *args, **kwargs):
        return self._path.open(*args, **kwargs)

    def read_text(self, *args, **kwargs):
        raise AssertionError("full-file read not allowed")


@pytest.fixture()
def client(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"api_key": "test-key", "tz": "UTC"}))
    history_file = tmp_path / "activity_history.json"
    history_file.write_text("[]")

    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main._get_activity_db_engine", return_value=None),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


def test_logs_endpoint_tails_without_reading_entire_file(client, tmp_path):
    log_file = tmp_path / "channelwatch.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(200)) + "\n")

    with patch("ui.backend.main.LOG_FILE", _LogFileGuard(log_file)):
        response = client.get("/api/logs?lines=5")

    assert response.status_code == 200
    assert response.json()["lines"] == [
        "line 195",
        "line 196",
        "line 197",
        "line 198",
        "line 199",
    ]


def test_logs_endpoint_offloads_tail_io_to_thread(client, tmp_path):
    log_file = tmp_path / "channelwatch.log"
    log_file.write_text("line 1\nline 2\n", encoding="utf-8")
    calls = []

    async def fake_to_thread(func, *args):
        calls.append((func, args))
        return func(*args)

    with (
        patch("ui.backend.main.LOG_FILE", log_file),
        patch("ui.backend.main.asyncio.to_thread", side_effect=fake_to_thread),
    ):
        response = client.get("/api/logs?lines=1")

    assert response.status_code == 200
    assert response.json()["lines"] == ["line 2"]
    assert len(calls) == 1
    assert calls[0][0].__name__ == "_tail_log_lines_if_available"


def test_logs_endpoint_clamps_requested_lines(client, tmp_path):
    log_file = tmp_path / "channelwatch.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(1100)) + "\n")

    with patch("ui.backend.main.LOG_FILE", log_file):
        response = client.get("/api/logs?lines=5000")

    assert response.status_code == 200
    lines = response.json()["lines"]
    assert len(lines) == 1000
    assert lines[0] == "line 100"
    assert lines[-1] == "line 1099"


def test_logs_download_returns_404_when_log_file_missing(client, tmp_path):
    with patch("ui.backend.main.LOG_FILE", tmp_path / "missing.log"):
        response = client.get("/api/logs/download")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "ERR_LOG_NOT_FOUND"
    assert detail["message"] == "Log file not found."
    assert "logging is enabled" in detail["remediation"]


def test_logs_download_returns_full_log_attachment(client, tmp_path):
    log_file = tmp_path / "channelwatch.log"
    log_file.write_text("line 1\nline 2\n", encoding="utf-8")

    with patch("ui.backend.main.LOG_FILE", log_file):
        response = client.get("/api/logs/download")

    assert response.status_code == 200
    assert response.text.replace("\r\n", "\n") == "line 1\nline 2\n"
    assert response.headers["content-type"].startswith("text/plain")
    assert 'filename="channelwatch.log"' in response.headers["content-disposition"]
