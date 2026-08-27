import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dvr_health import (
    DvrHealthTracker,
    DvrHealthTransition,
    STARTUP_GRACE_SECONDS,
)


def test_startup_outage_waits_for_five_minute_grace(tmp_path: Path):
    clock = [10_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0],
        now=lambda: clock[0],
    )

    assert tracker.evaluate(healthy=False, delay_seconds=120) is None
    clock[0] += STARTUP_GRACE_SECONDS - 1
    assert tracker.evaluate(healthy=False, delay_seconds=120) is None
    clock[0] += 1

    transition = tracker.evaluate(healthy=False, delay_seconds=120)

    assert transition is not None
    assert transition.event == "unreachable"


def test_outage_after_confirmed_health_uses_configured_delay(tmp_path: Path):
    clock = [15_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0],
        now=lambda: clock[0],
    )

    assert tracker.evaluate(healthy=True, delay_seconds=120) is None
    clock[0] += 30
    assert tracker.evaluate(healthy=False, delay_seconds=120) is None
    clock[0] += 119
    assert tracker.evaluate(healthy=False, delay_seconds=120) is None
    clock[0] += 1

    transition = tracker.evaluate(healthy=False, delay_seconds=120)

    assert transition is not None
    assert transition.event == "unreachable"
    assert clock[0] < 15_000.0 + STARTUP_GRACE_SECONDS


def test_one_outage_and_one_corresponding_recovery_are_emitted(tmp_path: Path):
    clock = [20_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0] - STARTUP_GRACE_SECONDS,
        now=lambda: clock[0],
    )
    assert tracker.evaluate(healthy=True, delay_seconds=30) is None
    assert tracker.evaluate(healthy=False, delay_seconds=30) is None
    clock[0] += 30

    outage = tracker.evaluate(healthy=False, delay_seconds=30)
    duplicate = tracker.evaluate(healthy=False, delay_seconds=30)
    recovery = tracker.evaluate(healthy=True, delay_seconds=30)
    healthy_duplicate = tracker.evaluate(healthy=True, delay_seconds=30)

    assert outage is not None and outage.event == "unreachable"
    assert duplicate is None
    assert recovery is not None and recovery.event == "recovered"
    assert recovery.outage_id == outage.outage_id
    assert healthy_duplicate is None


def test_recovery_notification_is_paired_with_accepted_outage_delivery(
    tmp_path: Path,
):
    clock = [25_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0] - STARTUP_GRACE_SECONDS,
        now=lambda: clock[0],
    )
    tracker.evaluate(healthy=False, delay_seconds=30)
    clock[0] += 30
    outage = tracker.evaluate(healthy=False, delay_seconds=30)
    assert outage is not None

    tracker.set_notification_armed(outage.outage_id, True)
    recovery = tracker.evaluate(healthy=True, delay_seconds=30)

    assert recovery is not None
    assert recovery.notification_armed is True


def test_recovery_is_not_notification_armed_when_outage_was_not_accepted(
    tmp_path: Path,
):
    clock = [26_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0] - STARTUP_GRACE_SECONDS,
        now=lambda: clock[0],
    )
    tracker.evaluate(healthy=False, delay_seconds=30)
    clock[0] += 30
    outage = tracker.evaluate(healthy=False, delay_seconds=30)
    assert outage is not None

    tracker.set_notification_armed(outage.outage_id, False)
    recovery = tracker.evaluate(healthy=True, delay_seconds=30)

    assert recovery is not None
    assert recovery.notification_armed is False


def test_outage_state_survives_restart_without_duplicate(tmp_path: Path):
    clock = [30_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0] - STARTUP_GRACE_SECONDS,
        now=lambda: clock[0],
    )
    tracker.evaluate(healthy=False, delay_seconds=30)
    clock[0] += 30
    outage = tracker.evaluate(healthy=False, delay_seconds=30)
    assert outage is not None

    restarted = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0],
        now=lambda: clock[0],
    )

    assert restarted.evaluate(healthy=False, delay_seconds=30) is None
    recovery = restarted.evaluate(healthy=True, delay_seconds=30)
    assert recovery is not None and recovery.outage_id == outage.outage_id
    assert stat.S_IMODE(restarted.path.stat().st_mode) == 0o600


def test_recovery_without_a_notified_outage_is_silent(tmp_path: Path):
    clock = [40_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0],
        now=lambda: clock[0],
    )
    tracker.evaluate(healthy=False, delay_seconds=120)

    assert tracker.evaluate(healthy=True, delay_seconds=120) is None


def test_reset_cancels_pending_outage(tmp_path: Path):
    clock = [50_000.0]
    tracker = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=clock[0] - STARTUP_GRACE_SECONDS,
        now=lambda: clock[0],
    )
    tracker.evaluate(healthy=False, delay_seconds=120)
    clock[0] += 119
    tracker.reset()
    clock[0] += 1

    assert tracker.evaluate(healthy=False, delay_seconds=120) is None


def test_malformed_state_is_not_overwritten(tmp_path: Path):
    probe = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=0,
    )
    probe.path.parent.mkdir(parents=True, exist_ok=True)
    original = b"{broken"
    probe.path.write_bytes(original)
    blocked = DvrHealthTracker(
        config_dir=tmp_path,
        dvr_id="dvr-a",
        process_started_at=0,
    )

    with pytest.raises(RuntimeError, match="needs recovery"):
        blocked.evaluate(healthy=False, delay_seconds=30)

    assert probe.path.read_bytes() == original


@pytest.mark.asyncio
async def test_health_transition_uses_health_routing_and_returns_queue_acceptance(
    monkeypatch,
):
    from core import main

    manager = MagicMock()
    manager.enqueue_notification.return_value = True
    monkeypatch.setattr(main, "record_activity", MagicMock())
    monkeypatch.setattr(
        main, "_health_notification_manager_for", lambda *args, **kwargs: manager
    )
    dvr = SimpleNamespace(id="dvr-a", name="Synthetic DVR", overrides={})
    settings = SimpleNamespace(
        alert_dvr_health=True,
        dvr_alert_unreachable=True,
        dvr_alert_recovered=True,
        global_rate_limit=20,
        global_rate_window=300,
    )

    accepted = await main._emit_dvr_health_transition(
        dvr,
        settings,
        DvrHealthTransition("unreachable", "outage-1"),
        test_mode=True,
    )

    assert accepted is True
    manager.enqueue_notification.assert_called_once()
    assert manager.enqueue_notification.call_args.kwargs["event_type"] == "health"


@pytest.mark.asyncio
async def test_unarmed_recovery_records_history_without_sending_notification(
    monkeypatch,
):
    from core import main

    manager_factory = MagicMock()
    activity = MagicMock()
    monkeypatch.setattr(main, "record_activity", activity)
    monkeypatch.setattr(main, "_health_notification_manager_for", manager_factory)
    dvr = SimpleNamespace(id="dvr-a", name="Synthetic DVR", overrides={})
    settings = SimpleNamespace(
        alert_dvr_health=True,
        dvr_alert_unreachable=True,
        dvr_alert_recovered=True,
        global_rate_limit=20,
        global_rate_window=300,
    )

    accepted = await main._emit_dvr_health_transition(
        dvr,
        settings,
        DvrHealthTransition("recovered", "outage-1", False),
        test_mode=True,
    )

    assert accepted is False
    activity.assert_called_once()
    manager_factory.assert_not_called()


def test_effective_health_settings_apply_only_known_dvr_overrides():
    from core import main

    settings = SimpleNamespace(alert_dvr_health=False, untouched="original")
    dvr = SimpleNamespace(
        overrides={"alert_dvr_health": True, "unknown_setting": "ignored"}
    )

    effective = main._effective_dvr_settings(settings, dvr)

    assert effective.alert_dvr_health is True
    assert effective.untouched == "original"
    assert not hasattr(effective, "unknown_setting")
    assert settings.alert_dvr_health is False


def test_health_notification_manager_reuses_monitor_then_fallback(monkeypatch):
    from core import main

    dvr = SimpleNamespace(id="dvr-a", overrides={})
    settings = SimpleNamespace(global_rate_limit=20, global_rate_window=300)
    monitor_manager = SimpleNamespace(_queue_accepting=True)
    monitor = SimpleNamespace(
        alert_manager=SimpleNamespace(notification_manager=monitor_manager)
    )
    monkeypatch.setattr(main, "_dvr_monitors", {"dvr-a": monitor})
    monkeypatch.setattr(main, "_dvr_health_notification_managers", {})
    initializer = MagicMock()
    monkeypatch.setattr(main, "initialize_notifications", initializer)

    assert (
        main._health_notification_manager_for(
            dvr, settings, test_mode=True
        )
        is monitor_manager
    )
    initializer.assert_not_called()

    fallback = object()
    main._dvr_health_notification_managers["dvr-a"] = fallback
    assert (
        main._health_notification_manager_for(
            dvr, settings, test_mode=True
        )
        is fallback
    )


def test_health_notification_manager_creates_and_registers_fallback(monkeypatch):
    from core import main
    from core import notification_drain

    dvr = SimpleNamespace(id="dvr-a", overrides={"alert_dvr_health": True})
    settings = SimpleNamespace(
        alert_dvr_health=False,
        global_rate_limit=20,
        global_rate_window=300,
    )
    manager = SimpleNamespace(_queue_accepting=True)
    register = MagicMock()
    monkeypatch.setattr(main, "_dvr_monitors", {})
    monkeypatch.setattr(main, "_dvr_health_notification_managers", {})
    monkeypatch.setattr(main, "initialize_notifications", MagicMock(return_value=manager))
    monkeypatch.setattr(notification_drain, "register_notification_manager", register)

    result = main._health_notification_manager_for(dvr, settings, test_mode=True)

    assert result is manager
    assert main._dvr_health_notification_managers == {"dvr-a": manager}
    register.assert_called_once_with(manager)
    main.initialize_notifications.assert_called_once()
    assert main.initialize_notifications.call_args.args[0].alert_dvr_health is True


def test_health_notification_manager_handles_no_configured_destination(monkeypatch):
    from core import main

    monkeypatch.setattr(main, "_dvr_monitors", {})
    monkeypatch.setattr(main, "_dvr_health_notification_managers", {})
    monkeypatch.setattr(main, "initialize_notifications", MagicMock(return_value=None))
    dvr = SimpleNamespace(id="dvr-a", overrides={})
    settings = SimpleNamespace(global_rate_limit=20, global_rate_window=300)

    assert main._health_notification_manager_for(dvr, settings, test_mode=True) is None


def test_health_manager_close_and_tracker_reset_are_best_effort(monkeypatch):
    from core import main
    from core import notification_drain

    calls = []

    class Manager:
        def shutdown_delivery_queue(self, *, drain, timeout):
            calls.append((drain, timeout))

    manager = Manager()
    tracker = MagicMock()
    tracker.reset.side_effect = RuntimeError("corrupt state")
    unregister = MagicMock()
    monkeypatch.setattr(main, "_dvr_health_trackers", {"dvr-a": tracker})
    monkeypatch.setattr(
        main, "_dvr_health_notification_managers", {"dvr-a": manager}
    )
    monkeypatch.setattr(notification_drain, "unregister_notification_manager", unregister)

    main._reset_dvr_health_state("dvr-a")

    tracker.reset.assert_called_once_with()
    unregister.assert_called_once_with(manager)
    assert calls == [(False, 0.0)]
    assert main._dvr_health_trackers == {}
    assert main._dvr_health_notification_managers == {}
    # Repeating cleanup is intentionally harmless.
    main._close_health_notification_manager("dvr-a")


@pytest.mark.asyncio
async def test_health_transition_delivery_respects_global_event_and_manager_state(
    monkeypatch,
):
    from core import main

    activity = MagicMock()
    monkeypatch.setattr(main, "record_activity", activity)
    dvr = SimpleNamespace(id="dvr-a", name="Synthetic DVR", overrides={})
    transition = DvrHealthTransition("unreachable", "outage-1")

    settings = SimpleNamespace(
        alert_dvr_health=False,
        dvr_alert_unreachable=True,
        dvr_alert_recovered=True,
        global_rate_limit=20,
        global_rate_window=300,
    )
    manager_factory = MagicMock()
    monkeypatch.setattr(main, "_health_notification_manager_for", manager_factory)
    assert await main._emit_dvr_health_transition(
        dvr, settings, transition, test_mode=True
    ) is False
    manager_factory.assert_not_called()

    settings.alert_dvr_health = True
    settings.dvr_alert_unreachable = False
    assert await main._emit_dvr_health_transition(
        dvr, settings, transition, test_mode=True
    ) is False
    manager_factory.assert_not_called()

    settings.dvr_alert_unreachable = True
    manager_factory.return_value = None
    assert await main._emit_dvr_health_transition(
        dvr, settings, transition, test_mode=True
    ) is False

    manager_factory.return_value = object()
    assert await main._emit_dvr_health_transition(
        dvr, settings, transition, test_mode=True
    ) is False
    assert activity.call_count == 4


@pytest.mark.asyncio
async def test_health_evaluation_cleans_removed_dvr_and_arms_accepted_outage(
    monkeypatch,
):
    from core import main

    removed_tracker = MagicMock()
    current_tracker = MagicMock()
    current_tracker.evaluate.return_value = DvrHealthTransition(
        "unreachable", "outage-1"
    )
    dvr = SimpleNamespace(id="dvr-a", name="Synthetic DVR", overrides={})
    settings = SimpleNamespace(
        dvr_health_alert_delay_seconds=120,
        get_dvr_connections=lambda: [dvr],
    )
    emit = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main, "_dvr_health_trackers", {"removed": removed_tracker, "dvr-a": current_tracker}
    )
    monkeypatch.setattr(main, "_dvr_health_notification_managers", {})
    monkeypatch.setattr(main, "_monitor_is_healthy", lambda dvr_id: False)
    monkeypatch.setattr(main, "_health_tracker_for", lambda dvr_id: current_tracker)
    monkeypatch.setattr(main, "_emit_dvr_health_transition", emit)

    await main._evaluate_dvr_health(test_mode=True)

    removed_tracker.reset.assert_called_once_with()
    current_tracker.evaluate.assert_called_once_with(
        healthy=False, delay_seconds=120
    )
    emit.assert_awaited_once()
    current_tracker.set_notification_armed.assert_called_once_with(
        "outage-1", True
    )


@pytest.mark.asyncio
async def test_health_evaluation_isolates_tracker_and_arming_failures(monkeypatch):
    from core import main

    failing_tracker = MagicMock()
    failing_tracker.evaluate.side_effect = RuntimeError("state unavailable")
    arming_tracker = MagicMock()
    arming_tracker.evaluate.return_value = DvrHealthTransition(
        "unreachable", "outage-2"
    )
    arming_tracker.set_notification_armed.side_effect = RuntimeError("disk full")
    dvrs = [
        SimpleNamespace(id="dvr-a", overrides={}),
        SimpleNamespace(id="dvr-b", overrides={}),
    ]
    settings = SimpleNamespace(
        dvr_health_alert_delay_seconds=120,
        get_dvr_connections=lambda: dvrs,
    )
    trackers = {"dvr-a": failing_tracker, "dvr-b": arming_tracker}
    emit = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "_dvr_health_trackers", dict(trackers))
    monkeypatch.setattr(main, "_health_tracker_for", lambda dvr_id: trackers[dvr_id])
    monkeypatch.setattr(main, "_monitor_is_healthy", lambda dvr_id: False)
    monkeypatch.setattr(main, "_emit_dvr_health_transition", emit)

    await main._evaluate_dvr_health(test_mode=True)

    emit.assert_awaited_once()
    arming_tracker.set_notification_armed.assert_called_once_with(
        "outage-2", True
    )
