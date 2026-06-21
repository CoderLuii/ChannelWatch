from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.helpers.initialize import initialize_alerts
from core.engine.alert_manager import AlertManager


def _make_dvr(dvr_id="dvr_abc12345"):
    return SimpleNamespace(
        id=dvr_id, name="Test DVR", host="192.168.1.1", port=8089, overrides={}
    )


def _make_nm():
    return MagicMock()


def _make_settings():
    return MagicMock()


class TestAlertManagerDvrValidation:
    def test_raises_when_dvr_is_none(self):
        with pytest.raises(ValueError):
            AlertManager(_make_nm(), _make_settings(), dvr=None)

    def test_raises_when_dvr_id_is_none(self):
        dvr = SimpleNamespace(id=None, name="Test DVR")
        with pytest.raises(ValueError):
            AlertManager(_make_nm(), _make_settings(), dvr=dvr)

    def test_raises_when_dvr_id_is_empty_string(self):
        dvr = SimpleNamespace(id="", name="Test DVR")
        with pytest.raises(ValueError):
            AlertManager(_make_nm(), _make_settings(), dvr=dvr)

    def test_raises_when_dvr_id_is_default(self):
        dvr = SimpleNamespace(id="default", name="Test DVR")
        with pytest.raises(ValueError):
            AlertManager(_make_nm(), _make_settings(), dvr=dvr)

    def test_exact_message_on_none_dvr(self):
        with pytest.raises(
            ValueError,
            match="AlertManager requires explicit dvr_id; no default fallback allowed",
        ):
            AlertManager(_make_nm(), _make_settings(), dvr=None)

    def test_exact_message_on_default_id(self):
        dvr = SimpleNamespace(id="default", name="Test DVR")
        with pytest.raises(
            ValueError,
            match="AlertManager requires explicit dvr_id; no default fallback allowed",
        ):
            AlertManager(_make_nm(), _make_settings(), dvr=dvr)

    def test_exact_message_on_empty_id(self):
        dvr = SimpleNamespace(id="", name="Test DVR")
        with pytest.raises(
            ValueError,
            match="AlertManager requires explicit dvr_id; no default fallback allowed",
        ):
            AlertManager(_make_nm(), _make_settings(), dvr=dvr)

    def test_accepts_explicit_dvr_id(self):
        dvr = _make_dvr("dvr_abc12345")
        manager = AlertManager(_make_nm(), _make_settings(), dvr=dvr)
        assert manager.dvr is dvr

    def test_state_file_uses_explicit_dvr_id(self):
        dvr = _make_dvr("dvr_abc12345")
        manager = AlertManager(_make_nm(), _make_settings(), dvr=dvr)
        assert "dvr_abc12345" in str(manager._state_file)

    def test_state_file_never_contains_default(self):
        dvr = _make_dvr("dvr_xyz99999")
        manager = AlertManager(_make_nm(), _make_settings(), dvr=dvr)
        assert "default" not in str(manager._state_file)

    @pytest.mark.asyncio
    async def test_run_cleanup_accepts_sync_and_async_alert_cleanup(self):
        class SyncAlert:
            def __init__(self):
                self.called = False

            def cleanup(self):
                self.called = True

        class AsyncAlert:
            def __init__(self):
                self.called = False

            async def cleanup(self):
                self.called = True

        sync_alert = SyncAlert()
        async_alert = AsyncAlert()
        manager = AlertManager(_make_nm(), _make_settings(), dvr=_make_dvr())
        manager.alert_instances = {"sync": sync_alert, "async": async_alert}

        await manager._run_cleanup()

        assert sync_alert.called is True
        assert async_alert.called is True


class TestInitializeAlertsRequiresDvr:
    def test_raises_when_dvr_is_none(self):
        with pytest.raises(ValueError):
            initialize_alerts(_make_nm(), _make_settings(), dvr=None)

    def test_exact_message_when_dvr_is_none(self):
        with pytest.raises(
            ValueError, match="initialize_alerts requires an explicit dvr"
        ):
            initialize_alerts(_make_nm(), _make_settings(), dvr=None)
