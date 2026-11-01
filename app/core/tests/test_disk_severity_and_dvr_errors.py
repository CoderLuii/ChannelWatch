from types import SimpleNamespace

import httpx

from ui.backend import main as backend_main


def test_disk_status_uses_operator_thresholds():
    cfg = SimpleNamespace(
        ds_critical_threshold_percent=10.0,
        ds_critical_threshold_gb=1.0,
        ds_warning_threshold_percent=20.0,
        ds_warning_threshold_gb=1.0,
    )
    # 8% free is only a warning with the shipped defaults, but critical here.
    assert backend_main._simple_disk_status(80.0, 1000.0) == "warning"
    assert backend_main._simple_disk_status(80.0, 1000.0, cfg) == "critical"
    assert backend_main._simple_disk_status(150.0, 1000.0, cfg) == "warning"
    assert backend_main._simple_disk_status(500.0, 1000.0, cfg) == "normal"
    assert backend_main._simple_disk_status(None, 1000.0, cfg) == "unknown"


def test_disk_status_ignores_unreadable_thresholds():
    cfg = SimpleNamespace(ds_critical_threshold_percent="not-a-number")
    assert backend_main._simple_disk_status(10.0, 1000.0, cfg) == "critical"


def test_worst_disk_severity_never_averages_away_a_critical_dvr():
    assert backend_main._worst_disk_severity(["normal", "critical", "warning"]) == "critical"
    assert backend_main._worst_disk_severity(["normal", "warning"]) == "warning"
    assert backend_main._worst_disk_severity(["unknown", "normal"]) == "normal"
    assert backend_main._worst_disk_severity([]) == "normal"


def test_dvr_connection_error_reason_is_described():
    request = httpx.Request("GET", "http://dvr.invalid:8089/status")
    assert "Timed out" in backend_main._describe_dvr_connection_error(
        httpx.ReadTimeout("slow", request=request)
    )
    assert "refused" in backend_main._describe_dvr_connection_error(
        httpx.ConnectError("refused", request=request)
    )
    assert "HTTP 503" in backend_main._describe_dvr_connection_error(
        httpx.Response(503, request=request)
    )
    assert backend_main._describe_dvr_connection_error(
        httpx.Response(200, request=request)
    ) is None


def test_disk_activity_filter_includes_recoveries():
    recovered = backend_main.AlertHistoryItem(
        type="disk_recovered", title="Disk space back to normal", message="", timestamp=""
    )
    assert backend_main._activity_matches_type(recovered, "disk")


def test_disk_threshold_order_requires_critical_below_warning():
    settings = SimpleNamespace(
        ds_warning_threshold_percent=10,
        ds_critical_threshold_percent=5,
        ds_warning_threshold_gb=50,
        ds_critical_threshold_gb=25,
    )
    assert backend_main._disk_threshold_order_error(settings) is None
    settings.ds_critical_threshold_percent = 10
    assert "percent" in backend_main._disk_threshold_order_error(settings)
    settings.ds_critical_threshold_percent = 5
    settings.ds_critical_threshold_gb = 60
    assert "GB" in backend_main._disk_threshold_order_error(settings)
    settings.ds_critical_threshold_gb = 0
    assert backend_main._disk_threshold_order_error(settings) is None


def test_disk_threshold_order_checks_each_dvr_against_what_it_inherits():
    settings = SimpleNamespace(
        ds_warning_threshold_percent=10,
        ds_critical_threshold_percent=5,
        ds_warning_threshold_gb=50,
        ds_critical_threshold_gb=25,
        dvr_servers=[{"name": "Den", "host": "den.local", "overrides": {}}],
    )
    server = settings.dvr_servers[0]
    assert backend_main._disk_threshold_order_error(settings) is None

    server["overrides"] = {"ds_warning_threshold_gb": 20}
    assert (
        backend_main._disk_threshold_order_error(settings)
        == "Critical GB must be lower than warning GB on Den."
    )

    server["overrides"] = {"ds_warning_threshold_gb": 20, "ds_critical_threshold_gb": 0}
    assert backend_main._disk_threshold_order_error(settings) is None

    server["overrides"] = {"ds_critical_threshold_percent": 10}
    server["name"] = ""
    assert (
        backend_main._disk_threshold_order_error(settings)
        == "Critical percent must be lower than warning percent on den.local."
    )

    server["overrides"] = {"ds_critical_threshold_gb": None}
    assert backend_main._disk_threshold_order_error(settings) is None


def test_disk_threshold_order_skips_archived_dvrs():
    settings = SimpleNamespace(
        ds_warning_threshold_percent=10,
        ds_critical_threshold_percent=5,
        ds_warning_threshold_gb=50,
        ds_critical_threshold_gb=25,
        dvr_servers=[
            {
                "name": "Old",
                "deleted_at": "2026-10-30T00:00:00Z",
                "overrides": {"ds_critical_threshold_gb": 99},
            }
        ],
    )
    assert backend_main._disk_threshold_order_error(settings) is None
