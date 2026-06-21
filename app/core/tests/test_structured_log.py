import json
import logging
import os
import tempfile
from unittest.mock import patch, MagicMock


from core.helpers.structured_log import (
    JsonFormatter,
    set_log_context,
    clear_log_context,
    log_context,
    _ctx_dvr_id,
)


def _make_record(msg="test message", name="channelwatch", **extra_attrs):
    record = logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
        func="",
    )
    for k, v in extra_attrs.items():
        setattr(record, k, v)
    return record


class TestJsonFormatterBaseFields:
    def test_required_keys_present(self):
        formatter = JsonFormatter()
        record = _make_record("hello world")
        output = json.loads(formatter.format(record))
        assert "timestamp" in output
        assert "level" in output
        assert "module" in output
        assert "message" in output

    def test_message_value(self):
        formatter = JsonFormatter()
        record = _make_record("startup complete")
        output = json.loads(formatter.format(record))
        assert output["message"] == "startup complete"

    def test_level_value(self):
        formatter = JsonFormatter()
        record = _make_record()
        output = json.loads(formatter.format(record))
        assert output["level"] == "INFO"

    def test_module_uses_logger_name(self):
        formatter = JsonFormatter()
        record = _make_record(name="core.helpers.logging")
        output = json.loads(formatter.format(record))
        assert output["module"] == "core.helpers.logging"

    def test_timestamp_is_iso8601(self):
        from datetime import datetime

        formatter = JsonFormatter()
        record = _make_record()
        output = json.loads(formatter.format(record))
        parsed = datetime.fromisoformat(output["timestamp"])
        assert parsed.tzinfo is not None

    def test_no_spurious_context_keys_without_context(self):
        clear_log_context()
        formatter = JsonFormatter()
        record = _make_record()
        output = json.loads(formatter.format(record))
        assert "dvr_id" not in output
        assert "request_id" not in output
        assert "user_id" not in output

    def test_output_is_single_line(self):
        formatter = JsonFormatter()
        record = _make_record("no newlines please")
        result = formatter.format(record)
        assert "\n" not in result


class TestJsonFormatterExtraFields:
    def test_dvr_id_from_extra(self):
        formatter = JsonFormatter()
        record = _make_record(dvr_id="dvr_abc123")
        output = json.loads(formatter.format(record))
        assert output["dvr_id"] == "dvr_abc123"

    def test_request_id_from_extra(self):
        formatter = JsonFormatter()
        record = _make_record(request_id="req-xyz-789")
        output = json.loads(formatter.format(record))
        assert output["request_id"] == "req-xyz-789"

    def test_user_id_from_extra(self):
        formatter = JsonFormatter()
        record = _make_record(user_id="user_42")
        output = json.loads(formatter.format(record))
        assert output["user_id"] == "user_42"

    def test_all_context_fields_together(self):
        formatter = JsonFormatter()
        record = _make_record(dvr_id="d1", request_id="r1", user_id="u1")
        output = json.loads(formatter.format(record))
        assert output["dvr_id"] == "d1"
        assert output["request_id"] == "r1"
        assert output["user_id"] == "u1"


class TestJsonFormatterContextVars:
    def setup_method(self):
        clear_log_context()

    def teardown_method(self):
        clear_log_context()

    def test_dvr_id_from_contextvar(self):
        set_log_context(dvr_id="ctx_dvr")
        formatter = JsonFormatter()
        record = _make_record()
        output = json.loads(formatter.format(record))
        assert output["dvr_id"] == "ctx_dvr"

    def test_request_id_from_contextvar(self):
        set_log_context(request_id="ctx_req")
        formatter = JsonFormatter()
        record = _make_record()
        output = json.loads(formatter.format(record))
        assert output["request_id"] == "ctx_req"

    def test_user_id_from_contextvar(self):
        set_log_context(user_id="ctx_user")
        formatter = JsonFormatter()
        record = _make_record()
        output = json.loads(formatter.format(record))
        assert output["user_id"] == "ctx_user"

    def test_extra_overrides_contextvar(self):
        set_log_context(dvr_id="ctx_dvr")
        formatter = JsonFormatter()
        record = _make_record(dvr_id="extra_dvr")
        output = json.loads(formatter.format(record))
        assert output["dvr_id"] == "extra_dvr"

    def test_clear_removes_contextvar_fields(self):
        set_log_context(dvr_id="will_be_cleared")
        clear_log_context()
        formatter = JsonFormatter()
        record = _make_record()
        output = json.loads(formatter.format(record))
        assert "dvr_id" not in output


class TestContextHelpers:
    def setup_method(self):
        clear_log_context()

    def teardown_method(self):
        clear_log_context()

    def test_set_and_clear_with_tokens(self):
        tokens = set_log_context(dvr_id="temp")
        assert _ctx_dvr_id.get() == "temp"
        clear_log_context(tokens)
        assert _ctx_dvr_id.get() is None

    def test_nested_set_restores_outer(self):
        outer = set_log_context(dvr_id="outer")
        inner = set_log_context(dvr_id="inner")
        assert _ctx_dvr_id.get() == "inner"
        clear_log_context(inner)
        assert _ctx_dvr_id.get() == "outer"
        clear_log_context(outer)
        assert _ctx_dvr_id.get() is None

    def test_log_context_manager_sets_and_clears(self):
        with log_context(dvr_id="ctx_mgr"):
            assert _ctx_dvr_id.get() == "ctx_mgr"
        assert _ctx_dvr_id.get() is None

    def test_log_context_manager_restores_on_exception(self):
        try:
            with log_context(dvr_id="error_ctx"):
                assert _ctx_dvr_id.get() == "error_ctx"
                raise ValueError("test error")
        except ValueError:
            pass
        assert _ctx_dvr_id.get() is None

    def test_unknown_kwargs_ignored_by_log_context(self):
        with log_context(dvr_id="ok", unknown_field="ignored"):
            assert _ctx_dvr_id.get() == "ok"


class TestSetupLoggingJsonMode:
    def test_json_mode_attaches_json_formatter_to_file_handler(self):
        from core.helpers.logging import setup_logging
        from core.helpers.structured_log import JsonFormatter

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"LOG_FORMAT": "json"}):
                root = logging.getLogger()
                original_handlers = list(root.handlers)
                try:
                    setup_logging(tmpdir, retention_days=1)
                    file_handlers = [
                        h for h in root.handlers if hasattr(h, "baseFilename")
                    ]
                    assert file_handlers, "Expected at least one file handler"
                    assert isinstance(file_handlers[-1].formatter, JsonFormatter)
                finally:
                    for h in list(root.handlers):
                        if h not in original_handlers:
                            root.removeHandler(h)
                            try:
                                h.close()
                            except Exception:
                                pass

    def test_text_mode_attaches_text_formatter(self):
        from core.helpers.logging import setup_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"LOG_FORMAT": "text"}):
                root = logging.getLogger()
                original_handlers = list(root.handlers)
                try:
                    setup_logging(tmpdir, retention_days=1)
                    file_handlers = [
                        h for h in root.handlers if hasattr(h, "baseFilename")
                    ]
                    assert file_handlers
                    assert not isinstance(
                        file_handlers[-1].formatter,
                        __import__(
                            "core.helpers.structured_log", fromlist=["JsonFormatter"]
                        ).JsonFormatter,
                    )
                finally:
                    for h in list(root.handlers):
                        if h not in original_handlers:
                            root.removeHandler(h)
                            try:
                                h.close()
                            except Exception:
                                pass


class TestLogFunctionModes:
    def setup_method(self):
        clear_log_context()
        import core.helpers.logging as _log_mod

        self._orig_format = _log_mod._log_format
        self._orig_handler = _log_mod.log_handler

    def teardown_method(self):
        import core.helpers.logging as _log_mod

        _log_mod._log_format = self._orig_format
        _log_mod.log_handler = self._orig_handler
        clear_log_context()

    def test_text_mode_prints_to_stdout(self, capsys):
        import core.helpers.logging as _log_mod

        _log_mod._log_format = "text"
        _log_mod.log_handler = None
        from core.helpers.logging import log

        log("text mode message")
        captured = capsys.readouterr()
        assert "text mode message" in captured.out
        assert "[CORE]" in captured.out

    def test_json_mode_routes_through_logger(self):
        import core.helpers.logging as _log_mod

        _log_mod._log_format = "json"
        mock_logger = MagicMock()
        with patch("core.helpers.logging.logging") as mock_logging_module:
            mock_logging_module.getLogger.return_value = mock_logger
            from core.helpers.logging import log

            log("json mode message", extra={"dvr_id": "dvr_test"})
            mock_logger.info.assert_called_once_with(
                "json mode message", extra={"dvr_id": "dvr_test"}
            )

    def test_log_respects_level_filtering(self, capsys):
        import core.helpers.logging as _log_mod

        _log_mod._log_format = "text"
        _log_mod.log_handler = None
        _log_mod.log_level = 1
        from core.helpers.logging import log, LOG_VERBOSE

        log("verbose suppressed", level=LOG_VERBOSE)
        captured = capsys.readouterr()
        assert "verbose suppressed" not in captured.out
