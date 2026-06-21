from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock, patch

from core.notifications.notification import (
    NotificationManager,
    _resolve_routing,
    APPRISE_DEST_KEYS,
    ALL_DEST_KEYS,
)


class TestResolveRoutingDefaults:
    def test_empty_config_returns_all_enabled(self):
        result = _resolve_routing("dvr_abc", "channel", {})
        assert result == {k: True for k in ALL_DEST_KEYS}

    def test_missing_dvr_id_returns_all_enabled(self):
        config = {"dvr_abc": {"channel": {"discord": False}}}
        result = _resolve_routing("", "channel", config)
        assert all(result.values())

    def test_missing_event_type_returns_all_enabled(self):
        config = {"dvr_abc": {"channel": {"discord": False}}}
        result = _resolve_routing("dvr_abc", "", config)
        assert all(result.values())

    def test_dvr_not_in_config_returns_all_enabled(self):
        config = {"dvr_other": {"channel": {"discord": False}}}
        result = _resolve_routing("dvr_abc", "channel", config)
        assert all(result.values())

    def test_event_not_in_dvr_config_returns_all_enabled(self):
        config = {"dvr_abc": {"disk": {"pushover": False}}}
        result = _resolve_routing("dvr_abc", "channel", config)
        assert all(result.values())

    def test_malformed_nested_routing_values_return_all_enabled(self):
        configs: list[Any] = [
            {"dvr_abc": "not-a-map"},
            {"dvr_abc": {"channel": "not-a-map"}},
            "not-a-map",
        ]
        for config in configs:
            result = _resolve_routing("dvr_abc", "channel", config)
            assert result == {k: True for k in ALL_DEST_KEYS}


class TestResolveRoutingPerChannel:
    def test_discord_disabled_others_enabled(self):
        config = {"dvr_1": {"channel": {"discord": False}}}
        result = _resolve_routing("dvr_1", "channel", config)
        assert result["discord"] is False
        assert result["pushover"] is True
        assert result["webhook"] is True

    def test_pushover_disabled_discord_enabled(self):
        config = {"dvr_1": {"vod": {"pushover": False}}}
        result = _resolve_routing("dvr_1", "vod", config)
        assert result["pushover"] is False
        assert result["discord"] is True

    def test_webhook_disabled_apprise_channels_enabled(self):
        config = {"dvr_1": {"disk": {"webhook": False}}}
        result = _resolve_routing("dvr_1", "disk", config)
        assert result["webhook"] is False
        assert all(result[k] for k in APPRISE_DEST_KEYS)

    def test_multiple_channels_disabled(self):
        config = {
            "dvr_1": {
                "recording": {"pushover": False, "discord": False, "telegram": False}
            }
        }
        result = _resolve_routing("dvr_1", "recording", config)
        assert result["pushover"] is False
        assert result["discord"] is False
        assert result["telegram"] is False
        assert result["slack"] is True
        assert result["webhook"] is True

    def test_all_channels_disabled(self):
        config = {"dvr_1": {"channel": {k: False for k in ALL_DEST_KEYS}}}
        result = _resolve_routing("dvr_1", "channel", config)
        assert all(not v for v in result.values())

    def test_absent_key_within_event_defaults_true(self):
        config = {"dvr_1": {"channel": {"discord": False}}}
        result = _resolve_routing("dvr_1", "channel", config)
        for key in ALL_DEST_KEYS:
            if key == "discord":
                assert result[key] is False
            else:
                assert result[key] is True

    def test_multiple_dvrs_independent(self):
        config = {
            "dvr_1": {"channel": {"discord": False}},
            "dvr_2": {"channel": {"pushover": False}},
        }
        r1 = _resolve_routing("dvr_1", "channel", config)
        r2 = _resolve_routing("dvr_2", "channel", config)
        assert r1["discord"] is False and r1["pushover"] is True
        assert r2["pushover"] is False and r2["discord"] is True

    def test_event_types_independent_per_dvr(self):
        config = {"dvr_1": {"channel": {"discord": False}, "vod": {"pushover": False}}}
        rc = _resolve_routing("dvr_1", "channel", config)
        rv = _resolve_routing("dvr_1", "vod", config)
        assert rc["discord"] is False and rc["pushover"] is True
        assert rv["pushover"] is False and rv["discord"] is True


def _make_manager_with_provider():
    manager = NotificationManager(rate_limit=100, rate_window=1)
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Apprise"
    provider.is_configured.return_value = True
    provider.send_notification.return_value = True
    manager.providers["Apprise"] = provider
    return manager, provider


class TestNotificationManagerPerChannelRouting:
    def test_no_routing_config_delivers_to_all(self):
        manager, provider = _make_manager_with_provider()
        with patch(
            "core.notifications.notification._load_routing_config", return_value={}
        ):
            result = manager.send_notification(
                "Title", "Msg", dvr_id="dvr_1", event_type="channel"
            )
        assert result is True
        provider.send_notification.assert_called_once()
        call_kwargs = provider.send_notification.call_args[1]
        assert call_kwargs["allowed_apprise_destinations"] == set(APPRISE_DEST_KEYS)

    def test_discord_disabled_pushover_in_allowed_set(self):
        manager, provider = _make_manager_with_provider()
        config = {"dvr_1": {"channel": {"discord": False}}}
        with patch(
            "core.notifications.notification._load_routing_config", return_value=config
        ):
            manager.send_notification(
                "Title", "Msg", dvr_id="dvr_1", event_type="channel"
            )
        call_kwargs = provider.send_notification.call_args[1]
        allowed = call_kwargs["allowed_apprise_destinations"]
        assert "discord" not in allowed
        assert "pushover" in allowed

    def test_webhook_disabled_in_routing(self):
        manager = NotificationManager(rate_limit=100, rate_window=1)
        wh = MagicMock()
        wh.is_configured.return_value = True
        wh.send_notification.return_value = True
        manager.webhook_manager = wh

        config = {"dvr_1": {"vod": {"webhook": False}}}
        with patch(
            "core.notifications.notification._load_routing_config", return_value=config
        ):
            manager.send_notification("Title", "Msg", dvr_id="dvr_1", event_type="vod")
        wh.send_notification.assert_not_called()

    def test_webhook_enabled_in_routing_calls_webhook(self):
        manager = NotificationManager(rate_limit=100, rate_window=1)
        wh = MagicMock()
        wh.is_configured.return_value = True
        wh.send_notification.return_value = True
        manager.webhook_manager = wh

        with patch(
            "core.notifications.notification._load_routing_config", return_value={}
        ):
            result = manager.send_notification(
                "Title", "Msg", dvr_id="dvr_1", event_type="disk"
            )
        wh.send_notification.assert_called_once()
        assert result is True

    def test_missing_dvr_in_routing_delivers_to_all(self):
        manager, provider = _make_manager_with_provider()
        config = {"dvr_other": {"channel": {"discord": False}}}
        with patch(
            "core.notifications.notification._load_routing_config", return_value=config
        ):
            result = manager.send_notification(
                "Title", "Msg", dvr_id="dvr_1", event_type="channel"
            )
        assert result is True
        call_kwargs = provider.send_notification.call_args[1]
        assert "discord" in call_kwargs["allowed_apprise_destinations"]

    def test_no_dvr_id_no_filter_passed(self):
        manager, provider = _make_manager_with_provider()
        with patch(
            "core.notifications.notification._load_routing_config", return_value={}
        ):
            result = manager.send_notification("Title", "Msg")
        assert result is True
        call_kwargs = provider.send_notification.call_args[1]
        assert "allowed_apprise_destinations" not in call_kwargs


class TestBaseAlertRoutingInjection:
    def _make_alert(self, routing_event_type, dvr_id=None):
        from core.alerts.base import BaseAlert

        nm = MagicMock()
        nm.send_notification.return_value = True

        class ConcreteAlert(BaseAlert):
            ALERT_TYPE = "Test"
            ROUTING_EVENT_TYPE = routing_event_type
            dvr: Any = None

            def _should_handle_event(self, *a, **kw):
                return True

            async def _handle_event(self, *a, **kw):
                return True

        alert = ConcreteAlert(nm)
        if dvr_id is not None:
            alert.dvr = types.SimpleNamespace(id=dvr_id)
        return alert, nm

    def test_send_alert_injects_event_type(self):
        alert, nm = self._make_alert("channel", dvr_id="dvr_abc")
        alert.send_alert("T", "M")
        nm.send_notification.assert_called_once()
        _, kwargs = nm.send_notification.call_args
        assert kwargs.get("event_type") == "channel"

    def test_send_alert_injects_dvr_id(self):
        alert, nm = self._make_alert("vod", dvr_id="dvr_xyz")
        alert.send_alert("T", "M")
        _, kwargs = nm.send_notification.call_args
        assert kwargs.get("dvr_id") == "dvr_xyz"

    def test_send_alert_no_dvr_no_injection(self):
        alert, nm = self._make_alert("disk")
        alert.send_alert("T", "M")
        _, kwargs = nm.send_notification.call_args
        assert "dvr_id" not in kwargs

    def test_caller_supplied_event_type_not_overridden(self):
        alert, nm = self._make_alert("channel", dvr_id="dvr_abc")
        alert.send_alert("T", "M", event_type="custom")
        _, kwargs = nm.send_notification.call_args
        assert kwargs.get("event_type") == "custom"

    def test_caller_supplied_dvr_id_not_overridden(self):
        alert, nm = self._make_alert("channel", dvr_id="dvr_abc")
        alert.send_alert("T", "M", dvr_id="dvr_override")
        _, kwargs = nm.send_notification.call_args
        assert kwargs.get("dvr_id") == "dvr_override"


class TestAppriseProviderDestinationFilter:
    def _make_provider(self, entries):
        from core.notifications.providers.apprise import AppriseProvider

        provider = AppriseProvider.__new__(AppriseProvider)
        provider.url_entries = entries
        provider.urls = [url for _, url in entries]
        provider.apprise = MagicMock()
        provider.settings = None
        return provider

    def test_empty_allowed_set_skips_delivery(self):
        provider = self._make_provider(
            [("pushover", "pover://abc"), ("discord", "discord://1/t")]
        )
        with patch.object(provider, "is_configured", return_value=True):
            result = provider.send_notification(
                "T", "M", allowed_apprise_destinations=set()
            )
        assert result is False

    def test_pushover_only_in_allowed_passes_only_pushover_url(self):
        pushover_url = "pover://abc"
        discord_url = "discord://1/t"
        entries = [("pushover", pushover_url), ("discord", discord_url)]
        provider = self._make_provider(entries)
        with patch.object(provider, "is_configured", return_value=True):
            apprise_mod = MagicMock()
            fake_apprise = MagicMock()
            fake_apprise.notify.return_value = True
            apprise_mod.Apprise.return_value = fake_apprise
            with patch("importlib.import_module", return_value=apprise_mod):
                provider.send_notification(
                    "T", "M", allowed_apprise_destinations={"pushover"}
                )
        added_urls = [call[0][0] for call in fake_apprise.add.call_args_list]
        assert pushover_url in added_urls
        assert discord_url not in added_urls

    def test_no_filter_sends_to_all(self):
        entries = [("pushover", "pover://abc"), ("slack", "slack://t")]
        provider = self._make_provider(entries)
        with patch.object(provider, "is_configured", return_value=True):
            apprise_mod = MagicMock()
            fake_apprise = MagicMock()
            fake_apprise.notify.return_value = True
            apprise_mod.Apprise.return_value = fake_apprise
            with patch("importlib.import_module", return_value=apprise_mod):
                provider.send_notification("T", "M")
        added_urls = [call[0][0] for call in fake_apprise.add.call_args_list]
        assert "pover://abc" in added_urls
        assert "slack://t" in added_urls


class TestRoutingEventTypeConstants:
    def test_channel_routing_event_type(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        assert ChannelWatchingAlert.ROUTING_EVENT_TYPE == "channel"

    def test_vod_routing_event_type(self):
        from core.alerts.vod_watching import VODWatchingAlert

        assert VODWatchingAlert.ROUTING_EVENT_TYPE == "vod"

    def test_recording_routing_event_type(self):
        from core.alerts.recording_events import RecordingEventsAlert

        assert RecordingEventsAlert.ROUTING_EVENT_TYPE == "recording"

    def test_disk_routing_event_type(self):
        from core.alerts.disk_space import DiskSpaceAlert

        assert DiskSpaceAlert.ROUTING_EVENT_TYPE == "disk"


class TestAlertSourcePreviewPlugin:
    def test_custom_alert_source_is_marked_preview_and_not_registered(self, tmp_path):
        from pathlib import Path
        import shutil

        from core.notifications.providers.examples import custom_alert_source
        from core.notifications.providers.plugin_loader import load_notification_plugins

        manager = MagicMock()
        preview_path = Path(custom_alert_source.__file__)
        isolated_plugin_dir = tmp_path / "plugins"
        isolated_plugin_dir.mkdir()
        shutil.copy(preview_path, isolated_plugin_dir / preview_path.name)

        registered = load_notification_plugins(manager, plugin_dir=isolated_plugin_dir)

        assert custom_alert_source.__plugin_status__ == "preview-v1.1-not-loaded"
        assert registered == []
        manager.register_provider.assert_not_called()


class TestDestKeyConstants:
    def test_all_apprise_dest_keys_present(self):
        assert set(APPRISE_DEST_KEYS) == {
            "pushover",
            "discord",
            "email",
            "telegram",
            "slack",
            "gotify",
            "matrix",
            "custom",
        }

    def test_all_dest_keys_includes_webhook(self):
        assert "webhook" in ALL_DEST_KEYS
        assert set(ALL_DEST_KEYS) == set(APPRISE_DEST_KEYS) | {"webhook"}

    def test_service_map_keys_match_dest_keys(self):
        from core.notifications.providers.apprise import AppriseProvider

        service_map_dest_keys = {
            k.removeprefix("apprise_") for k in AppriseProvider.SERVICE_MAP
        }
        assert service_map_dest_keys == set(APPRISE_DEST_KEYS)


class TestRoutingDefaultPreservation:
    def test_empty_notification_routing_in_settings_means_all_enabled(self):
        from core.helpers.config import CoreSettings

        fake_settings = types.SimpleNamespace(notification_routing={})
        with patch.object(CoreSettings, "get", return_value=fake_settings):
            from core.notifications.notification import _load_routing_config

            config = _load_routing_config()
        assert config == {}

    def test_resolve_routing_with_empty_config_is_all_true(self):
        result = _resolve_routing("any_dvr", "any_event", {})
        assert all(result.values())
