import threading
import warnings

import httpx
import pytest

from core.helpers.job_info import JobInfoProvider


class _JobsResponse:
    def __init__(self, jobs, status_code=200):
        self._jobs = jobs
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._jobs


def _call_in_daemon_thread(func):
    result = {}

    def runner():
        result["value"] = func()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(1.0)
    assert not thread.is_alive(), "JobInfoProvider cache lookup deadlocked"
    return result["value"]


def test_get_all_jobs_refreshes_expired_cache_without_nested_lock_deadlock(monkeypatch):
    provider = JobInfoProvider(host="192.168.1.10", port=9, cache_ttl=0)
    monkeypatch.setattr(
        "core.helpers.job_info.httpx.get",
        lambda *args, **kwargs: _JobsResponse([{"id": "job-1", "name": "News"}]),
    )

    jobs = _call_in_daemon_thread(provider.get_all_jobs)

    assert jobs == [{"id": "job-1", "name": "News"}]


def test_get_job_by_id_refreshes_expired_cache_without_nested_lock_deadlock(
    monkeypatch,
):
    provider = JobInfoProvider(host="192.168.1.10", port=9, cache_ttl=0)
    monkeypatch.setattr(
        "core.helpers.job_info.httpx.get",
        lambda *args, **kwargs: _JobsResponse([{"id": "job-2", "name": "Movie"}]),
    )

    job = _call_in_daemon_thread(lambda: provider.get_job_by_id("job-2"))

    assert job == {"id": "job-2", "name": "Movie"}


def test_recording_snapshot_distinguishes_empty_success_from_network_failure(
    monkeypatch,
):
    provider = JobInfoProvider(host="192.168.1.10", port=9)
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: _JobsResponse([]))

    assert provider.fetch_recordings_snapshot() == []

    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(provider, "_get", fail)
    assert provider.fetch_recordings_snapshot() is None


def test_recording_snapshot_uses_all_endpoint_for_legacy_dvr(monkeypatch):
    provider = JobInfoProvider(host="192.168.1.10", port=9)
    paths = []

    def get(path, **kwargs):
        paths.append(path)
        if path == "/api/v1/recordings":
            return _JobsResponse([], status_code=404)
        return _JobsResponse([{"job_id": "job-1", "Processed": True}])

    monkeypatch.setattr(provider, "_get", get)

    assert provider.fetch_recordings_snapshot() == [
        {"job_id": "job-1", "Processed": True}
    ]
    assert paths == ["/api/v1/recordings", "/api/v1/all"]


def test_job_snapshot_refreshes_cache_and_filters_non_object_rows(monkeypatch):
    provider = JobInfoProvider(host="192.168.1.10", port=9)
    response = _JobsResponse(
        [
            {"id": "job-1", "name": "News"},
            "invalid-row",
            {"name": "Job without an ID"},
        ]
    )
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: response)

    snapshot = provider.fetch_jobs_snapshot()

    assert snapshot == [
        {"id": "job-1", "name": "News"},
        {"name": "Job without an ID"},
    ]
    assert provider.get_all_jobs() == [{"id": "job-1", "name": "News"}]


def test_job_snapshot_rejects_non_list_and_distinguishes_expected_failure(
    monkeypatch,
):
    provider = JobInfoProvider(host="192.168.1.10", port=9)
    monkeypatch.setattr(
        provider,
        "_get",
        lambda *args, **kwargs: _JobsResponse({"id": "not-a-list"}),
    )
    assert provider.fetch_jobs_snapshot() is None

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(provider, "_get", offline)
    assert provider.fetch_jobs_snapshot() is None


def test_job_and_recording_snapshot_fail_closed_on_unexpected_provider_error(
    monkeypatch,
):
    provider = JobInfoProvider(host="192.168.1.10", port=9)

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected fixture failure")

    monkeypatch.setattr(provider, "_get", fail)

    assert provider.fetch_jobs_snapshot() is None
    assert provider.fetch_recordings_snapshot() is None


def test_recording_snapshot_rejects_non_list_payload(monkeypatch):
    provider = JobInfoProvider(host="192.168.1.10", port=9)
    monkeypatch.setattr(
        provider,
        "_get",
        lambda *args, **kwargs: _JobsResponse({"recordings": []}),
    )

    assert provider.fetch_recordings_snapshot() is None


def test_legacy_recording_provider_warns_only_when_instantiated():
    with warnings.catch_warnings(record=True) as import_warnings:
        warnings.simplefilter("always")
        from core.helpers.recording_info import RecordingInfoProvider

    assert import_warnings == []

    with pytest.warns(
        DeprecationWarning,
        match="RecordingInfoProvider is deprecated, use JobInfoProvider instead",
    ):
        provider = RecordingInfoProvider(host="192.168.1.10", port=9)

    assert isinstance(provider, JobInfoProvider)
    assert provider.host == "192.168.1.10"
    assert provider.port == 9
