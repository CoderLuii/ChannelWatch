"""Runs Recording-Events diagnostics with simulated recording lifecycle events."""

import time

from ...helpers.logging import log
from ..output import print_test_header, print_result

# ALERT TESTING


def _setup_recording_test(alert_manager):
    """Common setup for recording event tests."""
    if "Recording-Events" not in alert_manager.alert_instances:
        return None, False
    test_alert = alert_manager.alert_instances["Recording-Events"]
    if hasattr(test_alert, "_cache_channels"):
        test_alert._cache_channels()
    channel_data = {
        "129": {
            "name": "MOVIE CHANNEL",
            "logo_url": "https://tmsimg.fancybits.co/assets/s56240_h3_aa.png?w=360&h=270",
        },
        "152": {
            "name": "SCI-FI CHANNEL",
            "logo_url": "https://tmsimg.fancybits.co/assets/s28717_h3_aa.png?w=360&h=270",
        },
        "137": {
            "name": "ACTION NETWORK",
            "logo_url": "https://tmsimg.fancybits.co/assets/s10003_h3_aa.png?w=360&h=270",
        },
    }
    if hasattr(test_alert, "channel_provider"):
        for channel_number, data in channel_data.items():
            test_alert.channel_provider.channel_cache[channel_number] = data
    has_providers = bool(
        hasattr(alert_manager, "notification_manager")
        and alert_manager.notification_manager
        and alert_manager.notification_manager.get_active_providers()
    )
    return test_alert, has_providers


def _run_single_recording_test(name, host, port, alert_manager, test_fn):
    """Run a single recording event test with standardized output."""
    print_test_header(f"Recording {name} Test")
    try:
        test_alert, has_providers = _setup_recording_test(alert_manager)
        if not test_alert:
            print_result(False, "Alert not registered")
            return False
        log(f"Target: {host}:{port}")
        log(f"Processing {name.lower()} event...")
        test_fn(test_alert)
        if has_providers:
            print_result(True, f"{name} event processed, notification dispatched")
            return True
        else:
            print_result(
                False,
                f"{name} event processed but no notification providers configured",
            )
            return False
    except Exception as e:
        print_result(False, f"Exception: {e}")
        return False


def test_recording_scheduled_alert(host, port, alert_manager):
    return _run_single_recording_test(
        "Scheduled", host, port, alert_manager, test_recording_scheduled
    )


def test_recording_started_alert(host, port, alert_manager):
    return _run_single_recording_test(
        "Started", host, port, alert_manager, test_recording_started
    )


def test_recording_completed_alert(host, port, alert_manager):
    return _run_single_recording_test(
        "Completed", host, port, alert_manager, test_recording_completed
    )


def test_recording_stopped_alert(host, port, alert_manager):
    return _run_single_recording_test(
        "Stopped", host, port, alert_manager, test_recording_stopped
    )


def test_recording_cancelled_alert(host, port, alert_manager):
    return _run_single_recording_test(
        "Cancelled", host, port, alert_manager, test_recording_cancelled
    )


def test_recording_events_alert(host: str, port: int, alert_manager) -> bool:
    """Runs all 5 recording event tests."""
    results = [
        test_recording_scheduled_alert(host, port, alert_manager),
        test_recording_started_alert(host, port, alert_manager),
        test_recording_completed_alert(host, port, alert_manager),
        test_recording_stopped_alert(host, port, alert_manager),
        test_recording_cancelled_alert(host, port, alert_manager),
    ]
    return all(results)


# EVENT SIMULATIONS


def test_recording_scheduled(alert) -> bool:
    """Simulates a scheduled recording event."""
    job_id = f"test-job-scheduled-{int(time.time())}"
    mock_job = {
        "id": job_id,
        "name": "Batman (1989)",
        "start_time": time.time() + 300,
        "duration": 8160,
        "channels": ["137"],
        "summary": "Caped Crusader (Michael Keaton) saves Gotham City from the Joker (Jack Nicholson).",
        "item": {
            "summary": "Caped Crusader (Michael Keaton) saves Gotham City from the Joker (Jack Nicholson).",
            "image_url": "https://tmsimg.fancybits.co/assets/p11724_v_v12_ab.jpg?w=480&h=720",
        },
    }
    if hasattr(alert, "job_provider"):
        alert.job_provider._jobs_cache[job_id] = mock_job

    mock_event_data = {"Type": "recording.created", "Name": job_id, "Value": ""}
    result = alert._handle_recording_created(mock_event_data, mock_job)
    return bool(result)


def test_recording_started(alert) -> bool:
    """Simulates a recording started event."""
    job_id = f"test-job-started-{int(time.time())}"
    mock_job = {
        "id": job_id,
        "name": "Crank: High Voltage (2009)",
        "start_time": time.time() - 60,
        "duration": 6132,
        "channels": ["129"],
        "summary": "Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.",
        "item": {
            "summary": "Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.",
            "image_url": "https://tmsimg.fancybits.co/assets/p190667_v_v8_aq.jpg?w=480&h=720",
        },
    }
    if hasattr(alert, "job_provider"):
        alert.job_provider._jobs_cache[job_id] = mock_job

    mock_event_data = {
        "Type": "recording.started",
        "Name": "",
        "Value": f"recording-{job_id}",
    }
    result = alert._handle_recording_started(mock_event_data, mock_job)
    return bool(result)


def test_recording_completed(alert) -> bool:
    """Simulates a recording completed event."""
    file_id = f"test-file-completed-{int(time.time())}"
    mock_recording = {
        "id": file_id,
        "job_id": f"test-job-completed-{int(time.time())}",
        "title": "Pet Sematary (1989)",
        "channel": "129",
        "duration": 6840,
        "completed": True,
        "cancelled": False,
        "processed": True,
        "summary": "A doctor (Dale Midkiff) and his family move to a town near an ancient Indian burial ground.",
        "image_url": "https://tmsimg.fancybits.co/assets/p11589_v_v8_ai.jpg?w=480&h=720",
    }
    if hasattr(alert, "job_provider") and hasattr(
        alert.job_provider, "get_recording_by_id"
    ):
        original = alert.job_provider.get_recording_by_id
        alert.job_provider.get_recording_by_id = lambda fid: (
            mock_recording if fid == file_id else original(fid)
        )

    mock_event_data = {
        "Type": "recording.complete",
        "Name": "",
        "Value": f"recorded-{file_id}",
    }
    result = alert._handle_recording_completed(mock_event_data, mock_recording)
    return bool(result)


def test_recording_stopped(alert) -> bool:
    """Simulates a manually stopped recording event."""
    file_id = f"test-file-stopped-{int(time.time())}"
    mock_recording = {
        "id": file_id,
        "job_id": f"test-job-stopped-{int(time.time())}",
        "title": "Pandorum (2009)",
        "channel": "152",
        "duration": 1200,
        "completed": True,
        "cancelled": True,
        "processed": True,
        "summary": "Astronauts awake to a terrifying reality aboard a seemingly abandoned spaceship.",
        "image_url": "https://tmsimg.fancybits.co/assets/p191800_v_v12_ap.jpg?w=480&h=720",
    }
    if hasattr(alert, "job_provider") and hasattr(
        alert.job_provider, "get_recording_by_id"
    ):
        original = alert.job_provider.get_recording_by_id
        alert.job_provider.get_recording_by_id = lambda fid: (
            mock_recording if fid == file_id else original(fid)
        )

    mock_event_data = {
        "Type": "recording.complete",
        "Name": "",
        "Value": f"recorded-{file_id}",
    }
    result = alert._handle_recording_completed(mock_event_data, mock_recording)
    return bool(result)


def test_recording_cancelled(alert) -> bool:
    """Simulates a cancelled recording event."""
    job_id = f"test-job-cancelled-{int(time.time())}"
    mock_job = {
        "id": job_id,
        "name": "Svengoolie",
        "start_time": time.time() + 1200,
        "duration": 7200,
        "channels": ["137"],
        "summary": "A social misfit uses his only friends, his pet rats, to exact revenge on his tormentors.",
        "item": {
            "title": "Svengoolie",
            "episode_title": "Willard",
            "summary": "A social misfit uses his only friends, his pet rats, to exact revenge on his tormentors.",
            "image_url": "https://tmsimg.fancybits.co/assets/p926594_b_h9_ac.jpg?w=720&h=540",
        },
    }
    if hasattr(alert, "scheduled_recordings"):
        alert.scheduled_recordings[job_id] = {
            "job": mock_job,
            "created_at": time.time() - 60,
        }

    mock_event_data = {"Type": "recording.deleted", "Name": job_id, "Value": ""}

    if not hasattr(alert, "_handle_recording_deleted"):
        return False

    result = alert._handle_recording_deleted(mock_event_data, mock_job)
    return bool(result)
