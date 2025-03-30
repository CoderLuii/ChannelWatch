"""
Tests for server connectivity.
"""
import time
import sys
import requests
from typing import Optional

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info

def test_connectivity(host: str, port: int) -> bool:
    """Test connectivity to the Channels DVR server.
    
    Args:
        host: The server host
        port: The server port
        
    Returns:
        bool: True if connection is successful, False otherwise
    """
    print_test_header("Connectivity Test")
    success = False
    
    try:
        log(f"Connecting to Channels DVR at {host}:{port}")
        
        # Test basic connection to status endpoint
        response = requests.get(f"http://{host}:{port}/status", timeout=5)
        
        if response.status_code == 200:
            # Parse server info
            try:
                data = response.json()
                version = data.get("version", "Unknown")
                log(f"Connected to server version {version}")
                
                # Test event stream endpoint
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

def test_api_endpoints(host: str, port: int) -> bool:
    """Test common API endpoints.
    
    Args:
        host: The server host
        port: The server port
        
    Returns:
        bool: True if all endpoints are accessible, False otherwise
    """
    print_test_header("API Endpoints Test")
    
    # Group endpoints by functionality for better organization
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
                "timeout": 3600  # 1 hour timeout
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
    
    # Test each group of endpoints
    for group_name, endpoints in endpoint_groups.items():
        log(f"\n=== Testing {group_name} Endpoints ===")
        
        # Test each endpoint in the group
        for endpoint_info in endpoints:
            url = endpoint_info["url"]
            description = endpoint_info["description"]
            timeout = endpoint_info["timeout"]
            is_stream = endpoint_info.get("stream", False)
            
            log(f"Testing {url} - {description}")
            
            try:
                # Handle endpoint based on its type
                if is_stream:
                    response = requests.get(
                        f"http://{host}:{port}{url}", 
                        headers={"Accept": "text/event-stream"},
                        stream=True,
                        timeout=5
                    )
                    # Just check if we can connect, don't read the stream
                    if response.status_code == 200:
                        response.close()
                elif url == "/devices/ANY/guide/xmltv":
                    log(f"  Using extended timeout ({timeout // 60} minutes) for XMLTV data...")
                    response = requests.get(f"http://{host}:{port}{url}", timeout=timeout)
                else:
                    response = requests.get(f"http://{host}:{port}{url}", timeout=timeout)
                
                # Record the result
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
    
    # Print summary of results
    log("\n=== API Test Results Summary ===")
    successful_count = sum(1 for r in results if r["success"])
    log(f"Endpoints Tested: {len(results)}")
    log(f"Successful: {successful_count}")
    log(f"Failed: {len(results) - successful_count}")
    
    print_test_footer(success)
    return success

def test_event_stream(host: str, port: int, duration: int) -> bool:
    """
    Monitor the event stream for a specified duration.
    
    Args:
        host: The server host
        port: The server port
        duration: Number of seconds to monitor
        
    Returns:
        bool: True if test successful, False otherwise
    """
    from ...helpers.tools import monitor_event_stream
    
    print_test_header(f"Event Stream Monitor ({duration} seconds)")
    
    try:
        # Call the existing monitor function but return success/failure
        success = monitor_event_stream(host, port, duration)
        print_test_footer(success)
        return success
    except Exception as e:
        log(f"Error during event stream test: {e}")
        print_test_footer(False)
        return False