"""Implements connectivity and API endpoint validation tests for Channels DVR server."""
import time
import sys
import requests
from typing import Optional

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info

# CONNECTIVITY TESTING

def test_connectivity(host: str, port: int) -> bool:
    """Verifies basic connectivity to the Channels DVR server and event stream."""
    print_test_header("Connectivity Test")
    success = False
    
    try:
        log(f"Connecting to Channels DVR at {host}:{port}")
        
        response = requests.get(f"http://{host}:{port}/status", timeout=5)
        
        if response.status_code == 200:
            try:
                data = response.json()
                version = data.get("version", "Unknown")
                log(f"Connected to server version {version}")
                
                log("Testing connection to event stream...")
                event_response = requests.get(
                    f"http://{host}:{port}/dvr/events/subscribe", 
                    headers={"Accept": "text/event-stream"},
                    stream=True, 
                    timeout=5
                )
                
                if event_response.status_code == 200:
                    log("Event stream connection successful")
                    success = True
                else:
                    log(f"Event stream connection failed: HTTP {event_response.status_code}")
                    success = False
            except Exception as e:
                log(f"Error parsing server information: {e}")
                success = False
        else:
            log(f"Connection failed: HTTP {response.status_code}")
            success = False
    except Exception as e:
        log(f"Connection error: {e}")
        success = False
    
    print_test_footer(success)
    return success

# API ENDPOINT TESTING

def test_api_endpoints(host: str, port: int) -> bool:
    """Tests multiple API endpoints across various server functionality groups."""
    print_test_header("API Endpoints Test")
    
    endpoint_groups = {
        "Core System": [
            {
                "url": "/status",
                "description": "Server status and version information",
                "timeout": 5
            },
            {
                "url": "/dvr/events/subscribe",
                "description": "Event stream monitoring",
                "timeout": 5,
                "stream": True
            },
            {
                "url": "/dvr",
                "description": "Disk space and storage information",
                "timeout": 5
            }
        ],
        "Channel & Program Data": [
            {
                "url": "/api/v1/channels",
                "description": "Channel information for Channel-Watching alert",
                "timeout": 5
            },
            {
                "url": "/devices/ANY/guide/xmltv",
                "description": "Program guide data for program information",
                "timeout": 3600
            }
        ],
        "Recording & VOD Data": [
            {
                "url": "/api/v1/all",
                "description": "VOD/recorded content metadata",
                "timeout": 10
            }
        ]
    }
    
    success = True
    results = []
    
    for group_name, endpoints in endpoint_groups.items():
        log(f"\n=== Testing {group_name} Endpoints ===")
        
        for endpoint_info in endpoints:
            url = endpoint_info["url"]
            description = endpoint_info["description"]
            timeout = endpoint_info["timeout"]
            is_stream = endpoint_info.get("stream", False)
            
            log(f"Testing {url} - {description}")
            
            try:
                if is_stream:
                    response = requests.get(
                        f"http://{host}:{port}{url}", 
                        headers={"Accept": "text/event-stream"},
                        stream=True,
                        timeout=5
                    )
                    if response.status_code == 200:
                        response.close()
                elif url == "/devices/ANY/guide/xmltv":
                    log(f"  Using extended timeout ({timeout // 60} minutes) for XMLTV data...")
                    response = requests.get(f"http://{host}:{port}{url}", timeout=timeout)
                else:
                    response = requests.get(f"http://{host}:{port}{url}", timeout=timeout)
                
                if response.status_code == 200:
                    status = "✅ SUCCESS"
                    log(f"  {status} (HTTP 200)")
                    results.append({"url": url, "success": True})
                else:
                    status = "❌ FAILED"
                    log(f"  {status} (HTTP {response.status_code})")
                    success = False
                    results.append({"url": url, "success": False, "code": response.status_code})
            except Exception as e:
                log(f"  ❌ ERROR: {e}")
                success = False
                results.append({"url": url, "success": False, "error": str(e)})
    
    log("\n=== API Test Results Summary ===")
    successful_count = sum(1 for r in results if r["success"])
    log(f"Endpoints Tested: {len(results)}")
    log(f"Successful: {successful_count}")
    log(f"Failed: {len(results) - successful_count}")
    
    print_test_footer(success)
    return success

# EVENT STREAM MONITORING

def test_event_stream(host: str, port: int, duration: int) -> bool:
    """Monitors the Channels DVR event stream for the specified duration."""
    from ...helpers.tools import monitor_event_stream
    
    print_test_header(f"Event Stream Monitor ({duration} seconds)")
    
    try:
        success = monitor_event_stream(host, port, duration)
        print_test_footer(success)
        return success
    except Exception as e:
        log(f"Error during event stream test: {e}")
        print_test_footer(False)
        return False