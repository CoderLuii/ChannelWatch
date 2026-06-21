import threading

from core.helpers.job_info import JobInfoProvider


class _JobsResponse:
    def __init__(self, jobs):
        self._jobs = jobs

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
    provider = JobInfoProvider(host="127.0.0.1", port=9, cache_ttl=0)
    monkeypatch.setattr(
        "core.helpers.job_info.httpx.get",
        lambda *args, **kwargs: _JobsResponse([{"id": "job-1", "name": "News"}]),
    )

    jobs = _call_in_daemon_thread(provider.get_all_jobs)

    assert jobs == [{"id": "job-1", "name": "News"}]


def test_get_job_by_id_refreshes_expired_cache_without_nested_lock_deadlock(
    monkeypatch,
):
    provider = JobInfoProvider(host="127.0.0.1", port=9, cache_ttl=0)
    monkeypatch.setattr(
        "core.helpers.job_info.httpx.get",
        lambda *args, **kwargs: _JobsResponse([{"id": "job-2", "name": "Movie"}]),
    )

    job = _call_in_daemon_thread(lambda: provider.get_job_by_id("job-2"))

    assert job == {"id": "job-2", "name": "Movie"}
