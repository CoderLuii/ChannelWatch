"""Deliver signed webhook notifications to configured external endpoints.

Webhook delivery builds a ChannelWatch payload with event metadata, DVR context,
and notification content, signs the JSON body with an HMAC secret, and retries
transient HTTP failures before reporting success or failure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx

from .. import __app_name__, __version__
from ..helpers.url_validator import build_safe_url_request, is_safe_url, redact_url
from ..helpers.trusted_destinations import (
    build_trusted_notification_request,
    is_trusted_notification_destination,
)
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE


class WebhookManager:
    """Manage enabled webhook endpoints and signed payload delivery.

    The manager normalizes webhook settings, derives event types from incoming
    notification data, attaches delivery identifiers and DVR fields, and sends
    each enabled endpoint with bounded retry behavior.
    """

    MAX_ATTEMPTS = 3
    REQUEST_TIMEOUT_SECONDS = 5
    BASE_RETRY_DELAY_SECONDS = 1
    MAX_DELIVERY_WORKERS = 5
    MASK_VALUE = "****"

    def __init__(self, settings: Any):
        self.settings = settings
        self.webhooks = self._normalize_webhooks(getattr(settings, "webhooks", []))
        self._client = httpx.Client(timeout=self.REQUEST_TIMEOUT_SECONDS)

    def is_configured(self) -> bool:
        """Return whether at least one webhook endpoint is enabled."""
        return any(self._is_enabled(webhook) for webhook in self.webhooks)

    def send_notification(self, title: str, message: str, **kwargs: Any) -> bool:
        """Send a notification payload to every enabled webhook.

        The title, message, and keyword metadata are converted into a signed
        payload. The return value is ``True`` when at least one enabled endpoint
        accepts the delivery.
        """
        enabled_webhooks = [
            webhook for webhook in self.webhooks if self._is_enabled(webhook)
        ]
        if not enabled_webhooks:
            return False

        event_type = self._determine_event_type(
            title=title, message=message, kwargs=kwargs
        )
        payload = self._build_payload(
            event_type=event_type, title=title, message=message, kwargs=kwargs
        )

        if len(enabled_webhooks) == 1:
            return self._deliver_webhook(enabled_webhooks[0], payload)

        max_workers = max(1, min(self.MAX_DELIVERY_WORKERS, len(enabled_webhooks)))
        overall_success = False
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._deliver_webhook, webhook, payload)
                for webhook in enabled_webhooks
            ]
            for future in as_completed(futures):
                try:
                    if future.result():
                        overall_success = True
                except Exception as exc:
                    log(
                        f"Webhook delivery worker failed: {exc.__class__.__name__}",
                        level=LOG_STANDARD,
                    )

        return overall_success

    def _normalize_webhooks(self, webhooks: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not isinstance(webhooks, list):
            return normalized

        for webhook in webhooks:
            if not isinstance(webhook, dict):
                continue

            normalized.append(
                {
                    "url": str(webhook.get("url", "") or "").strip(),
                    "secret": str(webhook.get("secret", "") or ""),
                    "enabled": bool(webhook.get("enabled", False)),
                }
            )

        return normalized

    def _is_enabled(self, webhook: Dict[str, Any]) -> bool:
        return bool(webhook.get("enabled")) and bool(
            str(webhook.get("url", "")).strip()
        )

    def _build_payload(
        self, event_type: str, title: str, message: str, kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        delivery_id = str(uuid.uuid4())
        image_url = kwargs.get("image_url")

        return {
            "eventType": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "instanceName": __app_name__,
            "instanceUrl": self._resolve_instance_url(),
            "version": __version__,
            "deliveryId": delivery_id,
            "dvr_id": str(kwargs.get("dvr_id", "") or ""),
            "dvr_name": str(kwargs.get("dvr_name", "") or ""),
            "data": {
                "title": title,
                "message": message,
                "imageUrl": image_url if image_url else None,
            },
        }

    def _resolve_instance_url(self) -> str:
        for env_name in ("CHANNELWATCH_INSTANCE_URL", "CW_INSTANCE_URL", "APP_URL"):
            value = str(os.environ.get(env_name, "") or "").strip()
            if value:
                return value
        return "http://localhost:8501"

    def _compute_signature(self, secret: str, body: bytes) -> str:
        return (
            "sha256="
            + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        )

    def _post(
        self,
        url: str,
        body: bytes,
        headers: Dict[str, str],
        *,
        sni_hostname: str | None = None,
    ) -> httpx.Response:
        extensions = {"sni_hostname": sni_hostname} if sni_hostname else None
        return self._client.post(
            url, content=body, headers=headers, extensions=extensions
        )

    def _deliver_webhook(
        self, webhook: Dict[str, Any], payload: Dict[str, Any]
    ) -> bool:
        url = str(webhook.get("url", "")).strip()
        redacted_url = redact_url(url)
        secret = str(webhook.get("secret", "") or "")
        trusted_destinations = getattr(
            self.settings, "trusted_notification_destinations", []
        )
        destination_is_safe = is_safe_url(url)
        destination_is_trusted = False

        if not destination_is_safe:
            destination_is_trusted = is_trusted_notification_destination(
                url,
                "webhook",
                trusted_destinations,
            )

        if not destination_is_safe and not destination_is_trusted:
            log(
                f"Webhook skipped for {redacted_url}: destination failed safety check",
                level=LOG_STANDARD,
            )
            return False
        if destination_is_trusted:
            log(
                f"Webhook delivery allowed for trusted local destination {redacted_url}",
                level=LOG_STANDARD,
            )

        if not secret or secret == self.MASK_VALUE:
            log(
                f"Webhook skipped for {redacted_url}: secret is missing",
                level=LOG_STANDARD,
            )
            return False

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        delivery_id = payload["deliveryId"]
        event_type = payload["eventType"]
        signature = self._compute_signature(secret, body)

        headers = {
            "Content-Type": "application/json",
            "X-ChannelWatch-Signature": signature,
            "X-ChannelWatch-Delivery": delivery_id,
            "X-ChannelWatch-Event": event_type,
        }

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            safe_request = (
                build_trusted_notification_request(
                    url,
                    "webhook",
                    trusted_destinations,
                )
                if destination_is_trusted
                else build_safe_url_request(url)
            )
            if safe_request is None:
                log(
                    f"Webhook skipped for {redacted_url}: destination failed safety check before delivery",
                    level=LOG_STANDARD,
                )
                return False
            try:
                delivery_headers = dict(headers)
                delivery_headers["Host"] = safe_request.host_header
                response = self._post(
                    safe_request.url,
                    body,
                    delivery_headers,
                    sni_hostname=safe_request.sni_hostname,
                )
                if 200 <= response.status_code < 300:
                    log(
                        f"Webhook delivered to {redacted_url} ({event_type})",
                        level=LOG_STANDARD,
                    )
                    return True

                log(
                    f"Webhook attempt {attempt}/{self.MAX_ATTEMPTS} failed for {redacted_url}: HTTP {response.status_code}",
                    level=LOG_STANDARD,
                )
            except httpx.TimeoutException:
                log(
                    f"Webhook attempt {attempt}/{self.MAX_ATTEMPTS} timed out for {redacted_url}",
                    level=LOG_STANDARD,
                )
            except httpx.RequestError as exc:
                log(
                    f"Webhook attempt {attempt}/{self.MAX_ATTEMPTS} failed for {redacted_url}: {exc.__class__.__name__}",
                    level=LOG_STANDARD,
                )

            if attempt < self.MAX_ATTEMPTS:
                delay = self.BASE_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                log(f"Retrying webhook in {delay}s", level=LOG_VERBOSE)
                time.sleep(delay)

        return False

    def _determine_event_type(
        self, title: str, message: str, kwargs: Dict[str, Any]
    ) -> str:
        normalized_title = title.lower()
        normalized_message = message.lower()

        if "[test]" in normalized_title:
            return "test"
        if "watching tv" in normalized_title:
            return "channel.watching.start"
        if "watching dvr content" in normalized_title:
            return "vod.playback.start"
        if "low disk space critical" in normalized_title:
            return "disk.space.critical"
        if "low disk space warning" in normalized_title:
            return "disk.space.warning"
        if "recording event" in normalized_title:
            if "scheduled" in normalized_message:
                return "recording.scheduled"
            if "started" in normalized_message:
                return "recording.started"
            if "cancelled" in normalized_message:
                return "recording.cancelled"
            if "completed" in normalized_message or "stopped" in normalized_message:
                return "recording.completed"
            return "recording.updated"
        if kwargs.get("image_url"):
            return "alert.notification"
        return "notification"
