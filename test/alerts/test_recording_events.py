"""
Tests for Recording-Events alerts.
"""
import time
import json
from typing import Dict, Any

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info
from ...alerts import get_alert_class
from ...notifications.notification import NotificationManager

def test_recording_events_alert(host: str, port: int, alert_manager) -> bool:
    """
    Test Recording-Events alert by sending mock events for different recording states.
    
    Args:
        host: The server host
        port: The server port
        alert_manager: Alert manager instance
        
    Returns:
        bool: True if test successful, False otherwise
    """
    print_test_header("Recording-Events Alert Test")
    success = False
    
    try:
        # Check if Recording-Events alert is registered
        if "Recording-Events" not in alert_manager.alert_instances:
            log("ERROR: Recording-Events alert not registered")
            print_test_footer(False)
            return False
            
        # Get server info for realistic data
        server_info = get_server_info(host, port)
        
        log("Creating test alerts for Recording Events...")
        
        # Get the alert instance
        test_alert = alert_manager.alert_instances["Recording-Events"]
        
        # Make sure test alert has channel info
        if hasattr(test_alert, '_cache_channels'):
            test_alert._cache_channels()
            
        # Create mock channel data
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
        
        # Add channel data to the provider if it exists
        if hasattr(test_alert, 'channel_provider'):
            for channel_number, data in channel_data.items():
                test_alert.channel_provider.channel_cache[channel_number] = data
        
        # Test all types of recording events
        success = (
            test_recording_scheduled(test_alert) and
            test_recording_started(test_alert) and
            test_recording_completed(test_alert) and 
            test_recording_stopped(test_alert) and
            test_recording_scheduled_second(test_alert)  # Add second scheduled test
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

def test_recording_scheduled(alert):
    """Test a scheduled recording event."""
    log("Testing SCHEDULED recording event...")
    
    # Create a unique job ID
    job_id = f"test-job-scheduled-{int(time.time())}"
    
    # Create mock job data based on real movie
    mock_job = {
        "id": job_id,
        "name": "Batman (1989)",
        "start_time": time.time() + 300,  # 5 minutes in the future
        "duration": 8160,  # 136 minutes (2h16m)
        "channels": ["137"],
        "summary": "Caped Crusader (Michael Keaton) saves Gotham City from the Joker (Jack Nicholson).",
        "item": {
            "summary": "Caped Crusader (Michael Keaton) saves Gotham City from the Joker (Jack Nicholson).",
            "image_url": "https://tmsimg.fancybits.co/assets/p11724_v_v12_ab.jpg?w=480&h=720"
        }
    }
    
    # Add job to provider's cache
    if hasattr(alert, 'job_provider'):
        alert.job_provider._jobs_cache[job_id] = mock_job
    
    # Create mock event data for scheduled recording
    mock_event_data = {
        "Type": "recording.created",
        "Name": job_id,
        "Value": ""
    }
    
    try:
        # Process the mock event
        result = alert._handle_recording_created(mock_event_data)
        log(f"Scheduled recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in scheduled recording test: {e}")
        return False

def test_recording_started(alert):
    """Test a recording started event."""
    log("Testing STARTED recording event...")
    
    # Create a unique job ID
    job_id = f"test-job-started-{int(time.time())}"
    
    # Create mock job data for a manual recording
    mock_job = {
        "id": job_id,
        "name": "Crank: High Voltage (2009)",
        "start_time": time.time() - 60,  # Started 1 minute ago
        "duration": 6132,  # 102 minutes
        "channels": ["129"],
        "summary": "Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.",
        "item": {
            "summary": "Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.",
            "image_url": "https://tmsimg.fancybits.co/assets/p190667_v_v8_aq.jpg?w=480&h=720"
        }
    }
    
    # Add job to provider's cache
    if hasattr(alert, 'job_provider'):
        alert.job_provider._jobs_cache[job_id] = mock_job
    
    # Create mock event data for started recording
    mock_event_data = {
        "Type": "recording.started",
        "Name": "",
        "Value": f"recording-{job_id}"
    }
    
    try:
        # Process the mock event
        result = alert._handle_recording_started(mock_event_data)
        log(f"Started recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in started recording test: {e}")
        return False

def test_recording_completed(alert):
    """Test a recording completed event."""
    log("Testing COMPLETED recording event...")
    
    # Create a unique file ID
    file_id = f"test-file-completed-{int(time.time())}"
    job_id = f"test-job-completed-{int(time.time())}"
    
    # Create mock recording data based on real movie
    mock_recording = {
        "id": file_id,
        "job_id": job_id,
        "title": "Pet Sematary (1989)",
        "channel": "129",
        "duration": 6840,  # 114 minutes
        "completed": True,
        "cancelled": False,
        "processed": True,
        "summary": "A doctor (Dale Midkiff) and his family move to a town near an ancient Indian burial ground.",
        "image_url": "https://tmsimg.fancybits.co/assets/p11589_v_v8_ai.jpg?w=480&h=720"
    }
    
    # Set up the job provider to return this recording
    if hasattr(alert, 'job_provider') and hasattr(alert.job_provider, 'get_recording_by_id'):
        # Monkeypatch the get_recording_by_id method to return our mock data
        original_method = alert.job_provider.get_recording_by_id
        
        def mock_get_recording_by_id(file_id_param):
            if file_id_param == file_id:
                return mock_recording
            return original_method(file_id_param)
            
        alert.job_provider.get_recording_by_id = mock_get_recording_by_id
    
    # Create mock event data for completed recording
    mock_event_data = {
        "Type": "recording.complete",
        "Name": "",
        "Value": f"recorded-{file_id}"
    }
    
    try:
        # Process the mock event
        result = alert._handle_recording_completed(mock_event_data)
        log(f"Completed recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in completed recording test: {e}")
        return False

def test_recording_stopped(alert):
    """Test a recording manually stopped event."""
    log("Testing STOPPED recording event...")
    
    # Create a unique file ID for a manually stopped recording
    file_id = f"test-file-stopped-{int(time.time())}"
    job_id = f"test-job-stopped-{int(time.time())}"
    
    # Create mock recording data for manually stopped recording
    mock_recording = {
        "id": file_id,
        "job_id": job_id,
        "title": "Pandorum (2009)",
        "channel": "152",
        "duration": 1200,  # 20 minutes - partial recording
        "completed": True,
        "cancelled": True,  # Both completed and cancelled = manually stopped
        "processed": True,
        "summary": "Astronauts awake to a terrifying reality aboard a seemingly abandoned spaceship.",
        "image_url": "https://tmsimg.fancybits.co/assets/p191800_v_v12_ap.jpg?w=480&h=720"
    }
    
    # Set up the job provider to return this recording
    if hasattr(alert, 'job_provider') and hasattr(alert.job_provider, 'get_recording_by_id'):
        # Monkeypatch the get_recording_by_id method to return our mock data
        original_method = alert.job_provider.get_recording_by_id
        
        def mock_get_recording_by_id(file_id_param):
            if file_id_param == file_id:
                return mock_recording
            return original_method(file_id_param)
            
        alert.job_provider.get_recording_by_id = mock_get_recording_by_id
    
    # Create mock event data for stopped recording
    mock_event_data = {
        "Type": "recording.complete",
        "Name": "",
        "Value": f"recorded-{file_id}"
    }
    
    try:
        # Process the mock event
        result = alert._handle_recording_completed(mock_event_data)
        log(f"Stopped recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in stopped recording test: {e}")
        return False

def test_recording_scheduled_second(alert):
    """Test a second scheduled recording event."""
    log("Testing second SCHEDULED recording event...")
    
    # Create a unique job ID
    job_id = f"test-job-scheduled2-{int(time.time())}"
    
    # Create mock job data for second scheduled recording
    mock_job = {
        "id": job_id,
        "name": "The Terminator (1984)",
        "start_time": time.time() + 600,  # 10 minutes in the future
        "duration": 6480,  # 108 minutes
        "channels": ["152"],
        "summary": "A cyborg (Arnold Schwarzenegger) from the future arrives in 1984 Los Angeles to kill a woman (Linda Hamilton) whose unborn son will lead a rebellion.",
        "item": {
            "summary": "A cyborg (Arnold Schwarzenegger) from the future arrives in 1984 Los Angeles to kill a woman (Linda Hamilton) whose unborn son will lead a rebellion.",
            "image_url": "https://tmsimg.fancybits.co/assets/p8851_v_v8_ab.jpg?w=480&h=720"
        }
    }
    
    # Add job to provider's cache
    if hasattr(alert, 'job_provider'):
        alert.job_provider._jobs_cache[job_id] = mock_job
    
    # Create mock event data for scheduled recording
    mock_event_data = {
        "Type": "recording.created",
        "Name": job_id,
        "Value": ""
    }
    
    try:
        # Process the mock event
        result = alert._handle_recording_created(mock_event_data)
        log(f"Second scheduled recording result: {result}")
        return True
    except Exception as e:
        log(f"Error in second scheduled recording test: {e}")
        return False 