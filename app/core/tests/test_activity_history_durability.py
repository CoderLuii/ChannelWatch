import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


def test_activity_recorder_quarantines_malformed_history_before_rewrite(tmp_path):
    from core.helpers import activity_recorder as recorder

    history_file = tmp_path / "activity_history.json"
    history_file.write_text('{"partial":', encoding="utf-8")

    with patch.object(recorder, "HISTORY_FILE", str(history_file)):
        assert recorder.record_activity(
            activity_type="watching_channel",
            title="Watching TV",
            message="Watching Channel 5",
            channel_name="Channel 5",
            notification_history={},
        )

    quarantined = list(tmp_path.glob("activity_history.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == '{"partial":'
    rewritten = json.loads(history_file.read_text(encoding="utf-8"))
    assert rewritten[0]["title"] == "Watching TV"


def test_ui_loader_quarantines_malformed_history_without_erasing_memory(tmp_path):
    from ui.backend import main

    history_file = tmp_path / "activity_history.json"
    history_file.write_text("[{bad json", encoding="utf-8")
    existing = main.AlertHistoryItem(
        id="existing",
        type="watching_channel",
        title="Existing memory item",
        message="keep me",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    with (
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main.ACTIVITY_HISTORY", [existing]),
    ):
        assert main.load_alert_history() is False
        assert main.ACTIVITY_HISTORY == [existing]

    quarantined = list(tmp_path.glob("activity_history.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "[{bad json"
    assert not history_file.exists()


@pytest.mark.asyncio
async def test_clear_activity_history_offloads_legacy_file_truncation(tmp_path):
    from ui.backend import main

    history_file = tmp_path / "activity_history.json"
    history_file.write_text('[{"title":"old"}]', encoding="utf-8")
    calls = []

    async def run_inline(func, *args, **kwargs):
        calls.append(getattr(func, "__name__", repr(func)))
        return func(*args, **kwargs)

    with (
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main._get_activity_db_engine", return_value=None),
        patch(
            "ui.backend.main.asyncio.to_thread", new=AsyncMock(side_effect=run_inline)
        ),
    ):
        result = await main.clear_activity_history()

    assert "cleared" in result["message"].lower()
    assert calls == ["_clear_legacy_history_file"]
    assert json.loads(history_file.read_text(encoding="utf-8")) == []
