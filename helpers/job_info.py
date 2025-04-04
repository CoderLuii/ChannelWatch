"""
Job information provider for accessing Channels DVR job API endpoints.

This module provides access to job-related information from the Channels DVR server,
including active recording jobs and completed recordings.
"""
import time
import threading
import json
import requests
from typing import Dict, Any, List, Optional, Union

from .logging import log, LOG_STANDARD, LOG_VERBOSE

class JobInfoProvider:
    """Provider for accessing Channels DVR job information.
    
    This class provides methods to fetch and cache job-related data from
    the Channels DVR API, including active recording jobs and completed recordings.
    
    Attributes:
        host: The Channels DVR server host
        port: The Channels DVR server port
        _jobs_cache: Dictionary containing cached recording jobs
        _jobs_cache_time: When the jobs cache was last updated
        _cache_ttl: How long to keep cached data (in seconds)
        _lock: Thread lock for thread-safe operations
    """
    
    def __init__(self, host: str, port: int, cache_ttl: int = 86400):
        """Initialize the job info provider.
        
        Args:
            host: The Channels DVR server host address
            port: The Channels DVR server port number
            cache_ttl: Cache time-to-live in seconds (default: 24 hours)
        """
        self.host = host
        self.port = port
        self._jobs_cache = {}
        self._jobs_cache_time = 0
        self._cache_ttl = cache_ttl
        self._lock = threading.Lock()
    
    def _get_base_url(self) -> str:
        """Get the base URL for API requests.
        
        Returns:
            The base URL for the Channels DVR API
        """
        return f"http://{self.host}:{self.port}"
    
    def cache_jobs(self) -> int:
        """Cache all active recording jobs from the server.
        
        Returns:
            int: Number of jobs cached, or 0 if caching failed
        """
        try:
            with self._lock:
                url = f"{self._get_base_url()}/api/v1/jobs"
                response = requests.get(url, timeout=10)
                
                if response.status_code == 200:
                    jobs = response.json()
                    
                    # Update the jobs cache
                    self._jobs_cache = {}
                    for job in jobs:
                        job_id = job.get("id")
                        if job_id:
                            self._jobs_cache[job_id] = job
                    
                    self._jobs_cache_time = time.time()
                    log(f"Cached {len(jobs)} recording jobs", level=LOG_VERBOSE)
                    return len(jobs)
                else:
                    log(f"Failed to cache jobs: HTTP {response.status_code}", level=LOG_STANDARD)
                    return 0
        except Exception as e:
            log(f"Error caching jobs: {e}", level=LOG_STANDARD)
            return 0
    
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        """Get all active recording jobs.
        
        Returns:
            A list of all recording jobs
        """
        with self._lock:
            # Check if cache is stale
            current_time = time.time()
            if current_time - self._jobs_cache_time > self._cache_ttl:
                self.cache_jobs()
            
            # Return a copy of the jobs list to prevent modification
            return list(self._jobs_cache.values())
    
    def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific recording job by ID.
        
        Args:
            job_id: The ID of the job to fetch
            
        Returns:
            The job data or None if not found
        """
        with self._lock:
            # Check if cache is stale
            current_time = time.time()
            if current_time - self._jobs_cache_time > self._cache_ttl:
                self.cache_jobs()
            
            # Return job if it exists
            job = self._jobs_cache.get(job_id)
            
            # If not in cache, try direct API request
            if job is None:
                try:
                    url = f"{self._get_base_url()}/api/v1/jobs"
                    response = requests.get(url, timeout=10)
                    
                    if response.status_code == 200:
                        jobs = response.json()
                        for j in jobs:
                            if j.get("id") == job_id:
                                # Cache this job for future requests
                                self._jobs_cache[job_id] = j
                                return j
                except Exception as e:
                    log(f"Error fetching job {job_id}: {e}", level=LOG_STANDARD)
            
            return job
    
    def get_recording_by_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Get a completed recording by file ID.
        
        Args:
            file_id: The ID of the recording file to fetch
            
        Returns:
            The recording data or None if not found
        """
        try:
            # Direct query to ensure we get the freshest data
            url = f"{self._get_base_url()}/api/v1/recordings/{file_id}"
            response = requests.get(url, timeout=10)
            
            # If direct query works, return the data
            if response.status_code == 200:
                return response.json()
                
            # Fall back to /api/v1/all endpoint if direct query fails
            url = f"{self._get_base_url()}/api/v1/all?id={file_id}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0]
            
            return None
        except Exception as e:
            log(f"Error fetching recording {file_id}: {e}", level=LOG_STANDARD)
            return None
    
    def get_all_recordings(self) -> List[Dict[str, Any]]:
        """Get all completed recordings.
        
        Returns:
            A list of all recordings
        """
        try:
            url = f"{self._get_base_url()}/api/v1/recordings"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                return response.json()
                
            # Fall back to /api/v1/all endpoint if direct query fails
            url = f"{self._get_base_url()}/api/v1/all"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            
            return []
        except Exception as e:
            log(f"Error fetching all recordings: {e}", level=LOG_STANDARD)
            return []
    
    def is_job_active(self, job_id: str) -> bool:
        """Check if a job is still active.
        
        Args:
            job_id: The ID of the job to check
            
        Returns:
            True if the job is active, False otherwise
        """
        job = self.get_job_by_id(job_id)
        return job is not None
    
    def is_recording_scheduled(self, job_id: str) -> bool:
        """Check if a recording is scheduled for the future.
        
        Args:
            job_id: The ID of the job to check
            
        Returns:
            True if the recording is scheduled for the future, False otherwise
        """
        job = self.get_job_by_id(job_id)
        if job is None:
            return False
        
        current_time = time.time()
        start_time = job.get("start_time", 0)
        
        # Consider it scheduled if start time is more than 30 seconds in the future
        return (start_time - current_time) > 30 