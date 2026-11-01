import asyncio
import time
from types import SimpleNamespace

import pytest

from core.alerts import vod_watching as vod_module
from core.alerts.vod_watching import VODWatchingAlert
from core.helpers.vod_info import VODInfoProvider

# Shape of a /api/v1/all item as returned by current Channels DVR servers.
LIVE_LOWERCASE_ITEM = {
    "id": "4321",
    "title": "25 Words or Less",
    "episode_title": "Not Everyone Was Playing",
    "summary": "Teams compete.",
    "full_summary": "Teams compete in a word game.",
    "duration": 1939.26,
    "season_number": 8,
    "episode_number": 38,
    "original_air_date": "2026-10-29",
    "path": "/shares/DVR/TV/25 Words or Less/S08E38.mpg",
    "channel": "205",
    "watched": False,
    "favorited": True,
    "image_url": "http://dvr/image.jpg",
}


def _settings(**overrides):
    base = dict(
        vod_title=True,
        vod_episode_title=True,
        vod_summary=True,
        vod_duration=True,
        vod_progress=True,
        vod_image=True,
        vod_rating=True,
        vod_genres=True,
        vod_cast=True,
        vod_device_name=True,
        vod_device_ip=True,
        alert_vod_watching=True,
        vod_alert_cooldown=300,
        vod_significant_threshold=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Provider(VODInfoProvider):
    def __init__(self, settings, item):
        self.settings = settings
        self._item = item

    def get_metadata(self, file_id):
        return dict(self._item)


def _alert(monkeypatch, settings, item=LIVE_LOWERCASE_ITEM):
    alert = VODWatchingAlert.__new__(VODWatchingAlert)
    alert.settings = settings
    alert.dvr = SimpleNamespace(id="dvr1", name="DVR", host="h", port=8089)
    alert.vod_provider = _Provider(settings, item)
    alert.active_sessions = {}
    alert.identifier_ip_cache = {}
    alert._notification_history = {}
    alert.alert_cooldown = settings.vod_alert_cooldown
    alert.significant_threshold = settings.vod_significant_threshold
    alert.template_settings = {"title": "", "body": "", "use_default": True}
    contexts = []

    class _Formatter:
        config = {"title_prefix": ""}

        def build_context(self, alert_type, dvr, extra_context):
            contexts.append(extra_context)
            return extra_context

        def format_templated_alert(self, **kwargs):
            return {"title": "t", "message": "m", "image_url": None}

    alert.alert_formatter = _Formatter()
    sent = []

    async def _send(**kwargs):
        sent.append(kwargs)
        return True

    alert.send_alert_async = _send
    recorded = []
    monkeypatch.setattr(
        vod_module, "record_vod_watching", lambda **kw: recorded.append(kw)
    )
    return alert, contexts, recorded, sent


def _event(position):
    return {
        "Name": "6-file4321-192.168.1.20",
        "Value": f"Watching from Living Room at {position}",
    }


def test_lowercase_metadata_reaches_history_and_template(monkeypatch):
    alert, contexts, recorded, _ = _alert(monkeypatch, _settings())

    assert asyncio.run(alert._handle_event_sync("activities.set", _event("16m09s")))

    extra = recorded[0]["extra"]
    assert extra["episode_title"] == "Not Everyone Was Playing"
    assert extra["duration"] == "32m 19s"
    assert extra["summary"] == "Teams compete."
    assert extra["year"] == "2026"
    ctx = contexts[0]
    assert ctx["season_number"] == 8
    assert ctx["episode_number00"] == "38"
    assert ctx["release_year"] == "2026"
    assert ctx["release_date"] == "2026-10-29"
    assert ctx["library_path"].endswith("S08E38.mpg")
    assert ctx["channel"] == "205"
    assert ctx["favorited"] is True
    assert ctx["full_summary"] == "Teams compete in a word game."
    assert ctx["progress_percent"] == 50


def test_display_toggles_do_not_change_stored_history(monkeypatch):
    settings = _settings(vod_title=False, vod_device_name=False, vod_device_ip=False)
    alert, _, recorded, _ = _alert(monkeypatch, settings)

    asyncio.run(alert._handle_event_sync("activities.set", _event("1m")))

    row = recorded[0]
    assert row["content_name"] == "25 Words or Less"
    assert row["device_name"] == "Living Room"
    assert row["device_ip"] == "192.168.1.20"


@pytest.mark.parametrize("jump,expected", [("12m", True), ("2m", False)])
def test_significant_progress_bypasses_active_cooldown(monkeypatch, jump, expected):
    settings = _settings(vod_significant_threshold=600)
    alert, _, _, sent = _alert(monkeypatch, settings)

    assert asyncio.run(alert._handle_event_sync("activities.set", _event("0m")))
    # Intermediate updates inside the cooldown must not reset the baseline.
    asyncio.run(alert._handle_event_sync("activities.set", _event("1m")))
    result = asyncio.run(alert._handle_event_sync("activities.set", _event(jump)))

    assert result is expected
    assert len(sent) == (2 if expected else 1)


def test_cooldown_still_suppresses_without_threshold(monkeypatch):
    alert, _, _, sent = _alert(monkeypatch, _settings())
    asyncio.run(alert._handle_event_sync("activities.set", _event("0m")))
    assert not asyncio.run(alert._handle_event_sync("activities.set", _event("50m")))
    assert len(sent) == 1
    assert time.time() - alert.active_sessions["vod4321-192.168.1.20"]["last_notification"] < 5
