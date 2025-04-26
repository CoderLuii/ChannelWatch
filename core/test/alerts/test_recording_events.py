"""Tests the Recording-Events alert functionality with simulated recording lifecycle events."""
import time
import json
from typing import Dict, Any

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info
from ...alerts import get_alert_class
from ...notifications.notification import NotificationManager

# TEST DATA GENERATION

def test_recording_events_alert(host: str, port: int, alert_manager) -> bool:
    """Tests the Recording-Events alert by simulating various recording lifecycle events."""
    print_test_header("Recording-Events Alert Test")
    success = False
    
    try:
        if "Recording-Events" not in alert_manager.alert_instances:
            log("ERROR: Recording-Events alert not registered")
            print_test_footer(False)
            return False
            
        server_info = get_server_info(host, port)
        
        log("Creating test alerts for Recording Events...")
        
        test_alert = alert_manager.alert_instances["Recording-Events"]
        
        if hasattr(test_alert, '_cache_channels'):
            test_alert._cache_channels()
            
        channel_data = {
            "129": {
                "name": "MOVIE CHANNEL",
                "logo_url": "https://tmsimg.fancybits.co/assets/s56240_h3_aa.png?w=360&h=270"
            },
            "152": {
                "name": "SCI-FI CHANNEL",
                "logo_url": "https://tmsimg.fancybits.co/assets/s28717_h3_aa.png?w=360&h=270"
            },
            "137": {
                "name": "ACTION NETWORK",
                "logo_url": "https://tmsimg.fancybits.co/assets/s10003_h3_aa.png?w=360&h=270"
            }
        }
        
        if hasattr(test_alert, 'channel_provider'):
            for channel_number, data in channel_data.items():
                test_alert.channel_provider.channel_cache[channel_number] = data
        
        success = (
            test_recording_scheduled(test_alert) and
            test_recording_started(test_alert) and
            test_recording_completed(test_alert) and 
            test_recording_stopped(test_alert) and
            test_recording_cancelled(test_alert)
        )
        
        if success:
            log("✅ All recording event tests passed!")
            log("If you don't receive notifications, check your notification provider settings.")
        else:
            log("❌ Some recording event tests failed.")
            log("Check your notification provider configuration and logs for details.")
    
    except Exception as e:
        log(f"Error during test alert: {e}")
        success = False
        
    print_test_footer(success)
    return success

# EVENT SIMULATIONS

def test_recording_scheduled(alert):
    """Simulates a scheduled recording event with mock job data."""
    log("Testing SCHEDULED recording event...")
    
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
            "image_url": "https://tmsimg.fancybits.co/assets/p11724_v_v12_ab.jpg?w=480&h=720"
        }
    }
    
    if hasattr(alert, 'job_provider'):
        alert.job_provider._jobs_cache[job_id] = mock_job
    
    mock_event_data = {
        "Type": "recording.created",
        "Name": job_id,
        "Value": ""
    }
    
    try:
        result = alert._handle_recording_created(mock_event_data, mock_job)
        log(f"Scheduled recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in scheduled recording test: {e}")
        return False

def test_recording_started(alert):
    """Simulates a recording started event with mock job data."""
    log("Testing STARTED recording event...")
    
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
            "image_url": "https://tmsimg.fancybits.co/assets/p190667_v_v8_aq.jpg?w=480&h=720"
        }
    }
    
    if hasattr(alert, 'job_provider'):
        alert.job_provider._jobs_cache[job_id] = mock_job
    
    mock_event_data = {
        "Type": "recording.started",
        "Name": "",
        "Value": f"recording-{job_id}"
    }
    
    try:
        result = alert._handle_recording_started(mock_event_data, mock_job)
        log(f"Started recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in started recording test: {e}")
        return False

def test_recording_completed(alert):
    """Simulates a recording completed event with mock recording data."""
    log("Testing COMPLETED recording event...")
    
    file_id = f"test-file-completed-{int(time.time())}"
    job_id = f"test-job-completed-{int(time.time())}"
    
    mock_recording = {
        "id": file_id,
        "job_id": job_id,
        "title": "Pet Sematary (1989)",
        "channel": "129",
        "duration": 6840,
        "completed": True,
        "cancelled": False,
        "processed": True,
        "summary": "A doctor (Dale Midkiff) and his family move to a town near an ancient Indian burial ground.",
        "image_url": "https://tmsimg.fancybits.co/assets/p11589_v_v8_ai.jpg?w=480&h=720"
    }
    
    if hasattr(alert, 'job_provider') and hasattr(alert.job_provider, 'get_recording_by_id'):
        original_method = alert.job_provider.get_recording_by_id
        
        def mock_get_recording_by_id(file_id_param):
            if file_id_param == file_id:
                return mock_recording
            return original_method(file_id_param)
            
        alert.job_provider.get_recording_by_id = mock_get_recording_by_id
    
    mock_event_data = {
        "Type": "recording.complete",
        "Name": "",
        "Value": f"recorded-{file_id}"
    }
    
    try:
        result = alert._handle_recording_completed(mock_event_data, mock_recording)
        log(f"Completed recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in completed recording test: {e}")
        return False

def test_recording_stopped(alert):
    """Simulates a manually stopped recording event with mock recording data."""
    log("Testing STOPPED recording event...")
    
    file_id = f"test-file-stopped-{int(time.time())}"
    job_id = f"test-job-stopped-{int(time.time())}"
    
    mock_recording = {
        "id": file_id,
        "job_id": job_id,
        "title": "Pandorum (2009)",
        "channel": "152",
        "duration": 1200,
        "completed": True,
        "cancelled": True,
        "processed": True,
        "summary": "Astronauts awake to a terrifying reality aboard a seemingly abandoned spaceship.",
        "image_url": "https://tmsimg.fancybits.co/assets/p191800_v_v12_ap.jpg?w=480&h=720"
    }
    
    if hasattr(alert, 'job_provider') and hasattr(alert.job_provider, 'get_recording_by_id'):
        original_method = alert.job_provider.get_recording_by_id
        
        def mock_get_recording_by_id(file_id_param):
            if file_id_param == file_id:
                return mock_recording
            return original_method(file_id_param)
            
        alert.job_provider.get_recording_by_id = mock_get_recording_by_id
    
    mock_event_data = {
        "Type": "recording.complete",
        "Name": "",
        "Value": f"recorded-{file_id}"
    }
    
    try:
        result = alert._handle_recording_completed(mock_event_data, mock_recording)
        log(f"Stopped recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in stopped recording test: {e}")
        return False

def test_recording_cancelled(alert):
    """Simulates a cancelled recording event (deleted before start)."""
    log("Testing CANCELLED recording event...")
    
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
            "genres": ["Horror", "Comedy"],
            "original_air_date": "2024-01-27",
            "content_rating": "TV-PG"
        }
    }
    
    if hasattr(alert, 'scheduled_recordings'):
         alert.scheduled_recordings[job_id] = {"job": mock_job, "created_at": time.time() - 60}

    mock_event_data = {
        "Type": "recording.deleted",
        "Name": job_id,
        "Value": ""
    }
    
    try:
        if hasattr(alert, '_handle_recording_deleted'):
             result = alert._handle_recording_deleted(mock_event_data, mock_job) 
             log(f"Cancelled recording result: {result}")
             return True
        else:
             log("ERROR: _handle_recording_deleted method not found on alert object")
             return False
    except Exception as e:
        log(f"Error in cancelled recording test: {e}")
        return False 