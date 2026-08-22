from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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

    def test_explicit_malformed_routing_values_fail_closed(self):
        configs: list[Any] = [
            {"dvr_abc": "not-a-map"},
            {"dvr_abc": None},
            {"dvr_abc": {"channel": "not-a-map"}},
            {"dvr_abc": {"channel": None}},
            "not-a-map",
        ]
        for config in configs:
            result = _resolve_routing("dvr_abc", "channel", config)
            assert result == {k: False for k in ALL_DEST_KEYS}

    def test_malformed_explicit_destination_value_fails_closed(self):
        result = _resolve_routing(
            "dvr_abc", "channel", {"dvr_abc": {"channel": {"discord": "false"}}}
        )
        assert result["discord"] is False
        assert result["pushover"] is False


class TestResolveRoutingPerChannel:
    def test_partial_explicit_route_fails_closed_for_omitted_destinations(self):
        config = {"dvr_1": {"channel": {"discord": False}}}
        result = _resolve_routing("dvr_1", "channel", config)
        assert result["discord"] is False
        assert result["pushover"] is False
        assert result["webhook"] is False

    def test_pushover_disabled_discord_enabled(self):
        config = {"dvr_1": {"vod": {"pushover": False, "discord": True}}}
        result = _resolve_routing("dvr_1", "vod", config)
        assert result["pushover"] is False
        assert result["discord"] is True

    def test_webhook_only_partial_route_does_not_enable_apprise(self):
        config = {"dvr_1": {"disk": {"webhook": False}}}
        result = _resolve_routing("dvr_1", "disk", config)
        assert result["webhook"] is False
        assert all(not result[k] for k in APPRISE_DEST_KEYS)

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
        assert result["slack"] is False
        assert result["webhook"] is False

    def test_all_channels_disabled(self):
        config = {"dvr_1": {"channel": {k: False for k in ALL_DEST_KEYS}}}
        result = _resolve_routing("dvr_1", "channel", config)
        assert all(not v for v in result.values())

    def test_absent_key_within_explicit_event_defaults_false(self):
        config = {"dvr_1": {"channel": {"discord": False}}}
        result = _resolve_routing("dvr_1", "channel", config)
        for key in ALL_DEST_KEYS:
            if key == "discord":
                assert result[key] is False
            else:
                assert result[key] is False

    def test_multiple_dvrs_independent(self):
        config = {
            "dvr_1": {"channel": {"discord": False, "pushover": True}},
            "dvr_2": {"channel": {"pushover": False, "discord": True}},
        }
        r1 = _resolve_routing("dvr_1", "channel", config)
        r2 = _resolve_routing("dvr_2", "channel", config)
        assert r1["discord"] is False and r1["pushover"] is True
        assert r2["pushover"] is False and r2["discord"] is True

    def test_event_types_independent_per_dvr(self):
        config = {
            "dvr_1": {
                "channel": {"discord": False, "pushover": True},
                "vod": {"pushover": False, "discord": True},
            }
        }
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
        config = {
            "dvr_1": {"channel": {"discord": False, "pushover": True}}
        }
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

    def test_concrete_destination_id_sends_only_one_target(self):
        entries = [("pushover", "pover://abc"), ("slack", "slack://t")]
        provider = self._make_provider(entries)
        with patch.object(provider, "is_configured", return_value=True):
            apprise_mod = MagicMock()
            fake_apprise = MagicMock()
            fake_apprise.notify.return_value = True
            apprise_mod.Apprise.return_value = fake_apprise
            with patch("importlib.import_module", return_value=apprise_mod):
                assert provider.send_notification(
                    "T", "M", apprise_destination_id="slack"
                )

        added_urls = [call[0][0] for call in fake_apprise.add.call_args_list]
        assert added_urls == ["slack://t"]
        assert provider.notification_destinations() == [
            ("pushover", "pushover"),
            ("slack", "slack"),
        ]


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

    def test_none_notification_routing_in_settings_means_legacy_absence(self):
        from core.helpers.config import CoreSettings

        fake_settings = types.SimpleNamespace(notification_routing=None)
        with patch.object(CoreSettings, "get", return_value=fake_settings):
            from core.notifications.notification import _load_routing_config

            config = _load_routing_config()

        assert config == {}

    def test_resolve_routing_with_empty_config_is_all_true(self):
        result = _resolve_routing("any_dvr", "any_event", {})
        assert all(result.values())

    def test_settings_read_failure_is_not_treated_as_legacy_empty_routing(self):
        from core.helpers.config import CoreSettings
        from core.notifications.notification import _load_routing_config

        with patch.object(CoreSettings, "get", side_effect=RuntimeError("unreadable")):
            config = _load_routing_config()

        assert config is None
        assert not any(_resolve_routing("dvr_1", "channel", config).values())

    @pytest.mark.parametrize("malformed", [[], "", 0, False])
    def test_falsy_malformed_runtime_routing_is_not_broadened(self, malformed):
        from core.helpers.config import CoreSettings
        from core.notifications.notification import _load_routing_config

        fake_settings = types.SimpleNamespace(notification_routing=malformed)
        with patch.object(CoreSettings, "get", return_value=fake_settings):
            config = _load_routing_config()

        assert config == malformed
        assert not any(_resolve_routing("dvr_1", "channel", config).values())

    def test_falsy_malformed_runtime_routing_does_not_deliver_webhook(self):
        from core.helpers.config import CoreSettings

        manager = NotificationManager(rate_limit=10, rate_window=60)
        webhook = MagicMock()
        webhook.is_configured.return_value = True
        webhook.send_notification.return_value = True
        manager.register_webhook_manager(webhook)
        fake_settings = types.SimpleNamespace(notification_routing=[])

        with patch.object(CoreSettings, "get", return_value=fake_settings):
            delivered = manager.send_notification(
                "Malformed routing",
                "must fail closed",
                dvr_id="dvr_1",
                event_type="channel",
            )

        assert delivered is False
        webhook.send_notification.assert_not_called()


class TestRoutingSaveValidation:
    def test_partial_event_map_is_normalized_fail_closed(self):
        from core.notifications.routing import normalize_notification_routing

        normalized = normalize_notification_routing(
            {"dvr_1": {"channel": {"discord": True}}},
            [{"id": "dvr_1"}],
        )

        route = normalized["dvr_1"]["channel"]
        assert route["discord"] is True
        assert all(not route[key] for key in ALL_DEST_KEYS if key != "discord")

    def test_object_dvr_id_is_supported(self):
        from core.notifications.routing import normalize_notification_routing

        normalized = normalize_notification_routing(
            {"dvr_1": {"disk": {"webhook": False}}},
            [types.SimpleNamespace(id="dvr_1")],
        )

        assert normalized["dvr_1"]["disk"] == {
            key: False for key in ALL_DEST_KEYS
        }

    @pytest.mark.parametrize(
        ("routing", "message"),
        [
            ({"stale": {"channel": {"discord": True}}}, "unknown DVR"),
            ({"dvr_1": None}, "routing must be an object"),
            ({"dvr_1": {"channel": None}}, "routing must be an object"),
            ({"dvr_1": {"mystery": {"discord": True}}}, "unknown event"),
            ({"dvr_1": {"channel": {"carrier_pigeon": True}}}, "unknown destinations"),
            ({"dvr_1": {"channel": {"discord": "false"}}}, "must be boolean"),
        ],
    )
    def test_invalid_explicit_routes_report_diagnostics(self, routing, message):
        from core.notifications.routing import normalize_notification_routing

        with pytest.raises(ValueError, match=message):
            normalize_notification_routing(routing, [{"id": "dvr_1"}])


def test_apprise_circuits_are_isolated_per_concrete_destination():
    class TwoDestinationApprise:
        PROVIDER_TYPE = "Apprise"

        def __init__(self):
            self.calls: list[str] = []

        def is_configured(self):
            return True

        def notification_destinations(self, allowed):
            return [
                ("discord", "discord"),
                ("pushover", "pushover"),
            ]

        def send_notification(self, _title, _message, **kwargs):
            destination_id = kwargs["apprise_destination_id"]
            self.calls.append(destination_id)
            return destination_id == "pushover"

    manager = NotificationManager(rate_limit=10, rate_window=60)
    manager.circuit_breaker.FAILURE_THRESHOLD = 1
    provider = TwoDestinationApprise()
    manager.register_provider(provider)

    with patch("core.notifications.notification._load_routing_config", return_value={}):
        assert manager.send_notification(
            "First", "Message", dvr_id="dvr_1", event_type="channel"
        )
        assert manager.send_notification(
            "Second", "Message", dvr_id="dvr_1", event_type="channel"
        )

    assert provider.calls == ["discord", "pushover", "pushover"]
    assert manager.circuit_breaker.is_open(
        "dvr_1", "apprise", "Apprise", "discord"
    )
    assert not manager.circuit_breaker.is_open(
        "dvr_1", "apprise", "Apprise", "pushover"
    )
