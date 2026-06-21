# Notification provider plugins

ChannelWatch supports third-party notification providers through a Python file-based plugin loader.

Alert-source plugins are not part of the v0.9 runtime plugin system. A stable `AlertSource` interface preview now ships for plugin authors targeting v1.1, but there is no dynamic loading, registration, or runtime integration for alert-source plugins yet.

If you drop a valid provider module into `/config/plugins/notifications/`, ChannelWatch will try to import it during startup and register it alongside the built-in providers.

## What plugins can and cannot do

Plugins are for notification delivery only.

They can:

- register a new delivery provider type
- receive the final notification payload
- read their own config from environment variables or other local files you control

They cannot:

- replace the built-in routing engine
- receive database handles
- receive the encryption key
- receive raw ChannelWatch secrets as arguments from the loader
- hot-reload while the app is already running

## File locations

| Location | Purpose |
|---|---|
| `/config/plugins/notifications/*.py` | Runtime plugin directory |
| `app/core/notifications/providers/base.py` | `NotificationProvider` abstract base class |
| `app/core/notifications/providers/base.py` | `AlertSource` v1.1 stable preview interface |
| `app/core/notifications/providers/plugin_loader.py` | Loader implementation |
| `app/core/notifications/providers/examples/console_provider.py` | Shipped reference plugin |
| `app/core/notifications/providers/examples/custom_alert_source.py` | Shipped alert-source preview example |

For testing, you can override the scan directory:

```bash
CHANNELWATCH_PLUGIN_DIR=/my/test/dir
```

## Loader behavior

ChannelWatch loads plugins once during startup.

Current loader behavior:

- scans `*.py` files in sorted order
- skips filenames that start with `_`
- imports each file with a unique module name
- looks for concrete subclasses of `NotificationProvider`
- instantiates the class with no constructor arguments
- calls `initialize()` with no keyword arguments
- registers the provider only if `initialize()` returns `True`
- logs and skips failures instead of crashing the whole app

Hot reload is not supported. Restart the container after you add or change a plugin.

## Provider ABC

Every plugin must subclass `NotificationProvider` and implement all three abstract methods.

```python
from typing import Optional

from core.notifications.providers.base import NotificationProvider


class MyProvider(NotificationProvider):
    PROVIDER_TYPE = "MyService"
    DESCRIPTION = "My custom notification provider"

    def initialize(self, **kwargs) -> bool:
        # Loader passes no kwargs.
        # Read your own config here.
        return True

    def is_configured(self) -> bool:
        return True

    def send_notification(
        self,
        title: str,
        message: str,
        image_url: Optional[str] = None,
        **kwargs,
    ) -> bool:
        # Safe kwargs: dvr_id, dvr_name, event_type
        return True
```

### Required class members

| Name | Required | Meaning |
|---|---|---|
| `PROVIDER_TYPE` | Yes | Unique registry key |
| `DESCRIPTION` | No | Human-facing description for logs |
| `initialize()` | Yes | Startup initialization hook |
| `is_configured()` | Yes | Runtime readiness check |
| `send_notification()` | Yes | Delivery method |

## Alert-source preview for v1.1

ChannelWatch v0.9 does not load alert-source plugins at runtime.

The `AlertSource` abstract base class in `app/core/notifications/providers/base.py` is a stable interface preview for the planned v1.1 alert-source plugin API. You can prototype against this contract now, but ChannelWatch will not auto-discover or execute alert-source plugins until that future release work lands.

```python
from typing import Any

from core.notifications.providers.base import AlertSource


class MyAlertSource(AlertSource):
    SOURCE_TYPE = "MyAlertSource"
    DESCRIPTION = "My future alert-source plugin"

    def subscribe(self, callback) -> bool:
        return True

    def emit_event(self, event: dict[str, Any]) -> bool:
        return True

    def unsubscribe(self) -> bool:
        return True
```

### Preview contract

| Name | Required | Meaning |
|---|---|---|
| `SOURCE_TYPE` | Yes | Stable source identifier |
| `DESCRIPTION` | No | Human-facing description |
| `subscribe()` | Yes | Attach to the source event stream |
| `emit_event()` | Yes | Forward one normalized event to subscribers |
| `unsubscribe()` | Yes | Disconnect and clean up the subscription |

See `app/core/notifications/providers/examples/custom_alert_source.py` for a runnable mock event-stream example.

## Safe input contract

The loader and notification manager intentionally keep the plugin contract narrow.

### `initialize()`

`initialize()` is called with zero keyword arguments.

That means:

- no credentials are injected for you
- no settings object is handed in
- no database or storage object is passed in

If your plugin needs configuration, read it from your own environment variables or local files.

### `send_notification()`

The delivery call includes:

- `title`
- `message`
- `image_url`
- safe context kwargs: `dvr_id`, `dvr_name`, `event_type`

Do not rely on any other keyword argument being present.

## Collision rules

Provider type collisions are rejected.

| Condition | Result |
|---|---|
| `PROVIDER_TYPE` matches a built-in provider such as `Apprise` | plugin is skipped |
| `PROVIDER_TYPE` matches an already registered plugin | later plugin is skipped |
| two plugin files define the same provider type | first file in sort order wins |

## Failure behavior

A broken plugin should not take down the rest of ChannelWatch.

If a plugin file:

- fails to import
- contains no concrete provider subclass
- raises during initialization
- returns `False` from `initialize()`

the loader logs the failure and moves on.

This is deliberate. Built-in providers and other healthy plugins still load.

## Example plugin

ChannelWatch ships a working example at `app/core/notifications/providers/examples/console_provider.py`.

Minimal example:

```python
import sys
from typing import Optional

from core.notifications.providers.base import NotificationProvider


class ConsoleProvider(NotificationProvider):
    PROVIDER_TYPE = "Console"
    DESCRIPTION = "Writes notifications to stdout"

    def __init__(self) -> None:
        self._configured = False

    def initialize(self, **kwargs) -> bool:
        self._configured = True
        return True

    def is_configured(self) -> bool:
        return self._configured

    def send_notification(
        self,
        title: str,
        message: str,
        image_url: Optional[str] = None,
        **kwargs,
    ) -> bool:
        try:
            dvr_id = kwargs.get("dvr_id", "")
            event_type = kwargs.get("event_type", "")
            prefix = f"[{dvr_id}/{event_type}] " if dvr_id and event_type else ""
            print(f"[ConsoleProvider] {prefix}{title}: {message}", file=sys.stdout, flush=True)
            return True
        except Exception:
            return False
```

## Local test flow

You can test a plugin without booting the full app by calling the loader directly.

```python
from pathlib import Path

from core.notifications.notification import NotificationManager
from core.notifications.providers.plugin_loader import load_notification_plugins

manager = NotificationManager()
registered = load_notification_plugins(manager, plugin_dir=Path("/my/plugin/dir"))
print(registered)

ok = manager.send_notification(
    "Test title",
    "Hello from my plugin",
    dvr_id="dvr_abc12345",
    dvr_name="Living Room DVR",
    event_type="watching_channel",
)
print(ok)
```

## Practical advice for plugin authors

- pick a `PROVIDER_TYPE` that will not collide with built-ins or other local plugins
- keep `initialize()` cheap and deterministic
- return `False` when config is missing instead of crashing
- treat `image_url` as optional
- log clearly on failure, but do not print secrets
- restart the container after each plugin change

## Security notes

Plugins run as local Python code inside your ChannelWatch container. That means you should treat them as trusted code.

ChannelWatch narrows what the loader passes into the plugin, but a plugin still executes inside your own runtime. Only install plugin code you trust.

See [`.github/SECURITY.md`](../../.github/SECURITY.md) for the broader deployment threat model.

## See also

- [`docs/explanation/architecture.md`](../explanation/architecture.md) for broader context on ChannelWatch's extension surface and runtime architecture.
