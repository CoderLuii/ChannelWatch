import asyncio
import inspect
import threading
from typing import Any, cast
from unittest.mock import MagicMock


class TestAsyncRuntimeStructure:
    def test_main_is_coroutine_function(self):
        from core.main import main

        assert inspect.iscoroutinefunction(main)

    def test_run_monitors_is_coroutine_function(self):
        from core.main import _run_monitors

        assert inspect.iscoroutinefunction(_run_monitors)

    def test_run_is_plain_callable(self):
        from core.main import run

        assert callable(run)
        assert not inspect.iscoroutinefunction(run)

    def test_no_threading_thread_in_entrypoint(self):
        import core.main as mod

        main_src = inspect.getsource(mod.main)
        run_src = inspect.getsource(mod.run)
        monitors_src = inspect.getsource(mod._run_monitors)
        assert "threading.Thread(" not in (main_src + run_src + monitors_src)

    def test_event_monitor_start_runs_loop_inline(self):
        from core.engine.event_monitor import EventMonitor

        monitor = EventMonitor(host="127.0.0.1")
        caller_thread_id = threading.get_ident()
        loop_thread_id = None

        def fake_loop():
            nonlocal loop_thread_id
            loop_thread_id = threading.get_ident()
            monitor.running = False

        monitor._monitor_events_loop = fake_loop

        monitor.start_monitoring()

        assert loop_thread_id == caller_thread_id
        assert monitor.running is False

    def test_event_monitor_stop_closes_active_sse_resources(self):
        from core.engine.event_monitor import EventMonitor

        class FakeAsyncResource:
            def __init__(self):
                self.closed = False

            async def aclose(self):
                self.closed = True

        async def run():
            monitor = EventMonitor(host="127.0.0.1")
            response = FakeAsyncResource()
            client = FakeAsyncResource()
            monitor.running = True
            monitor._monitor_loop = asyncio.get_running_loop()
            monitor._active_response = cast(Any, response)
            monitor._active_client = cast(Any, client)

            monitor.stop_monitoring()
            await asyncio.sleep(0.01)

            assert monitor.running is False
            assert response.closed is True
            assert client.closed is True

        asyncio.run(run())


class TestRunMonitorsShutdown:
    def test_shutdown_event_stops_monitors(self):
        from core.main import _run_monitors

        mock_monitor = MagicMock()
        mock_monitor.dvr_name = "test-dvr"
        mock_monitor.running = True

        def fake_start_monitoring():
            while mock_monitor.running:
                pass

        mock_monitor.start_monitoring = fake_start_monitoring

        async def run():
            shutdown_event = asyncio.Event()
            asyncio.get_running_loop().call_later(0.05, shutdown_event.set)
            await _run_monitors([mock_monitor], shutdown_event)

        asyncio.run(run())
        assert mock_monitor.running is False

    def test_multiple_monitors_all_stopped(self):
        from core.main import _run_monitors

        monitors = []
        for i in range(3):
            m = MagicMock()
            m.dvr_name = f"dvr-{i}"
            m.running = True
            m.start_monitoring = lambda mon=m: _spin(mon)
            monitors.append(m)

        def _spin(mon):
            while mon.running:
                pass

        async def run():
            shutdown_event = asyncio.Event()
            asyncio.get_running_loop().call_later(0.05, shutdown_event.set)
            await _run_monitors(monitors, shutdown_event)

        asyncio.run(run())
        for m in monitors:
            assert m.running is False

    def test_run_monitors_waits_for_event_before_stopping(self):
        from core.main import _run_monitors

        mock_monitor = MagicMock()
        mock_monitor.dvr_name = "dvr-x"
        mock_monitor.running = True
        mock_monitor.start_monitoring = lambda: None
        completed = {"value": False}

        async def run():
            shutdown_event = asyncio.Event()

            async def set_after():
                await asyncio.sleep(0.1)
                completed["value"] = True
                shutdown_event.set()

            asyncio.create_task(set_after())
            await _run_monitors([mock_monitor], shutdown_event)

        asyncio.run(run())
        assert completed["value"] is True
