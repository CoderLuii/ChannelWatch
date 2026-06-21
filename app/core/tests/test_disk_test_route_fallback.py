# pyright: reportMissingImports=false

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from ui.backend.main import run_test_background


def make_settings(*, ds_test_route_override=""):
    dvr = SimpleNamespace(id="dvr_test", name="Test DVR", overrides={})
    settings = SimpleNamespace(
        dvr_servers=[
            {
                "id": "dvr_test",
                "host": "127.0.0.1",
                "port": 8089,
                "name": "Test DVR",
                "enabled": True,
            }
        ],
        global_rate_limit=20,
        global_rate_window=300,
        ds_test_route_override=ds_test_route_override,
        get_dvr_connections=lambda: [dvr],
    )
    return settings, dvr


class TestDiskTestRouteFallback:
    def test_disk_test_route_falls_back_to_normal_providers_when_override_has_no_active_providers(
        self,
    ):
        settings, dvr = make_settings(ds_test_route_override="discord://test-route")
        override_notification_manager = MagicMock()
        override_notification_manager.get_active_providers.return_value = []
        normal_notification_manager = MagicMock()
        alert_manager = object()

        with (
            patch("ui.backend.main.CORE_APP_AVAILABLE", True),
            patch("ui.backend.main._get_core_settings_sync", return_value=settings),
            patch(
                "ui.backend.main._get_dvr_servers",
                return_value=[("dvr_test", "Test DVR", "http://127.0.0.1:8089")],
            ),
            patch("core.helpers.logging.log_handler", object()),
            patch(
                "core.helpers.initialize.initialize_notifications",
                side_effect=[
                    override_notification_manager,
                    normal_notification_manager,
                ],
            ) as initialize_notifications,
            patch(
                "core.helpers.initialize.initialize_alerts", return_value=alert_manager
            ) as initialize_alerts,
            patch("core.diagnostics.run_test", return_value=True) as run_test,
        ):
            result = run_test_background("Test Disk Space Alert")

        assert result.success is True
        assert initialize_notifications.call_count == 2

        override_settings = initialize_notifications.call_args_list[0].args[0]
        fallback_settings = initialize_notifications.call_args_list[1].args[0]

        assert override_settings is not fallback_settings
        assert override_settings.apprise_custom == "discord://test-route"
        assert getattr(override_settings, "apprise_pushover", "") == ""
        assert initialize_alerts.call_args.args[0] is normal_notification_manager
        assert initialize_alerts.call_args.args[1] is fallback_settings
        assert initialize_alerts.call_args.kwargs["dvr"] is dvr
        run_test.assert_called_once_with("Disk-Space", "127.0.0.1", 8089, alert_manager)

    def test_disk_test_route_prefers_override_when_override_initializes_active_providers(
        self,
    ):
        settings, dvr = make_settings(ds_test_route_override="discord://test-route")
        override_notification_manager = MagicMock()
        override_notification_manager.get_active_providers.return_value = ["discord"]
        alert_manager = object()

        with (
            patch("ui.backend.main.CORE_APP_AVAILABLE", True),
            patch("ui.backend.main._get_core_settings_sync", return_value=settings),
            patch(
                "ui.backend.main._get_dvr_servers",
                return_value=[("dvr_test", "Test DVR", "http://127.0.0.1:8089")],
            ),
            patch("core.helpers.logging.log_handler", object()),
            patch(
                "core.helpers.initialize.initialize_notifications",
                return_value=override_notification_manager,
            ) as initialize_notifications,
            patch(
                "core.helpers.initialize.initialize_alerts", return_value=alert_manager
            ) as initialize_alerts,
            patch("core.diagnostics.run_test", return_value=True) as run_test,
        ):
            result = run_test_background("Test Disk Space Alert")

        assert result.success is True
        assert initialize_notifications.call_count == 1

        override_settings = initialize_notifications.call_args.args[0]
        assert override_settings.apprise_custom == "discord://test-route"
        assert getattr(override_settings, "apprise_email", "") == ""
        assert initialize_alerts.call_args.args[0] is override_notification_manager
        assert initialize_alerts.call_args.args[1] is override_settings
        assert initialize_alerts.call_args.kwargs["dvr"] is dvr
        run_test.assert_called_once_with("Disk-Space", "127.0.0.1", 8089, alert_manager)
