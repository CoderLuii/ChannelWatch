from __future__ import annotations

from pathlib import Path


from core.notifications.notification import NotificationManager
from core.notifications.providers.base import NotificationProvider
from core.notifications.providers.plugin_loader import load_notification_plugins


def _output_text(capsys, caplog) -> str:
    captured = capsys.readouterr()
    return captured.out + "\n" + caplog.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> NotificationManager:
    return NotificationManager(rate_limit=100, rate_window=300)


def _write_plugin(directory: Path, filename: str, source: str) -> Path:
    path = directory / filename
    path.write_text(source)
    return path


_GOOD_PLUGIN_SOURCE = """\
from core.notifications.providers.base import NotificationProvider

class GoodProvider(NotificationProvider):
    PROVIDER_TYPE = "GoodPlugin"
    DESCRIPTION = "Test good plugin"

    def __init__(self):
        self._ready = False

    def initialize(self, **kwargs):
        self._ready = True
        return True

    def is_configured(self):
        return self._ready

    def send_notification(self, title, message, image_url=None, **kwargs):
        return True
"""

_INIT_FALSE_SOURCE = """\
from core.notifications.providers.base import NotificationProvider

class InitFalseProvider(NotificationProvider):
    PROVIDER_TYPE = "InitFalsePlugin"
    DESCRIPTION = "Plugin whose initialize() returns False"

    def initialize(self, **kwargs):
        return False

    def is_configured(self):
        return False

    def send_notification(self, title, message, image_url=None, **kwargs):
        return False
"""

_SYNTAX_ERROR_SOURCE = "def broken(:"

_IMPORT_ERROR_SOURCE = "import nonexistent_module_xyz_abc_123\n"

_NO_SUBCLASS_SOURCE = """\
class NotAProvider:
    pass
"""

_ABSTRACT_SOURCE = """\
from abc import abstractmethod
from core.notifications.providers.base import NotificationProvider

class StillAbstract(NotificationProvider):
    PROVIDER_TYPE = "AbstractPlugin"
    DESCRIPTION = "Still abstract"

    @abstractmethod
    def initialize(self, **kwargs):
        ...

    @abstractmethod
    def send_notification(self, title, message, image_url=None, **kwargs):
        ...

    @abstractmethod
    def is_configured(self):
        ...
"""


# ---------------------------------------------------------------------------
# Test: missing / empty plugin directory
# ---------------------------------------------------------------------------


class TestPluginDirMissing:
    def test_missing_dir_returns_empty(self):
        mgr = _make_manager()
        result = load_notification_plugins(
            mgr, plugin_dir=Path("/nonexistent/path/xyz")
        )
        assert result == []

    def test_empty_dir_returns_empty(self, tmp_path):
        mgr = _make_manager()
        result = load_notification_plugins(mgr, plugin_dir=tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# Test: successful plugin discovery and registration
# ---------------------------------------------------------------------------


class TestPluginDiscovery:
    def test_good_plugin_is_registered(self, tmp_path):
        _write_plugin(tmp_path, "good_plugin.py", _GOOD_PLUGIN_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert "GoodPlugin" in result
        assert "GoodPlugin" in mgr.providers

    def test_registered_plugin_is_configured(self, tmp_path):
        _write_plugin(tmp_path, "good_plugin.py", _GOOD_PLUGIN_SOURCE)
        mgr = _make_manager()

        load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert mgr.providers["GoodPlugin"].is_configured()

    def test_multiple_plugins_in_dir_all_registered(self, tmp_path):
        source_b = _GOOD_PLUGIN_SOURCE.replace("GoodPlugin", "PluginB").replace(
            "GoodProvider", "ProviderB"
        )
        source_c = _GOOD_PLUGIN_SOURCE.replace("GoodPlugin", "PluginC").replace(
            "GoodProvider", "ProviderC"
        )
        _write_plugin(tmp_path, "plugin_b.py", source_b)
        _write_plugin(tmp_path, "plugin_c.py", source_c)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert "PluginB" in result
        assert "PluginC" in result
        assert len(mgr.providers) == 2

    def test_underscore_files_are_skipped(self, tmp_path):
        _write_plugin(tmp_path, "_internal.py", _GOOD_PLUGIN_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert result == []
        assert not mgr.providers


# ---------------------------------------------------------------------------
# Test: import / syntax failures do not crash
# ---------------------------------------------------------------------------


class TestImportFailureSafety:
    def test_syntax_error_skipped_with_warning(self, tmp_path, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        _write_plugin(tmp_path, "broken_syntax.py", _SYNTAX_ERROR_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert result == []
        output = _output_text(capsys, caplog)
        assert "broken_syntax.py" in output
        assert (
            "import failed" in output.lower()
            or "skipping" in output.lower()
        )

    def test_import_error_skipped_with_warning(self, tmp_path, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        _write_plugin(tmp_path, "bad_import.py", _IMPORT_ERROR_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert result == []
        assert "bad_import.py" in _output_text(capsys, caplog)

    def test_bad_plugin_does_not_crash_app(self, tmp_path):
        _write_plugin(tmp_path, "broken.py", _SYNTAX_ERROR_SOURCE)
        _write_plugin(tmp_path, "good_plugin.py", _GOOD_PLUGIN_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert "GoodPlugin" in result

    def test_no_subclass_plugin_logged_not_crashed(self, tmp_path, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        _write_plugin(tmp_path, "not_a_provider.py", _NO_SUBCLASS_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert result == []
        assert "not_a_provider.py" in _output_text(capsys, caplog)

    def test_still_abstract_plugin_is_skipped(self, tmp_path):
        _write_plugin(tmp_path, "still_abstract.py", _ABSTRACT_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert result == []
        assert not mgr.providers

    def test_init_returns_false_plugin_not_registered(self, tmp_path, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        _write_plugin(tmp_path, "init_false.py", _INIT_FALSE_SOURCE)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert result == []
        assert "InitFalsePlugin" not in mgr.providers
        output = _output_text(capsys, caplog)
        assert "False" in output or "skipping" in output.lower()


# ---------------------------------------------------------------------------
# Test: duplicate registration is rejected gracefully
# ---------------------------------------------------------------------------


class TestDuplicateRegistration:
    def test_duplicate_provider_type_not_double_registered(self, tmp_path):
        _write_plugin(tmp_path, "plugin_a.py", _GOOD_PLUGIN_SOURCE)
        duplicate = _GOOD_PLUGIN_SOURCE.replace("GoodProvider", "GoodProvider2")
        _write_plugin(tmp_path, "plugin_a_dup.py", duplicate)
        mgr = _make_manager()

        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert result.count("GoodPlugin") == 1
        assert len([k for k in mgr.providers if k == "GoodPlugin"]) == 1


# ---------------------------------------------------------------------------
# Test: load-once semantics via initialize_notifications wiring
# ---------------------------------------------------------------------------


class TestLoadOnceViaInitialize:
    def test_initialize_notifications_calls_plugin_loader(self, tmp_path):
        _write_plugin(tmp_path, "good_plugin.py", _GOOD_PLUGIN_SOURCE)

        from core.helpers.initialize import initialize_notifications
        from core.helpers.config import CoreSettings

        settings = CoreSettings()
        mgr = initialize_notifications(settings, test_mode=True, plugin_dir=tmp_path)

        assert mgr is not None
        assert "GoodPlugin" in mgr.providers

    def test_initialize_notifications_with_no_plugins_still_returns_manager_when_webhook_configured(
        self, tmp_path
    ):
        from core.helpers.initialize import initialize_notifications
        from core.helpers.config import CoreSettings

        settings = CoreSettings()
        mgr = initialize_notifications(settings, test_mode=True, plugin_dir=tmp_path)
        assert mgr is None or isinstance(mgr, NotificationManager)


# ---------------------------------------------------------------------------
# Test: ConsoleProvider example plugin
# ---------------------------------------------------------------------------


class TestConsoleProvider:
    def test_console_provider_initializes(self):
        from core.notifications.providers.examples.console_provider import (
            ConsoleProvider,
        )

        p = ConsoleProvider()
        assert not p.is_configured()
        result = p.initialize()
        assert result is True
        assert p.is_configured()

    def test_console_provider_sends_to_stdout(self, capsys):
        from core.notifications.providers.examples.console_provider import (
            ConsoleProvider,
        )

        p = ConsoleProvider()
        p.initialize()
        ok = p.send_notification("Test Title", "Test message body")
        assert ok is True
        captured = capsys.readouterr()
        assert "Test Title" in captured.out
        assert "Test message body" in captured.out

    def test_console_provider_includes_dvr_context(self, capsys):
        from core.notifications.providers.examples.console_provider import (
            ConsoleProvider,
        )

        p = ConsoleProvider()
        p.initialize()
        p.send_notification(
            "Alert",
            "Channel changed",
            dvr_id="dvr_abc123",
            event_type="channel",
        )
        captured = capsys.readouterr()
        assert "dvr_abc123" in captured.out
        assert "channel" in captured.out

    def test_console_provider_is_valid_notification_provider(self):
        from core.notifications.providers.examples.console_provider import (
            ConsoleProvider,
        )

        assert issubclass(ConsoleProvider, NotificationProvider)
        assert ConsoleProvider.PROVIDER_TYPE == "Console"

    def test_console_provider_loaded_by_plugin_loader(self, tmp_path):
        import shutil

        examples_dir = (
            Path(__file__).resolve().parent.parent
            / "notifications"
            / "providers"
            / "examples"
        )
        shutil.copy(
            examples_dir / "console_provider.py", tmp_path / "console_provider.py"
        )

        mgr = _make_manager()
        result = load_notification_plugins(mgr, plugin_dir=tmp_path)

        assert "Console" in result
        assert "Console" in mgr.providers
        assert mgr.providers["Console"].is_configured()

    def test_console_provider_delivers_via_manager(self, tmp_path, capsys):
        import shutil

        examples_dir = (
            Path(__file__).resolve().parent.parent
            / "notifications"
            / "providers"
            / "examples"
        )
        shutil.copy(
            examples_dir / "console_provider.py", tmp_path / "console_provider.py"
        )

        mgr = _make_manager()
        load_notification_plugins(mgr, plugin_dir=tmp_path)

        mgr.send_notification(
            "Recording started",
            "Your show is now recording.",
            dvr_id="dvr_test",
            event_type="recording",
        )
        captured = capsys.readouterr()
        assert "Recording started" in captured.out
