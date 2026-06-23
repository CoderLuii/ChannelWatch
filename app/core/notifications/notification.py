"""Fan out notifications through providers and webhooks.

The manager coordinates provider registration, DVR/event routing, rate limiting,
delivery retry persistence, circuit-breaker checks, and optional webhook
delivery for each alert notification.
"""

import asyncio
from typing import Any, Dict, List, Optional, Set

from ..helpers.logging import log, LOG_STANDARD
from .providers.base import NotificationProvider
from .rate_limiter import RateLimiter
from .delivery import CircuitBreaker, deliver_with_retry, estimate_payload_size

APPRISE_DEST_KEYS = (
    "pushover",
    "discord",
    "email",
    "telegram",
    "slack",
    "gotify",
    "matrix",
    "custom",
)
ALL_DEST_KEYS = APPRISE_DEST_KEYS + ("webhook",)
SINGLE_ATTEMPT_APPRISE_EVENT_TYPES = {"channel", "vod"}

_ALL_ENABLED: Dict[str, bool] = {k: True for k in ALL_DEST_KEYS}


def _resolve_routing(
    dvr_id: str, event_type: str, routing_config: Dict[str, Any]
) -> Dict[str, bool]:
    if (
        not isinstance(routing_config, dict)
        or not routing_config
        or not dvr_id
        or not event_type
    ):
        return dict(_ALL_ENABLED)
    dvr_routing = routing_config.get(dvr_id)
    if not isinstance(dvr_routing, dict):
        return dict(_ALL_ENABLED)
    event_routing = dvr_routing.get(event_type)
    if not isinstance(event_routing, dict):
        return dict(_ALL_ENABLED)
    return {k: bool(event_routing.get(k, True)) for k in ALL_DEST_KEYS}


def _load_routing_config() -> Dict[str, Any]:
    try:
        from ..helpers.config import CoreSettings

        return CoreSettings.get().notification_routing or {}
    except Exception:
        return {}


def _should_retry_apprise(event_type: str) -> bool:
    """Return whether the outer Apprise wrapper should retry this alert type."""
    normalized = (event_type or "").strip().lower()
    return normalized not in SINGLE_ATTEMPT_APPRISE_EVENT_TYPES


class NotificationManager:
    """Coordinate all configured notification destinations.

    A manager owns provider instances, an optional webhook manager, rate-limit
    state, and delivery retry/circuit-breaker state backed by the configured
    database engine when available.
    """

    def __init__(
        self, rate_limit: int = 20, rate_window: int = 300, db_engine: Any = None
    ):
        self.providers: Dict[str, NotificationProvider] = {}
        self.webhook_manager: Optional[Any] = None
        self.rate_limiter = RateLimiter(
            max_notifications=rate_limit,
            window_seconds=rate_window,
        )
        self.circuit_breaker = CircuitBreaker()
        self.db_engine = db_engine

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

    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Send one notification through all routed destinations.

        The title, message, DVR id, event type, and optional activity metadata
        are checked against rate limits and routing settings before delivery.
        The return value is ``True`` if any configured destination succeeds.
        """
        has_webhooks = bool(
            self.webhook_manager and self.webhook_manager.is_configured()
        )
        if not self.providers and not has_webhooks:
            return False

        if not self.rate_limiter.allow():
            log(f"Notification suppressed by rate limiter: {title}", level=LOG_STANDARD)
            return False

        dvr_id = kwargs.get("dvr_id", "")
        event_type = kwargs.get("event_type", "")
        routing = _resolve_routing(dvr_id, event_type, _load_routing_config())
        activity_event_id = kwargs.get("activity_event_id")

        allowed_apprise: Optional[Set[str]] = None
        if dvr_id and event_type:
            allowed_apprise = {k for k in APPRISE_DEST_KEYS if routing.get(k, True)}

        payload_size = estimate_payload_size(title, message, **kwargs)

        overall_success = False

        for provider_type, provider in self.providers.items():
            if not provider.is_configured():
                continue
            send_kwargs = dict(kwargs)
            if allowed_apprise is not None:
                send_kwargs["allowed_apprise_destinations"] = allowed_apprise

            def _call(p=provider, sk=send_kwargs):
                return p.send_notification(title, message, **sk)

            success = deliver_with_retry(
                dvr_id=dvr_id,
                channel="apprise",
                event_type=event_type,
                provider_type=provider_type,
                channel_id="apprise",
                payload_size=payload_size,
                deliver_fn=_call,
                circuit_breaker=self.circuit_breaker,
                db_engine=self.db_engine,
                activity_event_id=activity_event_id,
                with_retry=_should_retry_apprise(event_type),
            )
            if success:
                log(
                    f"Notification sent via {provider_type}: {title}",
                    level=LOG_STANDARD,
                )
                overall_success = True
            else:
                log(
                    f"Notification failed via {provider_type}: {title}",
                    level=LOG_STANDARD,
                )

        wm = self.webhook_manager
        if wm is not None and has_webhooks and routing.get("webhook", True):

            def _webhook_call(w=wm):
                return w.send_notification(title, message, **kwargs)

            success = deliver_with_retry(
                dvr_id=dvr_id,
                channel="webhook",
                event_type=event_type,
                provider_type="webhook",
                channel_id="",
                payload_size=payload_size,
                deliver_fn=_webhook_call,
                circuit_breaker=self.circuit_breaker,
                db_engine=self.db_engine,
                activity_event_id=activity_event_id,
                with_retry=False,
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

    async def send_notification_async(self, title: str, message: str, **kwargs) -> bool:
        """Send one notification without blocking the asyncio event loop."""
        has_webhooks = bool(
            self.webhook_manager and self.webhook_manager.is_configured()
        )
        if not self.providers and not has_webhooks:
            return False

        if not self.rate_limiter.allow():
            log(f"Notification suppressed by rate limiter: {title}", level=LOG_STANDARD)
            return False

        dvr_id = kwargs.get("dvr_id", "")
        event_type = kwargs.get("event_type", "")
        routing = _resolve_routing(dvr_id, event_type, _load_routing_config())
        activity_event_id = kwargs.get("activity_event_id")

        allowed_apprise: Optional[Set[str]] = None
        if dvr_id and event_type:
            allowed_apprise = {k for k in APPRISE_DEST_KEYS if routing.get(k, True)}

        payload_size = estimate_payload_size(title, message, **kwargs)

        overall_success = False

        for provider_type, provider in self.providers.items():
            if not provider.is_configured():
                continue
            send_kwargs = dict(kwargs)
            if allowed_apprise is not None:
                send_kwargs["allowed_apprise_destinations"] = allowed_apprise

            def _call(p=provider, sk=send_kwargs):
                return p.send_notification(title, message, **sk)

            # Providers are intentionally sync for plugin compatibility; run
            # the existing retry/delivery loop in a worker thread.
            success = await asyncio.to_thread(
                deliver_with_retry,
                dvr_id=dvr_id,
                channel="apprise",
                event_type=event_type,
                provider_type=provider_type,
                channel_id="apprise",
                payload_size=payload_size,
                deliver_fn=_call,
                circuit_breaker=self.circuit_breaker,
                db_engine=self.db_engine,
                activity_event_id=activity_event_id,
                with_retry=_should_retry_apprise(event_type),
            )
            if success:
                log(
                    f"Notification sent via {provider_type}: {title}",
                    level=LOG_STANDARD,
                )
                overall_success = True
            else:
                log(
                    f"Notification failed via {provider_type}: {title}",
                    level=LOG_STANDARD,
                )

        wm = self.webhook_manager
        if wm is not None and has_webhooks and routing.get("webhook", True):

            def _webhook_call(w=wm):
                return w.send_notification(title, message, **kwargs)

            success = await asyncio.to_thread(
                deliver_with_retry,
                dvr_id=dvr_id,
                channel="webhook",
                event_type=event_type,
                provider_type="webhook",
                channel_id="",
                payload_size=payload_size,
                deliver_fn=_webhook_call,
                circuit_breaker=self.circuit_breaker,
                db_engine=self.db_engine,
                activity_event_id=activity_event_id,
                with_retry=False,
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
