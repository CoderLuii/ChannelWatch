"""Fan out notifications through providers and webhooks.

The manager coordinates provider registration, DVR/event routing, rate limiting,
delivery retry persistence, circuit-breaker checks, and optional webhook
delivery for each alert notification.
"""

from dataclasses import dataclass
import asyncio
from queue import Empty, Full, Queue
import threading
import time
from typing import Any, Dict, List, Optional, Set

from ..helpers.logging import log, LOG_STANDARD
from .providers.base import NotificationProvider
from .rate_limiter import RateLimiter
from .delivery import CircuitBreaker, deliver_with_retry, estimate_payload_size
from .routing import ALL_DEST_KEYS as ALL_DEST_KEYS
from .routing import APPRISE_DEST_KEYS, resolve_notification_routing

SINGLE_ATTEMPT_APPRISE_EVENT_TYPES = {"channel", "runtime", "vod"}
DIAGNOSTIC_DEADLINE_SECONDS = 19.5


def _resolve_routing(
    dvr_id: str, event_type: str, routing_config: Dict[str, Any]
) -> Dict[str, bool]:
    return resolve_notification_routing(dvr_id, event_type, routing_config)


def _load_routing_config() -> Any:
    try:
        from ..helpers.config import CoreSettings

        routing = CoreSettings.get().notification_routing
        # Only the deliberate legacy absence representations enable every
        # destination.  Other falsy values are malformed and must reach the
        # resolver unchanged so it can fail closed.
        if routing is None or (isinstance(routing, dict) and not routing):
            return {}
        return routing
    except Exception:
        # A settings read failure is not equivalent to the deliberate legacy
        # empty mapping. Return an invalid sentinel so resolution fails closed.
        return None


def _should_retry_apprise(event_type: str) -> bool:
    """Return whether the outer Apprise wrapper should retry this alert type."""
    normalized = (event_type or "").strip().lower()
    return normalized not in SINGLE_ATTEMPT_APPRISE_EVENT_TYPES


@dataclass(frozen=True)
class _QueuedNotification:
    title: str
    message: str
    kwargs: dict[str, Any]
    dedupe_key: Optional[str]
    terminal: bool = False


class NotificationManager:
    """Coordinate all configured notification destinations.

    A manager owns provider instances, an optional webhook manager, rate-limit
    state, and delivery retry/circuit-breaker state backed by the configured
    database engine when available.
    """

    def __init__(
        self,
        rate_limit: int = 20,
        rate_window: int = 300,
        db_engine: Any = None,
        rate_limiter: Optional[RateLimiter] = None,
        delivery_queue_size: int = 256,
        diagnostic_mode: bool = False,
        diagnostic_deadline_seconds: float = DIAGNOSTIC_DEADLINE_SECONDS,
    ):
        self.providers: Dict[str, NotificationProvider] = {}
        self.webhook_manager: Optional[Any] = None
        self.rate_limiter = rate_limiter or RateLimiter(
            max_notifications=rate_limit, window_seconds=rate_window
        )
        self.circuit_breaker = CircuitBreaker()
        self.db_engine = db_engine
        self._provider_delivery_lock = threading.Lock()
        self._delivery_queue: Queue[Any] = Queue(
            maxsize=max(1, int(delivery_queue_size))
        )
        self.diagnostic_mode = bool(diagnostic_mode)
        self.diagnostic_deadline_seconds = max(
            0.01, float(diagnostic_deadline_seconds)
        )
        self._queue_state_lock = threading.Lock()
        self._queued_dedupe_keys: set[str] = set()
        self._delivery_worker: Optional[threading.Thread] = None
        self._queue_accepting = True
        self._queue_sentinel = object()
        self._queue_dropped = 0

    def register_provider(self, provider: NotificationProvider) -> bool:
        """Register a notification provider by its provider type.

        Returns ``False`` when a provider with the same type was already
        registered, otherwise stores the provider and returns ``True``.
        """
        if provider.PROVIDER_TYPE in self.providers:
            log(f"Provider {provider.PROVIDER_TYPE} already registered")
            return False
        self.providers[provider.PROVIDER_TYPE] = provider
        return True

    def initialize_provider(self, provider_type: str, **kwargs) -> bool:
        """Initialize a registered provider with configuration keyword values.

        Unknown provider types are logged and return ``False``; known providers
        receive the keyword arguments unchanged.
        """
        if provider_type not in self.providers:
            log(f"Provider {provider_type} not registered")
            return False
        return self.providers[provider_type].initialize(**kwargs)

    def register_webhook_manager(self, webhook_manager: Any) -> None:
        """Attach the webhook manager used for webhook notification fanout."""
        self.webhook_manager = webhook_manager

    def get_active_providers(self) -> List[str]:
        """Return provider types whose instances are currently configured."""
        return [
            provider_type
            for provider_type, provider in self.providers.items()
            if provider.is_configured()
        ]

    def _has_configured_destinations(self) -> bool:
        has_webhooks = bool(
            self.webhook_manager and self.webhook_manager.is_configured()
        )
        return bool(self.get_active_providers() or has_webhooks)

    def has_configured_destinations(self) -> bool:
        """Return whether any provider or webhook can accept a notification."""

        return self._has_configured_destinations()

    @staticmethod
    def _dedupe_key(kwargs: dict[str, Any]) -> Optional[str]:
        raw_key = kwargs.get("notification_dedupe_key") or kwargs.get(
            "activity_event_id"
        )
        if raw_key in (None, ""):
            return None
        return "|".join(
            (
                str(kwargs.get("dvr_id", "")),
                str(kwargs.get("event_type", "")),
                str(raw_key),
            )
        )

    def _iter_apprise_destinations(
        self,
        provider: NotificationProvider,
        allowed_apprise: Optional[Set[str]],
    ) -> list[tuple[str, str]]:
        enumerator = getattr(type(provider), "notification_destinations", None)
        if not callable(enumerator):
            return [("apprise", "apprise")]
        return list(provider.notification_destinations(allowed_apprise))

    def _deliver_notification(self, title: str, message: str, **kwargs) -> bool:
        """Perform one delivery in the caller or the owned queue worker."""
        has_webhooks = bool(
            self.webhook_manager and self.webhook_manager.is_configured()
        )
        if not self.providers and not has_webhooks:
            return False

        dvr_id = kwargs.get("dvr_id", "")
        event_type = kwargs.get("event_type", "")
        routing = _resolve_routing(dvr_id, event_type, _load_routing_config())
        activity_event_id = kwargs.get("activity_event_id")
        diagnostic_deadline = kwargs.get("_diagnostic_deadline_monotonic")

        allowed_apprise: Optional[Set[str]] = None
        if dvr_id and event_type:
            allowed_apprise = {k for k in APPRISE_DEST_KEYS if routing.get(k, False)}

        payload_size = estimate_payload_size(title, message, **kwargs)

        overall_success = False

        with self._provider_delivery_lock:
            for provider_type, provider in self.providers.items():
                if not provider.is_configured():
                    continue
                send_kwargs = dict(kwargs)
                if allowed_apprise is not None:
                    send_kwargs["allowed_apprise_destinations"] = allowed_apprise

                destinations = self._iter_apprise_destinations(
                    provider, allowed_apprise
                )
                for destination_id, destination_key in destinations:
                    destination_kwargs = dict(send_kwargs)
                    if destination_id != "apprise":
                        destination_kwargs["apprise_destination_id"] = destination_id

                    def _call(p=provider, sk=destination_kwargs):
                        return p.send_notification(title, message, **sk)

                    success = deliver_with_retry(
                        dvr_id=dvr_id,
                        channel="apprise",
                        event_type=event_type,
                        provider_type=provider_type,
                        channel_id=destination_id,
                        payload_size=payload_size,
                        deliver_fn=_call,
                        circuit_breaker=self.circuit_breaker,
                        db_engine=self.db_engine,
                        activity_event_id=activity_event_id,
                        with_retry=_should_retry_apprise(event_type),
                        deadline_monotonic=diagnostic_deadline,
                    )
                    if success:
                        log(
                            f"Notification sent via {provider_type}/{destination_key}: {title}",
                            level=LOG_STANDARD,
                        )
                        overall_success = True
                    else:
                        log(
                            f"Notification failed via {provider_type}/{destination_key}: {title}",
                            level=LOG_STANDARD,
                        )

            wm = self.webhook_manager
            if wm is not None and has_webhooks and routing.get("webhook", False):

                def _webhook_call(w=wm):
                    return w.send_notification(title, message, **kwargs)

                success = deliver_with_retry(
                    dvr_id=dvr_id,
                    channel="webhook",
                    event_type=event_type,
                    provider_type="webhook",
                    channel_id="webhook",
                    payload_size=payload_size,
                    deliver_fn=_webhook_call,
                    circuit_breaker=self.circuit_breaker,
                    db_engine=self.db_engine,
                    activity_event_id=activity_event_id,
                    with_retry=False,
                    deadline_monotonic=diagnostic_deadline,
                )
                if success:
                    overall_success = True
            elif has_webhooks and dvr_id and event_type:
                log(
                    f"Notification skipped (routing): {dvr_id}/{event_type} → webhook disabled",
                    level=LOG_STANDARD,
                )

        active_destinations = len(self.get_active_providers()) + (
            1 if has_webhooks else 0
        )
        if not overall_success and active_destinations > 0:
            log(
                f"Notification failed for all configured providers (Title: {title}).",
                level=LOG_STANDARD,
            )

        return overall_success

    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Synchronously deliver one rate-limited notification."""
        if self.diagnostic_mode and "_diagnostic_deadline_monotonic" not in kwargs:
            kwargs = dict(kwargs)
            kwargs["_diagnostic_deadline_monotonic"] = (
                time.monotonic() + self.diagnostic_deadline_seconds
            )
        if not self._has_configured_destinations():
            return False
        if not self.rate_limiter.allow():
            log(f"Notification suppressed by rate limiter: {title}", level=LOG_STANDARD)
            return False
        return self._deliver_notification(title, message, **kwargs)

    def _ensure_delivery_worker_locked(self) -> bool:
        """Start the worker while the caller holds ``_queue_state_lock``."""
        if not self._queue_accepting:
            return False
        if self._delivery_worker is None or not self._delivery_worker.is_alive():
            worker = threading.Thread(
                target=self._delivery_worker_loop,
                name=f"channelwatch-notifications-{id(self):x}",
                daemon=True,
            )
            try:
                worker.start()
            except RuntimeError as exc:
                log(
                    f"Notification queue worker failed to start: {type(exc).__name__}",
                    level=LOG_STANDARD,
                )
                self._delivery_worker = None
                return False
            self._delivery_worker = worker
        return True

    def _delivery_worker_loop(self) -> None:
        while True:
            request = self._delivery_queue.get()
            terminal_request = False
            try:
                if request is self._queue_sentinel:
                    return
                terminal_request = bool(
                    isinstance(request, _QueuedNotification) and request.terminal
                )
                try:
                    self._deliver_notification(
                        request.title,
                        request.message,
                        **request.kwargs,
                    )
                except BaseException as exc:
                    # This is the terminal boundary of an owned daemon thread.
                    # A provider must not strand later work or shutdown markers,
                    # even if it raises SystemExit or another BaseException.
                    log(
                        f"Notification queue worker error: {type(exc).__name__}",
                        level=LOG_STANDARD,
                    )
            finally:
                if isinstance(request, _QueuedNotification) and request.dedupe_key:
                    with self._queue_state_lock:
                        self._queued_dedupe_keys.discard(request.dedupe_key)
                self._delivery_queue.task_done()
            if terminal_request:
                with self._queue_state_lock:
                    if self._delivery_worker is threading.current_thread():
                        self._delivery_worker = None
                return

    def enqueue_notification(self, title: str, message: str, **kwargs) -> bool:
        """Accept work without waiting for provider retries.

        Overflow drops the newest request. Only requests with an explicit
        ``notification_dedupe_key`` or ``activity_event_id`` are deduplicated;
        unrelated alerts are never guessed to be duplicates.
        """
        if not self._has_configured_destinations():
            return False
        dedupe_key = self._dedupe_key(kwargs)
        with self._queue_state_lock:
            if not self._queue_accepting:
                return False
            if dedupe_key and dedupe_key in self._queued_dedupe_keys:
                log(
                    f"Notification skipped (duplicate queued work): {title}",
                    level=LOG_STANDARD,
                )
                return True
            # Start the worker before consuming a global rate token or
            # accepting queue state, so thread-start failure is a clean reject.
            if not self._ensure_delivery_worker_locked():
                return False
            # Enqueues are serialized by ``_queue_state_lock`` and the worker
            # only removes items, so a non-full queue cannot become full before
            # this caller's put. Reject overflow before consuming an
            # installation-wide rate-limit token.
            if self._delivery_queue.full():
                self._queue_dropped += 1
                log(
                    f"Notification queue full; newest notification dropped: {title}",
                    level=LOG_STANDARD,
                )
                return False
            if not self.rate_limiter.allow():
                log(
                    f"Notification suppressed by rate limiter: {title}",
                    level=LOG_STANDARD,
                )
                return False
            request = _QueuedNotification(title, message, dict(kwargs), dedupe_key)
            try:
                self._delivery_queue.put_nowait(request)
            except Full:
                # Defensive only: normal producers are serialized above and
                # shutdown cannot publish its sentinel while this lock is held.
                self._queue_dropped += 1
                log(
                    f"Notification queue full; newest notification dropped: {title}",
                    level=LOG_STANDARD,
                )
                return False
            if dedupe_key:
                self._queued_dedupe_keys.add(dedupe_key)
        return True

    def enqueue_terminal_notification(
        self, title: str, message: str, **kwargs
    ) -> bool:
        """Atomically accept one final notice and close this manager's queue.

        Once accepted, ordinary queued work is discarded and the owned worker
        attempts this request exactly once before exiting. The method never
        waits for provider I/O, so monitor reload and shutdown remain bounded.
        Acceptance does not guarantee remote receipt: routing, provider
        behavior, and the remote service still determine delivery success.
        """
        if not self._has_configured_destinations():
            return False
        dedupe_key = self._dedupe_key(kwargs)
        with self._queue_state_lock:
            if not self._queue_accepting:
                return False
            if not self._ensure_delivery_worker_locked():
                return False
            if not self.rate_limiter.allow():
                log(
                    f"Notification suppressed by rate limiter: {title}",
                    level=LOG_STANDARD,
                )
                return False

            # No producer or shutdown path can mutate the queue while this
            # state lock is held. The worker may already own one in-flight
            # request, but every still-pending request is stale monitor work.
            while True:
                try:
                    queued = self._delivery_queue.get_nowait()
                except Empty:
                    break
                if isinstance(queued, _QueuedNotification) and queued.dedupe_key:
                    self._queued_dedupe_keys.discard(queued.dedupe_key)
                self._delivery_queue.task_done()

            terminal_request = _QueuedNotification(
                title,
                message,
                dict(kwargs),
                dedupe_key,
                terminal=True,
            )
            try:
                self._delivery_queue.put_nowait(terminal_request)
            except Full:  # pragma: no cover - producers are serialized above.
                log(
                    f"Terminal notification queue handoff failed: {title}",
                    level=LOG_STANDARD,
                )
                return False
            if dedupe_key:
                self._queued_dedupe_keys.add(dedupe_key)
            # Publish the terminal state only after the final item is queued.
            # Repeated shutdown then observes instead of draining this request.
            self._queue_accepting = False
        return True

    async def send_notification_async(self, title: str, message: str, **kwargs) -> bool:
        """Queue one notification and return whether the queue accepted it."""
        if self.diagnostic_mode:
            deadline = time.monotonic() + self.diagnostic_deadline_seconds
            diagnostic_kwargs = dict(kwargs)
            diagnostic_kwargs["_diagnostic_deadline_monotonic"] = deadline
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self.send_notification,
                        title,
                        message,
                        **diagnostic_kwargs,
                    ),
                    timeout=self.diagnostic_deadline_seconds + 0.25,
                )
            except TimeoutError:
                log(
                    "Notification diagnostic exceeded its delivery deadline",
                    level=LOG_STANDARD,
                )
                return False
        return self.enqueue_notification(title, message, **kwargs)

    def wait_for_delivery_queue(self, timeout: float = 5.0) -> bool:
        """Wait for queued and in-flight work; intended for tests and drains."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._delivery_queue.mutex:
                unfinished = self._delivery_queue.unfinished_tasks
            if unfinished == 0:
                return True
            time.sleep(0.01)
        with self._delivery_queue.mutex:
            return self._delivery_queue.unfinished_tasks == 0

    def shutdown_delivery_queue(
        self, *, drain: bool = False, timeout: float = 1.0
    ) -> bool:
        """Stop accepting work and bound shutdown time.

        Reload and shutdown default to dropping pending work. In-flight provider
        calls cannot be interrupted safely and may finish in the daemon worker,
        but they never hold up process or monitor shutdown.
        """
        with self._queue_state_lock:
            first_shutdown = self._queue_accepting
            if not first_shutdown and self._delivery_worker is None:
                return True
            self._queue_accepting = False

        worker = self._delivery_worker
        if not first_shutdown:
            # A prior shutdown or terminal handoff already owns queue closure.
            # Repeated callers only observe the worker; they never dequeue the
            # terminal request or duplicate a shutdown marker.
            if worker is None:
                return True
            worker.join(timeout=max(0.0, timeout))
            stopped = not worker.is_alive()
            if stopped:
                with self._queue_state_lock:
                    if self._delivery_worker is worker:
                        self._delivery_worker = None
            return stopped

        if drain:
            self.wait_for_delivery_queue(timeout=timeout)

        while True:
            try:
                queued = self._delivery_queue.get_nowait()
            except Empty:
                break
            if isinstance(queued, _QueuedNotification) and queued.dedupe_key:
                with self._queue_state_lock:
                    self._queued_dedupe_keys.discard(queued.dedupe_key)
            self._delivery_queue.task_done()

        if worker is None:
            return True
        try:
            self._delivery_queue.put_nowait(self._queue_sentinel)
        except Full:
            log(
                "Notification queue could not accept shutdown marker",
                level=LOG_STANDARD,
            )
            return False
        worker.join(timeout=max(0.0, timeout))
        stopped = not worker.is_alive()
        if stopped:
            with self._queue_state_lock:
                self._delivery_worker = None
        return stopped

    @property
    def delivery_queue_dropped(self) -> int:
        return self._queue_dropped
