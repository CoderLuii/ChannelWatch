import hashlib
import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import List, Optional

from ...helpers.logging import log, LOG_STANDARD
from .base import NotificationProvider

_DEFAULT_PLUGIN_DIR = "/config/plugins/notifications"
_PLUGIN_DIR_ENV = "CHANNELWATCH_PLUGIN_DIR"


def _resolve_dir(override: Optional[Path]) -> Path:
    if override is not None:
        return Path(override)
    return Path(os.environ.get(_PLUGIN_DIR_ENV, _DEFAULT_PLUGIN_DIR))


def load_notification_plugins(
    notification_manager,
    plugin_dir: Optional[Path] = None,
) -> List[str]:
    resolved = _resolve_dir(plugin_dir)

    if not resolved.exists():
        return []

    registered: List[str] = []

    for plugin_path in sorted(resolved.glob("*.py")):
        if plugin_path.name.startswith("_"):
            continue
        _load_single_plugin(plugin_path, notification_manager, registered)

    if registered:
        log(f"Plugin providers loaded: {', '.join(registered)}", LOG_STANDARD)

    return registered


def _load_single_plugin(
    path: Path,
    notification_manager,
    registered: List[str],
) -> None:
    path_hash = hashlib.md5(str(path).encode()).hexdigest()[:8]
    module_name = f"channelwatch_plugin_{path.stem}_{path_hash}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            log(f"Plugin skipped (no spec): {path.name}", LOG_STANDARD)
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        found = False
        for _attr, obj in inspect.getmembers(module, inspect.isclass):
            if (
                obj is NotificationProvider
                or not issubclass(obj, NotificationProvider)
                or inspect.isabstract(obj)
                or obj.__module__ != module_name
            ):
                continue

            try:
                instance = obj()
                if instance.initialize():
                    if notification_manager.register_provider(instance):
                        registered.append(obj.PROVIDER_TYPE)
                        log(
                            f"Plugin registered: {obj.PROVIDER_TYPE} ({path.name})",
                            LOG_STANDARD,
                        )
                        found = True
                    else:
                        log(
                            f"Plugin {obj.PROVIDER_TYPE} already registered, skipping ({path.name})",
                            LOG_STANDARD,
                        )
                else:
                    log(
                        f"Plugin {obj.PROVIDER_TYPE} initialize() returned False, skipping ({path.name})",
                        LOG_STANDARD,
                    )
            except Exception as exc:
                log(
                    f"Plugin instantiation/init error in {path.name}: {exc}",
                    LOG_STANDARD,
                )

        if not found:
            log(
                f"Plugin loaded but no concrete NotificationProvider subclass found: {path.name}",
                LOG_STANDARD,
            )

    except Exception as exc:
        log(f"Plugin import failed, skipping: {path.name}: {exc}", LOG_STANDARD)
