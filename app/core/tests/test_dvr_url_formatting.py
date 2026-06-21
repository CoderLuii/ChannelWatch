from types import SimpleNamespace


def test_dvr_connection_brackets_bare_ipv6_hosts():
    from core.helpers.dvr_connection import DVRConnection, build_dvr_base_url

    assert build_dvr_base_url("2001:db8::1", 8089) == "http://[2001:db8::1]:8089"
    assert build_dvr_base_url("[2001:db8::1]", 8089) == "http://[2001:db8::1]:8089"
    assert build_dvr_base_url("fe80::1%eth0", 8089) == "http://[fe80::1%eth0]:8089"
    assert (
        DVRConnection(id="dvr_ipv6", name="IPv6", host="2001:db8::1").base_url
        == "http://[2001:db8::1]:8089"
    )


def test_core_dvr_url_builders_bracket_bare_ipv6_hosts(tmp_path, monkeypatch):
    from core.alerts.common import stream_tracker
    from core.alerts.common.stream_tracker import StreamTracker
    from core.engine.event_monitor import EventMonitor
    from core.helpers.channel_info import ChannelInfoProvider
    from core.helpers.program_info import ProgramInfoProvider
    from core.helpers.vod_info import VODInfoProvider

    monkeypatch.setattr(stream_tracker, "CONFIG_PATH", str(tmp_path))

    assert EventMonitor(host="2001:db8::1").base_url == "http://[2001:db8::1]:8089"
    assert VODInfoProvider("2001:db8::1", 8089).base_url == "http://[2001:db8::1]:8089"
    assert (
        ProgramInfoProvider("2001:db8::1", 8089).base_url == "http://[2001:db8::1]:8089"
    )
    assert (
        ChannelInfoProvider("2001:db8::1", 8089).base_url == "http://[2001:db8::1]:8089"
    )
    assert StreamTracker(host="2001:db8::1").base_url == "http://[2001:db8::1]:8089"


def test_ui_backend_dvr_server_tuples_bracket_bare_ipv6_hosts():
    import ui.backend.main as ui_main

    settings = SimpleNamespace(
        dvr_servers=[
            {
                "id": "dvr_ipv6",
                "name": "IPv6 DVR",
                "host": "2001:db8::1",
                "port": 8089,
                "enabled": True,
            }
        ]
    )

    assert ui_main._get_dvr_servers_from_settings(settings) == [
        ("dvr_ipv6", "IPv6 DVR", "http://[2001:db8::1]:8089")
    ]


def test_doctor_base_url_brackets_bare_ipv6_hosts():
    from core.cli.doctor import _base_url

    assert _base_url({"host": "2001:db8::1", "port": 8089}) == (
        "http://[2001:db8::1]:8089"
    )
