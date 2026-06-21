"""Multi-platform notification provider using Apprise library for service integration."""

import importlib
import ipaddress
from typing import Any, Optional, List
from urllib.parse import urlparse, urlunparse

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ...helpers.url_validator import redact_url, is_safe_url
from .base import NotificationProvider
from ...helpers.config import CoreSettings

# APPRISE PROVIDER


_HTTP_STYLE_APPRISE_SCHEMES = {
    "json": "http",
    "form": "http",
    "xml": "http",
    "jsons": "https",
    "forms": "https",
    "xmls": "https",
}


class AppriseProvider(NotificationProvider):
    """Integrates multiple notification services through the Apprise library."""

    PROVIDER_TYPE = "Apprise"
    DESCRIPTION = "Multi-platform notification service"

    SERVICE_MAP = {
        "apprise_pushover": "pover://{}",
        "apprise_discord": "discord://{}",
        "apprise_email": "mailto://{}",
        "apprise_telegram": "tgram://{}",
        "apprise_slack": "slack://{}",
        "apprise_gotify": "gotify://{}",
        "apprise_matrix": "matrix://{}",
        "apprise_custom": "{}",
    }

    def __init__(self):
        """Initializes Apprise provider with empty configuration."""
        self.apprise = None
        self.urls: List[str] = []
        self.url_entries: List[tuple] = []
        self.settings: Optional[CoreSettings] = None

    # CONFIGURATION

    def initialize(self, settings: CoreSettings, **kwargs) -> bool:
        """Configures Apprise with service URLs from application settings."""
        self.settings = settings
        try:
            apprise_module = importlib.import_module("apprise")
            self.apprise = apprise_module.Apprise()

            self.url_entries = self._collect_url_entries_from_settings()
            self.urls = [url for _, url in self.url_entries]

            for url in self.urls:
                add_result = self.apprise.add(url)
                if not add_result:
                    log(
                        f"Failed to add notification URL: {redact_url(url)}",
                        LOG_STANDARD,
                    )
                else:
                    service_type = url.split("://")[0] if "://" in url else "custom"
                    log(f"Added {service_type} service", LOG_VERBOSE)

            if self.is_configured():
                services = [url.split("://")[0] for url in self.urls if "://" in url]
                service_counts = {}
                for service in services:
                    service_counts[service] = service_counts.get(service, 0) + 1
                service_summary = ", ".join(
                    [f"{count} {name}" for name, count in service_counts.items()]
                )
                log(f"Notification services ready: {service_summary}", LOG_VERBOSE)
                return True

            log("No valid notification services configured", LOG_STANDARD)
            return False

        except ImportError:
            log("Apprise package not installed. Run: pip install apprise")
            return False
        except Exception as e:
            destination_summary = self._destination_summary(self.urls)
            log(
                f"Error initializing notification services ({destination_summary}): {type(e).__name__}"
            )
            return False

    def _collect_url_entries_from_settings(self) -> List[tuple]:
        if not self.settings:
            return []

        entries: List[tuple] = []
        settings = self.settings

        for setting_attr, url_template in self.SERVICE_MAP.items():
            dest_key = setting_attr.removeprefix("apprise_")
            value = getattr(settings, setting_attr, "")
            if value and isinstance(value, str):
                if (
                    setting_attr == "apprise_email"
                    and "=" in value
                    and "://" not in value
                    and (
                        value.strip().startswith(("user=", "pass=", "smtp=", "port="))
                        or any(
                            param in value
                            for param in ["user=", "pass=", "smtp=", "port="]
                        )
                    )
                ):
                    if "from=" not in value:
                        url = f"mailtos://_?{value}&from=ChannelWatch"
                    else:
                        url = f"mailtos://_?{value}"

                elif setting_attr == "apprise_discord" and (
                    "discord.com/api/webhooks/" in value
                    or "discordapp.com/api/webhooks/" in value
                ):
                    try:
                        parts = value.split("/api/webhooks/")
                        if len(parts) == 2 and "/" in parts[1]:
                            webhook_parts = parts[1].split("/", 1)
                            if len(webhook_parts) >= 2:
                                webhook_id, webhook_token = (
                                    webhook_parts[0],
                                    webhook_parts[1],
                                )
                                if "?" in webhook_token:
                                    webhook_token = webhook_token.split("?", 1)[0]
                                url = f"discord://{webhook_id}/{webhook_token}"
                            else:
                                url = url_template.format(value)
                                log(
                                    "Could not extract token from Discord webhook URL",
                                    LOG_STANDARD,
                                )
                        else:
                            url = url_template.format(value)
                            log("Invalid Discord webhook URL format", LOG_STANDARD)
                    except Exception as e:
                        log(f"Error parsing Discord webhook URL: {e}", LOG_STANDARD)
                        url = url_template.format(value)
                elif setting_attr == "apprise_custom" and "://" in value:
                    url = value
                else:
                    url = url_template.format(value)
                    if setting_attr == "apprise_email" and "from=" not in url:
                        separator = "&" if "?" in url else "?"
                        url = f"{url}{separator}from=ChannelWatch"
                        log("Added ChannelWatch as sender name for email", LOG_VERBOSE)
                entries.append((dest_key, url))

        email_to = settings.apprise_email_to
        if email_to:
            updated = []
            found_mailto = False
            for dest_key, url in entries:
                if url.startswith(("mailto://", "mailtos://")):
                    separator = "&" if "?" in url else "?"
                    updated.append((dest_key, f"{url}{separator}to={email_to}"))
                    found_mailto = True
                else:
                    updated.append((dest_key, url))
            if found_mailto:
                entries = updated

        return entries

    def is_configured(self) -> bool:
        """Verifies that Apprise is initialized with at least one service URL."""
        return bool(self.apprise and self.urls)

    def _destination_summary(self, urls: List[str]) -> str:
        service_counts: dict[str, int] = {}
        for url in urls:
            service_type = url.split("://", 1)[0] if "://" in url else "custom"
            service_counts[service_type] = service_counts.get(service_type, 0) + 1
        if not service_counts:
            return "none"
        return ", ".join(
            f"{count} {service_type}" for service_type, count in service_counts.items()
        )

    def _is_safe_custom_destination(self, dest_key: str, url: str) -> bool:
        """Reject HTTP-style custom Apprise destinations that fail SSRF policy."""
        if dest_key != "custom":
            return True

        try:
            parsed = urlparse(url)
        except Exception:
            return False

        http_scheme = _HTTP_STYLE_APPRISE_SCHEMES.get(parsed.scheme.lower())
        if not http_scheme:
            return True

        if not parsed.hostname:
            log(
                f"SSRF: dropping Apprise custom destination: {redact_url(url)} (reason: missing host)",
                LOG_STANDARD,
            )
            return False

        validation_url = urlunparse(
            (
                http_scheme,
                parsed.netloc,
                parsed.path or "/",
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        if is_safe_url(validation_url):
            return True

        log(
            f"SSRF: dropping Apprise custom destination: {redact_url(url)} (reason: blocked by is_safe_url destination policy)",
            LOG_STANDARD,
        )
        return False

    def _apprise_attach_url(self, image_url: Optional[str]) -> Optional[str]:
        """Return an image URL only when Apprise can fetch it without DNS rebinding."""
        if not image_url:
            return None
        try:
            parsed = urlparse(image_url)
            hostname = parsed.hostname or ""
            addr = ipaddress.ip_address(hostname)
        except ValueError:
            log(
                f"SSRF: dropping image_url from Apprise attachment: {redact_url(image_url)} (reason: DNS-bound attachment fetch is not supported)",
                LOG_STANDARD,
            )
            return None
        except Exception:
            return None
        if not addr.is_global:
            log(
                f"SSRF: dropping image_url from Apprise attachment: {redact_url(image_url)} (reason: blocked by literal IP policy)",
                LOG_STANDARD,
            )
            return None
        return image_url

    # NOTIFICATION DELIVERY

    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        log(f"Sending notification: {title}", level=LOG_VERBOSE)
        if not self.is_configured():
            log("No notification services configured", level=LOG_VERBOSE)
            return False

        allowed: Optional[set] = kwargs.get("allowed_apprise_destinations")

        active_entries = [
            (dest_key, url)
            for dest_key, url in self.url_entries
            if allowed is None or dest_key in allowed
        ]

        if not active_entries:
            log(
                "All Apprise destinations suppressed by routing config",
                level=LOG_VERBOSE,
            )
            return False

        success = False
        try:
            image_url = kwargs.get("image_url")
            if image_url and not is_safe_url(image_url):
                log(
                    f"SSRF: dropping image_url from notification: {redact_url(image_url)} (reason: blocked by is_safe_url policy)",
                    LOG_STANDARD,
                )
                image_url = None
            discord_urls = []
            other_urls = []

            for dest_key, url in active_entries:
                if not self._is_safe_custom_destination(dest_key, url):
                    continue
                if url.startswith("discord://"):
                    discord_urls.append(url)
                else:
                    other_urls.append(url)

            apprise_module = importlib.import_module("apprise")
            discord_success = False
            other_success = False
            if discord_urls:
                try:
                    try:
                        import httpx
                    except ImportError:
                        log(
                            "httpx library not available, using Apprise fallback for Discord",
                            level=LOG_STANDARD,
                        )
                        discord_message = message
                        discord_apprise = apprise_module.Apprise()
                        for url in discord_urls:
                            discord_apprise.add(url)

                        try:
                            body_format = (
                                apprise_module.NotifyFormat.TEXT
                                if hasattr(apprise_module, "NotifyFormat")
                                else None
                            )
                        except (ImportError, AttributeError):
                            body_format = None
                        apprise_image_url = self._apprise_attach_url(image_url)
                        discord_success = discord_apprise.notify(
                            title=title,
                            body=discord_message,
                            body_format=body_format,
                            attach=[apprise_image_url] if apprise_image_url else None,
                        )
                        if discord_success:
                            log(
                                "Discord notification sent via Apprise fallback",
                                level=LOG_VERBOSE,
                            )
                    else:
                        for discord_url in discord_urls:
                            if (
                                discord_url.startswith("discord://")
                                and "/" in discord_url[10:]
                            ):
                                parts = discord_url[10:].split("/", 1)
                                if len(parts) == 2:
                                    webhook_id, webhook_token = parts
                                    webhook_url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"
                                    embed: dict[str, Any] = {
                                        "title": title,
                                        "description": message,
                                        "color": 3447003,
                                    }
                                    if image_url and not is_safe_url(image_url):
                                        log(
                                            f"SSRF: dropping image_url from notification: {redact_url(image_url)} (reason: blocked by delivery-time is_safe_url policy)",
                                            LOG_STANDARD,
                                        )
                                        image_url = None
                                    if image_url:
                                        embed["image"] = {"url": image_url}

                                    payload = {
                                        "username": "ChannelWatch Bot",
                                        "content": "",
                                        "embeds": [embed],
                                    }

                                    log(
                                        "Sending Discord notification",
                                        level=LOG_VERBOSE,
                                    )
                                    try:
                                        response = httpx.post(
                                            webhook_url, json=payload, timeout=10
                                        )
                                    except (
                                        httpx.RequestError,
                                        httpx.TimeoutException,
                                    ) as e:
                                        log(
                                            f"Error sending Discord notification to {redact_url(webhook_url)}: {type(e).__name__}",
                                            level=LOG_STANDARD,
                                        )
                                        continue
                                    except Exception as e:
                                        log(
                                            f"Error sending Discord notification: {type(e).__name__}",
                                            level=LOG_STANDARD,
                                        )
                                        continue

                                    if response.status_code == 204:
                                        discord_success = True
                                        log(
                                            "Discord notification sent successfully",
                                            level=LOG_VERBOSE,
                                        )
                                    else:
                                        log(
                                            f"Discord notification failed: {response.status_code} {response.text}",
                                            level=LOG_STANDARD,
                                        )
                except Exception as e:
                    log(
                        f"Discord notification error: {type(e).__name__}",
                        level=LOG_STANDARD,
                    )
                    discord_success = False
            if other_urls:
                try:
                    try:
                        body_format = (
                            apprise_module.NotifyFormat.HTML
                            if "NotifyFormat" in dir(apprise_module)
                            else None
                        )
                        html_message = message.replace("\n", "<br />")
                    except (ImportError, AttributeError):
                        body_format = None
                        html_message = message
                    other_apprise = apprise_module.Apprise()
                    for url in other_urls:
                        other_apprise.add(url)
                    if image_url and not is_safe_url(image_url):
                        log(
                            f"SSRF: dropping image_url from notification: {redact_url(image_url)} (reason: blocked by delivery-time is_safe_url policy)",
                            LOG_STANDARD,
                        )
                        image_url = None
                    apprise_image_url = self._apprise_attach_url(image_url)
                    attach = [apprise_image_url] if apprise_image_url else None
                    other_success = other_apprise.notify(
                        title=title,
                        body=html_message,
                        attach=attach,
                        body_format=body_format,
                    )
                    if other_success:
                        log(
                            "Other notification services: delivery successful",
                            level=LOG_VERBOSE,
                        )
                    else:
                        log(
                            "Other notification services: delivery failed",
                            level=LOG_STANDARD,
                        )
                except Exception as e:
                    destination_summary = self._destination_summary(other_urls)
                    log(
                        f"Error with other notification services ({destination_summary}): {type(e).__name__}",
                        level=LOG_STANDARD,
                    )
                    other_success = False
            success = discord_success or other_success

            if success:
                log("Notification sent successfully", level=LOG_VERBOSE)
            else:
                log("All notification services failed", level=LOG_STANDARD)

        except Exception as e:
            log(f"Notification error: {type(e).__name__}", level=LOG_STANDARD)
            success = False

        return success
