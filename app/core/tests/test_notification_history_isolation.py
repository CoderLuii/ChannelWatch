import time
from types import SimpleNamespace
from unittest.mock import MagicMock


from core.helpers.activity_recorder import (
    should_record_activity,
    cleanup_notification_history,
)
from core.engine.alert_manager import AlertManager


def _make_dvr(dvr_id):
    return SimpleNamespace(
        id=dvr_id, name=f"DVR-{dvr_id}", host="192.168.1.1", port=8089, overrides={}
    )


def _make_manager(dvr_id):
    return AlertManager(MagicMock(), MagicMock(), dvr=_make_dvr(dvr_id))


class TestShouldRecordActivityWithExplicitHistory:
    def test_first_call_always_records(self):
        history: dict[str, float] = {}
        assert should_record_activity("key1", history) is True

    def test_immediate_duplicate_is_suppressed(self):
        history: dict[str, float] = {}
        should_record_activity("key1", history)
        assert should_record_activity("key1", history) is False

    def test_separate_dicts_are_independent(self):
        history_a: dict[str, float] = {}
        history_b: dict[str, float] = {}
        should_record_activity("ch5-device1", history_a)
        assert should_record_activity("ch5-device1", history_b) is True

    def test_different_keys_in_same_history_both_record(self):
        history: dict[str, float] = {}
        assert should_record_activity("key-a", history) is True
        assert should_record_activity("key-b", history) is True

    def test_key_recorded_in_history_after_first_call(self):
        history: dict[str, float] = {}
        should_record_activity("mykey", history)
        assert "mykey" in history


class TestCleanupNotificationHistory:
    def test_removes_old_entries(self):
        history: dict[str, float] = {"old-key": time.time() - 3700}
        cleanup_notification_history(history)
        assert "old-key" not in history

    def test_keeps_recent_entries(self):
        history: dict[str, float] = {"new-key": time.time()}
        cleanup_notification_history(history)
        assert "new-key" in history

    def test_mixed_entries(self):
        now = time.time()
        history: dict[str, float] = {"old": now - 4000, "fresh": now}
        cleanup_notification_history(history)
        assert "old" not in history
        assert "fresh" in history


class TestAlertManagerOwnsHistory:
    def test_alert_manager_has_notification_history(self):
        manager = _make_manager("dvr_aaa111")
        assert hasattr(manager, "_notification_history")
        assert isinstance(manager._notification_history, dict)

    def test_alert_manager_history_dict_type_is_stable_on_new_manager(self):
        manager = _make_manager("dvr_aaa111")
        assert hasattr(manager, "_notification_history")
        assert isinstance(manager._notification_history, dict)

    def test_two_managers_have_independent_history_dicts(self):
        am_a = _make_manager("dvr_aaa111")
        am_b = _make_manager("dvr_bbb222")
        assert am_a._notification_history is not am_b._notification_history

    def test_two_new_managers_do_not_share_history_dict_reference(self):
        am_a = _make_manager("dvr_aaa111")
        am_b = _make_manager("dvr_bbb222")
        assert am_a._notification_history is not am_b._notification_history


class TestCrossDvrIsolation:
    def test_same_key_records_independently_on_each_dvr(self):
        am_a = _make_manager("dvr_aaa111")
        am_b = _make_manager("dvr_bbb222")

        result_a = should_record_activity(
            "ch5-device1",
            am_a._notification_history,
        )
        result_b = should_record_activity(
            "ch5-device1",
            am_b._notification_history,
        )

        assert result_a is True, "DVR-A should record the first occurrence"
        assert result_b is True, "DVR-B should not be suppressed by DVR-A's history"

    def test_dvr_a_suppresses_its_own_duplicate(self):
        am_a = _make_manager("dvr_aaa111")

        should_record_activity("ch5-device1", am_a._notification_history)
        result = should_record_activity("ch5-device1", am_a._notification_history)

        assert result is False, "DVR-A should suppress its own rapid duplicate"

    def test_dvr_b_suppression_does_not_affect_dvr_a(self):
        am_a = _make_manager("dvr_aaa111")
        am_b = _make_manager("dvr_bbb222")

        should_record_activity("ch5-device1", am_b._notification_history)

        result_a = should_record_activity(
            "ch5-device1",
            am_a._notification_history,
        )
        assert result_a is True, (
            "DVR-A history is independent — DVR-B's suppression should not carry over"
        )

    def test_ten_dvrs_all_record_same_key(self):
        managers = [_make_manager(f"dvr_{i:08x}") for i in range(10)]
        results = [
            should_record_activity("ch5-device1", m._notification_history)
            for m in managers
        ]
        assert all(results), "All 10 DVRs should independently record the same key"
