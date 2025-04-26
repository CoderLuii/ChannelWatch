"""Service for retrieving and caching Channels DVR recording job information."""
import time
import threading
import json
import requests
from requests.exceptions import Timeout, RequestException
from typing import Dict, Any, List, Optional, Union

from .logging import log, LOG_STANDARD, LOG_VERBOSE

# JOB INFO
class JobInfoProvider:
    """Thread-safe service for accessing and caching Channels DVR recording jobs."""
    
    def __init__(self, host: str, port: int, cache_ttl: int = 86400):
        """Initialize job information provider with connection details and cache settings."""
        self.host = host
        self.port = port
        self._jobs_cache = {}
        self._jobs_cache_time = 0
        self._cache_ttl = cache_ttl
        self._lock = threading.Lock()
    
    def _get_base_url(self) -> str:
        """Generate base URL for API requests to Channels DVR server."""
        return f"http://{self.host}:{self.port}"
    
    # CACHING
    def cache_jobs(self) -> int:
        """Fetch and store all active recording jobs from the server."""
        try:
            with self._lock:
                url = f"{self._get_base_url()}/api/v1/jobs"
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                
                jobs = response.json()
                
                new_cache = {}
                for job in jobs:
                    job_id = job.get("id")
                    if job_id:
                        new_cache[job_id] = job
                
                self._jobs_cache = new_cache
                self._jobs_cache_time = time.time()
                return len(jobs)

        except Timeout:
            log(f"Error caching jobs: Timeout occurred connecting to {url}", level=LOG_STANDARD)
            return 0
        except RequestException as e:
            log(f"Error caching jobs: Network error connecting to {url}: {e}", level=LOG_STANDARD)
            return 0
        except json.JSONDecodeError as e:
            log(f"Error caching jobs: Failed to decode JSON response from {url}: {e}", level=LOG_STANDARD)
            return 0
        except Exception as e:
            log(f"Unexpected error caching jobs: {e}", level=LOG_STANDARD)
            return 0
    
    # JOB RETRIEVAL
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        """Retrieve all active recording jobs, refreshing cache if needed."""
        with self._lock:
            current_time = time.time()
            if current_time - self._jobs_cache_time > self._cache_ttl:
                self.cache_jobs()
            
            return list(self._jobs_cache.values())
    
    def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve specific recording job by ID, with fallback to direct API query."""
        log(f"Retrieving job by ID: {job_id}", level=LOG_VERBOSE)
        try:
            with self._lock:
                current_time = time.time()
                cache_expired = current_time - self._jobs_cache_time > self._cache_ttl
                if cache_expired:
                    log(f"Cache expired, refreshing before job lookup for {job_id}", level=LOG_VERBOSE)
                    self.cache_jobs()
                
                job = self._jobs_cache.get(job_id)
                if job:
                    log(f"Job {job_id} found in cache", level=LOG_VERBOSE)
                    return job
                log(f"Job {job_id} not found in cache, querying API directly", level=LOG_VERBOSE)
            
            try:
                url = f"{self._get_base_url()}/api/v1/jobs"
                log(f"Fetching job from API: {url}", level=LOG_VERBOSE)
                
                start_time = time.time()
                response = requests.get(url, timeout=5.0)
                fetch_time = time.time() - start_time
                
                if fetch_time > 2.0:
                    log(f"Slow job API call for {job_id}: {fetch_time:.2f}s", level=LOG_VERBOSE)
                
                response.raise_for_status()
                
                jobs = response.json()
                found_job = None
                for j in jobs:
                    if j.get("id") == job_id:
                        found_job = j
                        break
                
                if found_job:
                    log(f"Job {job_id} found from API", level=LOG_VERBOSE)
                    with self._lock:
                        self._jobs_cache[job_id] = found_job
                    return found_job
                
                log(f"Job {job_id} not found in API response", level=LOG_STANDARD)
                return None

            except Timeout:
                log(f"Timeout error fetching job {job_id} (5.0s timeout exceeded)", level=LOG_STANDARD)
                return None
            except RequestException as e:
                log(f"Network error fetching job {job_id}: {e}", level=LOG_STANDARD)
                return None
            except json.JSONDecodeError as e:
                log(f"JSON decode error fetching job {job_id}: {e}", level=LOG_STANDARD)
                return None
            except Exception as e:
                log(f"Unexpected error fetching job {job_id}: {e}", level=LOG_STANDARD)
                return None
        except Exception as e:
            log(f"Critical error in get_job_by_id for job {job_id}: {e}", level=LOG_STANDARD)
            return None
    
    # RECORDING RETRIEVAL
    def get_recording_by_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve completed recording by file ID with fallback to alternative endpoint."""
        try:
            url = f"{self._get_base_url()}/api/v1/recordings/{file_id}"
            response = requests.get(url, timeout=15)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code != 404:
                log(f"HTTP {response.status_code} fetching recording {file_id}", level=LOG_STANDARD)

            url = f"{self._get_base_url()}/api/v1/all?id={file_id}"
            response = requests.get(url, timeout=15)
            response.raise_for_status()

            data = response.json()
            if data and len(data) > 0:
                return data[0]
            else:
                return None

        except Timeout:
            log(f"Error fetching recording {file_id}: Timeout occurred.", level=LOG_STANDARD)
            return None
        except RequestException as e:
            log(f"Error fetching recording {file_id}: Network error: {e}", level=LOG_STANDARD)
            return None
        except json.JSONDecodeError as e:
            log(f"Error fetching recording {file_id}: Failed to decode JSON response: {e}", level=LOG_STANDARD)
            return None
        except Exception as e:
            log(f"Unexpected error fetching recording {file_id}: {e}", level=LOG_STANDARD)
            return None
    
    def get_all_recordings(self) -> List[Dict[str, Any]]:
        """Retrieve all completed recordings with fallback to alternative endpoint."""
        try:
            url = f"{self._get_base_url()}/api/v1/recordings"
            response = requests.get(url, timeout=20)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code != 404:
                log(f"HTTP {response.status_code} fetching all recordings", level=LOG_STANDARD)

            url = f"{self._get_base_url()}/api/v1/all"
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            return response.json()

        except Timeout:
            log(f"Error fetching all recordings: Timeout occurred.", level=LOG_STANDARD)
            return []
        except RequestException as e:
            log(f"Error fetching all recordings: Network error: {e}", level=LOG_STANDARD)
            return []
        except json.JSONDecodeError as e:
            log(f"Error fetching all recordings: Failed to decode JSON response: {e}", level=LOG_STANDARD)
            return []
        except Exception as e:
            log(f"Unexpected error fetching all recordings: {e}", level=LOG_STANDARD)
            return []
    
    # STATUS CHECKS
    def is_job_active(self, job_id: str) -> bool:
        """Determine if a recording job is currently active on the server."""
        job = self.get_job_by_id(job_id)
        return job is not None
    
    def is_recording_scheduled(self, job_id: str) -> bool:
        """Determine if a recording job is scheduled for future execution."""
        job = self.get_job_by_id(job_id)
        if job is None:
            return False
        
        current_time = time.time()
        start_time = job.get("start_time", 0)
        
        return (start_time - current_time) > 30 