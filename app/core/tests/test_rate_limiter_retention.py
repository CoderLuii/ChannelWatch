"""Expiry and capacity must not turn memory reclamation into a limit bypass."""
from types import SimpleNamespace

from ui.backend import main


def test_expired_other_clients_are_reclaimed(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    limiter = main.InMemoryRateLimiter(60)
    for client in ("a", "b", "c"):
        assert limiter.allow_request(client, 2)
    clock[0] = 61
    assert limiter.allow_request("new", 2)
    assert list(limiter._requests) == ["new"]


def test_capacity_preserves_active_abuser_and_recovers_after_expiry(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    limiter = main.InMemoryRateLimiter(60, max_clients=2)
    assert limiter.allow_request("active", 1)
    assert limiter.allow_request("other", 1)
    assert not limiter.allow_request("new", 1)
    assert not limiter.allow_request("active", 1)
    assert len(limiter._requests) == 2
    clock[0] = 60
    assert limiter.allow_request("new", 1)
    assert list(limiter._requests) == ["new"]


def test_recent_activity_moves_client_after_older_clients(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    limiter = main.InMemoryRateLimiter(60)
    assert limiter.allow_request("renewed", 2)
    clock[0] = 1
    assert limiter.allow_request("expires", 1)
    clock[0] = 59
    assert limiter.allow_request("renewed", 2)
    clock[0] = 61
    assert limiter.allow_request("new", 1)
    assert "expires" not in limiter._requests
    assert limiter.allow_request("renewed", 2)
    assert not limiter.allow_request("renewed", 2)
