# IMPORTS
import os
import sys 
from fastapi import FastAPI, HTTPException, Response, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import asyncio
from pydantic import BaseModel
from .config import load_settings, save_settings
from .schemas import AppSettings
import uuid
import json
from datetime import datetime, timedelta
import xmlrpc.client
import requests
from typing import Optional, List, Dict, Any
import time
import threading
import re
import logging

# LOGGING SETUP
log = logging.getLogger(__name__)
from uvicorn.logging import AccessFormatter

class StaticFileFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args and isinstance(record.args, (tuple, list)) and len(record.args) >= 3:
                arg_to_check = record.args[1]
                if isinstance(arg_to_check, str):
                    request_path: str = arg_to_check
                    if request_path.startswith('/_next/static/'):
                        return False
        except (IndexError, TypeError):
            pass
        return True

access_logger = logging.getLogger("uvicorn.access")
access_logger.addFilter(StaticFileFilter())

# CORE APP INTEGRATION
try:
    log.debug(f"Attempting imports from webui/main.py (PYTHONPATH=/app)")
    from core import __version__, __app_name__
    from core.helpers.config import get_settings as _get_core_settings_sync
    
    async def get_core_settings() -> Optional[Any]:
        try:
            return _get_core_settings_sync()
        except TypeError:
            return None 

    log.debug("Imported core.helpers.config")
    from core.helpers.initialize import initialize_notifications, initialize_alerts
    log.debug("Imported core.helpers.initialize")
    from core.test import run_test 
    log.debug("Imported core.test")
    CORE_APP_AVAILABLE = True
    log.info("Core app components loaded successfully for testing.")
except ImportError as e:
    log.error(f"Specific ImportError: {e}")
    log.warning(f"Could not import core app components for testing: {e}. Test endpoints will be disabled.")
    CORE_APP_AVAILABLE = False
    async def get_core_settings() -> Optional[Any]: 
        return None 
    def initialize_notifications(settings: Any, test_mode: bool) -> Optional[Any]: 
        return None 
    def initialize_alerts(notification_manager: Any, settings: Any, test_mode: bool) -> Optional[Any]: 
        return None 
    def run_test(test_name: str, host: str, port: int, alert_manager: Optional[Any], duration: int = 30) -> bool: 
        return False 
    __version__ = "N/A"
    __app_name__ = "ChannelWatch"

# APP INITIALIZATION
app = FastAPI(title="ChannelWatch UI Backend")

WEBUI_DIR = Path(__file__).resolve().parent 
STATIC_UI_DIR = WEBUI_DIR / "static_ui"

# BASIC ENDPOINTS
@app.get("/api/ping")
async def ping():
    return {"status": "ok"}

@app.get("/api/settings", response_model=AppSettings)
async def get_settings_endpoint():
    """Retrieve the current application settings."""
    return load_settings()

@app.post("/api/settings")
async def update_settings_endpoint(settings: AppSettings):
    """Update and save the application settings."""
    try:
        save_settings(settings)
        return {"message": "Settings saved successfully"}
    except Exception as e:
        print(f"[WebUI API] ERROR: Failed saving settings: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"An unexpected error occurred while saving settings: {str(e)}"
        )

# INFORMATION MODELS
class AboutInfo(BaseModel):
    app_name: str
    version: str
    developer: str
    description: str
    github_url: str
    dockerhub_url: str

@app.get("/api/about", response_model=AboutInfo, tags=["Information"])
async def get_about_info():
    """Returns information about the ChannelWatch application."""
    return AboutInfo(
        app_name=__app_name__,
        version=__version__,
        developer="CoderLuii",
        description="Channels DVR monitoring tool for real-time notifications.",
        github_url="https://github.com/CoderLuii/ChannelWatch",
        dockerhub_url="https://hub.docker.com/r/coderluii/channelwatch"
    )

# ACTIVITY TRACKING
APP_START_TIME = datetime.now()
CORE_LAST_START_TIME = APP_START_TIME
log.debug(f"[WebUI] Application started at {APP_START_TIME.isoformat()}")

class AlertHistoryItem(BaseModel):
    id: str = ""
    type: str
    title: str
    message: str
    timestamp: str
    icon: str = "bell"

ACTIVITY_HISTORY: List[AlertHistoryItem] = []

CONFIG_DIR = Path("/config")
HISTORY_FILE = CONFIG_DIR / "activity_history.json"

LAST_MODIFIED_TIME = 0

def load_alert_history():
    global ACTIVITY_HISTORY, LAST_MODIFIED_TIME
    
    ACTIVITY_HISTORY = []
    
    if os.path.exists(HISTORY_FILE):
        try:
            log.debug(f"[WebUI] Loading activity history from {HISTORY_FILE}")
            with open(HISTORY_FILE, 'r') as f:
                items = json.load(f)
                
            for item_data in items:
                try:
                    item = AlertHistoryItem(**item_data)
                    ACTIVITY_HISTORY.append(item)
                except Exception as e:
                    print(f"[WebUI] Error loading activity item: {e}")
                    
            log.debug(f"[WebUI] Loaded {len(ACTIVITY_HISTORY)} activity items from history file")
            
            LAST_MODIFIED_TIME = os.path.getmtime(HISTORY_FILE)
            
        except json.JSONDecodeError as e:
            print(f"[WebUI] Error parsing history file: {e}")
            with open(HISTORY_FILE, 'w') as f:
                json.dump([], f)
        except Exception as e:
            print(f"[WebUI] Error loading history file: {e}")
    else:
        try:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(HISTORY_FILE, 'w') as f:
                json.dump([], f)
            
            LAST_MODIFIED_TIME = os.path.getmtime(HISTORY_FILE)
            
        except Exception as e:
            print(f"[WebUI] Error creating activity history file: {e}")

    return False

def check_history_file_changes():
    """Check if the activity history file has been modified and reload if needed."""
    global LAST_MODIFIED_TIME
    
    try:
        if os.path.exists(HISTORY_FILE):
            current_mtime = os.path.getmtime(HISTORY_FILE)
            
            if current_mtime > LAST_MODIFIED_TIME:
                load_alert_history()
                return True
    except Exception as e:
        print(f"[WebUI] Error checking history file changes: {e}")
    
    return False

load_alert_history()

def history_file_watcher():
    """Thread that monitors the activity history file for changes."""
    
    while True:
        try:
            check_history_file_changes()
            
            time.sleep(2)
        except Exception as e:
            print(f"[WebUI] Error in history file watcher: {e}")
            time.sleep(5)

history_file_watcher_thread = threading.Thread(target=history_file_watcher, daemon=True)

# SYSTEM INFO
class SystemInfo(BaseModel):
    channelwatch_version: str
    channels_dvr_host: Optional[str]
    channels_dvr_port: int
    channels_dvr_server_version: Optional[str] = None
    timezone: str
    disk_usage_percent: Optional[float] = None
    disk_usage_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    log_retention_days: Optional[int] = None
    start_time: Optional[str] = None
    uptime_data: Dict[str, int] = {}
    core_status: str = "Unknown"

@app.get("/api/system-info", response_model=SystemInfo, tags=["Information"])
async def get_system_info():
    """Returns system information including versions and connection details."""
    global CORE_LAST_START_TIME
    
    if CORE_APP_AVAILABLE:
        from core.helpers.config import CoreSettings
        settings = CoreSettings()
    else:
        settings = load_settings()
    
    disk_usage_percent = None
    disk_usage_gb = None
    disk_total_gb = None
    disk_free_gb = None
    dvr_version = None
    
    if settings and settings.channels_dvr_host:
        try:
            status_response = requests.get(
                f"http://{settings.channels_dvr_host}:{settings.channels_dvr_port}/status", 
                timeout=3
            )
            if status_response.status_code == 200:
                data = status_response.json()
                dvr_version = data.get("version", None)
                
            storage_response = requests.get(
                f"http://{settings.channels_dvr_host}:{settings.channels_dvr_port}/dvr", 
                timeout=3
            )
            if storage_response.status_code == 200:
                storage_data = storage_response.json()
                
                if "ServerStorage" in storage_data:
                    storage_info = storage_data["ServerStorage"]
                    bytes_to_gb = lambda bytes_val: round(bytes_val / 1073741824, 2)
                    bytes_to_tb = lambda bytes_val: round(bytes_val / 1099511627776, 2)
                    if "Available" in storage_info and "Total" in storage_info:
                        available_bytes = storage_info["Available"]
                        total_bytes = storage_info["Total"]
                        used_bytes = total_bytes - available_bytes
                        disk_total_gb = bytes_to_gb(total_bytes)
                        disk_free_gb = bytes_to_gb(available_bytes)
                        disk_usage_gb = bytes_to_gb(used_bytes)
                elif "disk" in storage_data:
                    try:
                        disk_info = storage_data["disk"]
                        if "free" in disk_info and "total" in disk_info:
                            free_bytes = disk_info.get("free", 0)
                            total_bytes = disk_info.get("total", 0)
                            
                            if isinstance(free_bytes, (int, float)):
                                disk_free_gb = round(free_bytes / 1073741824, 2)
                            elif isinstance(free_bytes, str) and "GB" in free_bytes:
                                disk_free_gb = float(free_bytes.replace("GB", "").strip())
                            
                            if isinstance(total_bytes, (int, float)):
                                disk_total_gb = round(total_bytes / 1073741824, 2)
                            elif isinstance(total_bytes, str) and "TB" in total_bytes:
                                tb_value = float(total_bytes.replace("TB", "").strip())
                                disk_total_gb = tb_value * 1024
                            elif isinstance(total_bytes, str) and "GB" in total_bytes:
                                disk_total_gb = float(total_bytes.replace("GB", "").strip())
                            
                            if disk_free_gb is not None and disk_total_gb is not None:
                                disk_usage_gb = disk_total_gb - disk_free_gb
                                disk_usage_percent = round((disk_usage_gb / disk_total_gb) * 100)
                    except Exception as e:
                        pass
                else:
                    pass
            else:
                pass
        except Exception as e:
            print(f"[WebUI API] ERROR: Failed to fetch DVR server information: {e}")
    
    core_status = "Unknown"
    actual_core_start_time = None
    try:
        proxy = get_supervisor_proxy()
        if proxy:
            process_info = proxy.supervisor.getProcessInfo('core')
            core_status = process_info.get('statename', 'Unknown').capitalize()
            start_timestamp = process_info.get('start', 0)
            if start_timestamp > 0:
                actual_core_start_time = datetime.fromtimestamp(start_timestamp)
                if CORE_LAST_START_TIME is None or actual_core_start_time > CORE_LAST_START_TIME:
                    CORE_LAST_START_TIME = actual_core_start_time
            else:
                if core_status not in ('Running', 'Starting'):
                    CORE_LAST_START_TIME = APP_START_TIME
        else:
            print("[WebUI API] WARNING: Could not connect to supervisord to get core status.")
            core_status = "Error"
    except Exception as e:
        print(f"[WebUI API] ERROR: Failed to get core status from supervisord: {e}")
        core_status = "Error"
    
    current_time = datetime.now()
    start_time_to_use = CORE_LAST_START_TIME if CORE_LAST_START_TIME else APP_START_TIME
    uptime_seconds = int((current_time - start_time_to_use).total_seconds())
    
    uptime_days = uptime_seconds // (24 * 3600)
    uptime_seconds %= (24 * 3600)
    uptime_hours = uptime_seconds // 3600
    uptime_seconds %= 3600
    uptime_minutes = uptime_seconds // 60
    uptime_seconds %= 60
    
    uptime_data = {
        "days": uptime_days,
        "hours": uptime_hours,
        "minutes": uptime_minutes,
        "seconds": uptime_seconds
    }

    return SystemInfo(
        channelwatch_version=__version__,
        channels_dvr_host=settings.channels_dvr_host if settings else None,
        channels_dvr_port=settings.channels_dvr_port if settings else 8089,
        channels_dvr_server_version=dvr_version,
        timezone=settings.tz if settings else "America/Los_Angeles",
        disk_usage_percent=disk_usage_percent,
        disk_usage_gb=disk_usage_gb,
        disk_total_gb=disk_total_gb, 
        disk_free_gb=disk_free_gb,
        log_retention_days=settings.log_retention_days if settings else 7,
        start_time=start_time_to_use.isoformat(),
        uptime_data=uptime_data,
        core_status=core_status
    )

from typing import List, Dict, Any
from datetime import datetime

class RecordingInfo(BaseModel):
    id: str
    title: str
    start_time: int
    channel: str
    scheduled_time: str

@app.get("/api/recordings/upcoming", response_model=List[RecordingInfo], tags=["DVR"])
async def get_upcoming_recordings(limit: int = 250):
    """Returns information about upcoming scheduled recordings from Channels DVR."""
    upcoming_recordings = []
    if CORE_APP_AVAILABLE:
        from core.helpers.config import CoreSettings
        settings = CoreSettings()
    else:
        settings = load_settings()
    
    if settings and settings.channels_dvr_host:
        try:
            channel_map = {}
            try:
                channel_url = f"http://{settings.channels_dvr_host}:{settings.channels_dvr_port}/api/v1/channels"
                channel_response = requests.get(channel_url, timeout=5)
                
                if channel_response.status_code == 200:
                    channels_data = channel_response.json()
                    for channel in channels_data:
                        channel_number = None
                        if 'number' in channel:
                            channel_number = str(channel['number'])
                        channel_name = channel.get('name', 'Unknown Channel')
                        if channel_number:
                            channel_map[channel_number] = channel_name
                        if 'id' in channel:
                            channel_id = str(channel['id'])
                            if channel_id:
                                channel_map[channel_id] = channel_name
                else:
                    pass
            except Exception as e:
                pass
            
            url = f"http://{settings.channels_dvr_host}:{settings.channels_dvr_port}/dvr/jobs"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                recordings_data = response.json()
                current_time = int(datetime.now().timestamp())
                
                future_count = 0
                for recording in recordings_data:
                    start_time = recording.get('Time', 0)
                    if start_time > current_time:
                        future_count += 1
                        title = recording.get('Name', 'Untitled Recording')
                        channel_name = "Unknown Channel"
                        channel_number = None
                        
                        if 'Channels' in recording and recording['Channels'] and len(recording['Channels']) > 0:
                            channel_value = recording['Channels'][0]
                            if channel_value is not None:
                                channel_number = str(channel_value)
                        elif 'Channel' in recording and recording['Channel']:
                            channel_value = recording['Channel']
                            if channel_value is not None:
                                channel_number = str(channel_value)
                        elif 'Airing' in recording and isinstance(recording['Airing'], dict) and 'Channel' in recording['Airing']:
                            channel_value = recording['Airing']['Channel']
                            if channel_value is not None:
                                channel_number = str(channel_value)
                        
                        if channel_number in channel_map:
                            channel_name = channel_map[channel_number]
                        else:
                            if channel_number:
                                channel_name = f"Channel {channel_number}"
                        
                        recording_datetime = datetime.fromtimestamp(start_time)
                        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                        tomorrow = today + timedelta(days=1)
                        
                        if recording_datetime.date() == today.date():
                            date_prefix = "Today"
                        elif recording_datetime.date() == tomorrow.date():
                            date_prefix = "Tomorrow"
                        else:
                            date_prefix = recording_datetime.strftime("%b %d")
                        
                        time_str = recording_datetime.strftime("%I:%M %p")
                        scheduled_time = f"{date_prefix} at {time_str}"
                        
                        upcoming_recordings.append(RecordingInfo(
                            id=recording.get('ID', ''),
                            title=title,
                            start_time=start_time,
                            channel=channel_name,
                            scheduled_time=scheduled_time
                        ))
                
                upcoming_recordings.sort(key=lambda x: x.start_time)
            else:
                pass
        except Exception as e:
            pass
    
    return upcoming_recordings[:limit]

@app.get("/api/recordings/active", response_model=int, tags=["DVR"])
async def get_active_recordings_count():
    """Returns the count of currently active recordings."""
    active_count = 0
    if CORE_APP_AVAILABLE:
        from core.helpers.config import CoreSettings
        settings = CoreSettings()
    else:
        settings = load_settings()
    
    if settings and settings.channels_dvr_host:
        try:
            url = f"http://{settings.channels_dvr_host}:{settings.channels_dvr_port}/dvr/jobs"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                recordings_data = response.json()
                current_time = int(datetime.now().timestamp())
                for recording in recordings_data:
                    start_time = recording.get('start_time', 0)
                    stop_time = recording.get('stop_time', 0)
                    if start_time <= current_time and stop_time > current_time:
                        active_count += 1
            else:
                pass
        except Exception as e:
            pass
    
    return active_count

STREAM_COUNT_FILE = "/config/stream_count.txt"
@app.get("/api/streams/active", response_model=int, tags=["DVR"])
async def get_active_streams_count():
    """Returns the total count of active streams (viewing + recording)."""
    stream_count = 0
    
    try:
        if os.path.exists(STREAM_COUNT_FILE):
            with open(STREAM_COUNT_FILE, 'r') as f:
                content = f.read().strip()
                if content.isdigit():
                    stream_count = int(content)
                else:
                    pass
        else:
            pass
    except Exception as e:
        pass

    return stream_count

# TESTING
class TestResult(BaseModel):
    test_name: str
    success: bool
    message: str

async def run_test_background(test_name: str) -> TestResult:
    """Helper function to run a test in the background."""
    message = ""
    success = False
    if not CORE_APP_AVAILABLE:
        return TestResult(test_name=test_name, success=False, message="Core app components not available for testing.")

    try:
        settings = await get_core_settings()

        if settings is None:
            raise ValueError("Failed to load core settings for test.")

        host = settings.channels_dvr_host
        port = settings.channels_dvr_port
        if not host:
            raise ValueError("DVR Host not configured in settings.")

        if test_name == "Test Connectivity":
            test_key = 'connectivity'
            alert_mgr_needed = False
        elif test_name == "Test API Endpoints":
            test_key = 'api'
            alert_mgr_needed = False
        elif test_name == "Test Channel Watching Alert":
            test_key = 'Channel-Watching'
            alert_mgr_needed = True
        elif test_name == "Test VOD Watching Alert":
            test_key = 'VOD-Watching'
            alert_mgr_needed = True
        elif test_name == "Test Disk Space Alert":
            test_key = 'Disk-Space'
            alert_mgr_needed = True
        elif test_name == "Test Recording Events Alert":
            test_key = 'Recording-Events'
            alert_mgr_needed = True
        else:
            raise ValueError(f"Unknown test name received: {test_name}")

        from core.test import run_test
            
        if alert_mgr_needed:
            from core.helpers.config import get_settings
            from core.helpers.initialize import initialize_notifications, initialize_alerts

            notification_manager = initialize_notifications(settings, test_mode=True)
            if not notification_manager:
                return TestResult(test_name=test_name, success=False, message="Failed to initialize notification system")
                
            alert_manager = initialize_alerts(notification_manager, settings, test_mode=True)
            if not alert_manager:
                return TestResult(test_name=test_name, success=False, message="Failed to initialize alert manager")
                
            if 'Disk-Space' in alert_manager.alert_instances:
                disk_alert = alert_manager.alert_instances['Disk-Space']
                
                if test_key != 'Disk-Space':
                    disk_alert.running = False
                else:
                    pass
            
            success = run_test(test_key, host, port, alert_manager)
            
            if test_key == 'Disk-Space' and 'Disk-Space' in alert_manager.alert_instances:
                disk_alert = alert_manager.alert_instances['Disk-Space']
                if hasattr(disk_alert, 'running') and disk_alert.running:
                    disk_alert.stop_monitoring()
        else:
            success = run_test(test_key, host, port, None)

        message = f"Test '{test_name}' {'succeeded' if success else 'failed'}"
    
    except Exception as e:
        print(f"[TEST RUNNER] Error running test '{test_name}': {e}")
        success = False
        message = f"Error running test '{test_name}': {e}. Check container logs."

    return TestResult(test_name=test_name, success=success, message=message)


@app.post("/api/run_test/{test_name_url}", response_model=TestResult, tags=["Testing"])
async def trigger_test_endpoint(test_name_url: str, background_tasks: BackgroundTasks):
    """Triggers a specified test to run in the background."""
    if not CORE_APP_AVAILABLE:
         raise HTTPException(status_code=501, detail="Core app components not available for testing.")
         
    test_name = test_name_url.replace("_", " ")

    background_tasks.add_task(run_test_background, test_name)

    return TestResult(
        test_name=test_name,
        success=True,
        message=f"Test '{test_name}' started in background. Check container logs for details and results."
    )

@app.get("/api/recent-activity", response_model=List[AlertHistoryItem], tags=["Activity"])
async def get_recent_activity(hours: int = 24, limit: int = 250):
    """Returns the most recent activity/alerts from the system."""
    try:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_items = [
            item for item in ACTIVITY_HISTORY 
            if datetime.fromisoformat(item.timestamp) >= cutoff_time
        ]
        return recent_items[:limit]
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to retrieve recent activity")

# SUPERVISOR INTEGRATION
SUPERVISOR_HOST_PORT = "127.0.0.1:9001"
SUPERVISOR_USER = os.environ.get("SUPERVISOR_USER")
SUPERVISOR_PASS = os.environ.get("SUPERVISOR_PASS")

SUPERVISOR_URL = None
if SUPERVISOR_USER and SUPERVISOR_PASS:
    SUPERVISOR_URL = f"http://{SUPERVISOR_USER}:{SUPERVISOR_PASS}@{SUPERVISOR_HOST_PORT}/RPC2"

elif SUPERVISOR_HOST_PORT: 
    SUPERVISOR_URL = f"http://{SUPERVISOR_HOST_PORT}/RPC2"
else:
    pass

def get_supervisor_proxy():
    if not SUPERVISOR_URL:
        print(f"Cannot connect to Supervisor: URL not configured.")
        return None
    try:
        server = xmlrpc.client.ServerProxy(SUPERVISOR_URL)
        return server
    except Exception as e:
        print(f"Failed to create Supervisor RPC proxy: {e}")
        return None

# CONTROL ENDPOINTS
@app.post("/api/restart_container", status_code=202, tags=["Control"])
async def restart_container():
    """Restart ChannelWatch"""
    try:
        def delayed_restart():
            try:
                time.sleep(2)
                
                import subprocess
                subprocess.run(["kill", "-15", "1"], check=True)
            except Exception as e:
                print(f"[WebUI API] ERROR: Failed to restart ChannelWatch: {e}")
        
        import threading
        restart_thread = threading.Thread(target=delayed_restart)
        restart_thread.daemon = True
        restart_thread.start()
        
        return {"message": "Restart initiated. The application will be unavailable for a few moments."}
    except Exception as e:
        print(f"[WebUI API] ERROR: Failed to prepare ChannelWatch restart: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate restart: {str(e)}")

@app.post("/api/restart_core", status_code=202, tags=["Control"])
async def restart_core_process():
    """Uses Supervisor's XML-RPC interface to restart the core process."""
    global CORE_LAST_START_TIME
    server = get_supervisor_proxy()
    if not server:
        raise HTTPException(status_code=503, detail="Could not connect to Supervisor control interface. Check logs.")
        
    try:
        stop_result = server.supervisor.stopProcess("core", True)
        await asyncio.sleep(1) 
        start_result = server.supervisor.startProcess("core", True)
        
        CORE_LAST_START_TIME = datetime.now()
        
        return {"message": f"Restart command sent to process 'core'."}
    except ConnectionRefusedError:
        print(f"[WebUI API] ERROR: Connection refused to Supervisor RPC at {SUPERVISOR_HOST_PORT}. Is it running?")
        raise HTTPException(status_code=503, detail="Could not connect to Supervisor control interface (Connection Refused).")
    except xmlrpc.client.Fault as err:
         if err.faultCode == 401:
             print(f"[WebUI API] ERROR: Supervisor RPC authentication failed (401 Unauthorized). Check SUPERVISOR_USER/PASS environment variables.")
             raise HTTPException(status_code=401, detail="Supervisor authentication failed. Check credentials.")
         else:
             print(f"[WebUI API] ERROR: Supervisor RPC fault: {err.faultCode} {err.faultString}")
             raise HTTPException(status_code=500, detail=f"Supervisor command failed: {err.faultString}")
    except Exception as e:
        print(f"[WebUI API] ERROR: Failed to send restart command via Supervisor: {e}")
        if isinstance(e, AttributeError) and 'NoneType' in str(e):
             raise HTTPException(status_code=503, detail="Supervisor proxy object not available.")
        raise HTTPException(status_code=500, detail="Failed to send restart command.")

# STATIC FILE SERVING
STATIC_IMAGES_DIR = WEBUI_DIR / "static" / "images"
if STATIC_IMAGES_DIR.is_dir():
    app.mount("/images", StaticFiles(directory=STATIC_IMAGES_DIR), name="static-images")
else:
    pass

if STATIC_UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_UI_DIR, html=True), name="static-ui")
else:
    print(f"[WebUI] WARNING: Static UI directory not found at {STATIC_UI_DIR}. Frontend will not load.")
    @app.get("/")
    async def fallback_root():
        return {"message": "Frontend UI not found."}

@app.on_event("startup")
async def startup_event():
    """Application startup tasks."""
    history_file_watcher_thread.start()