import json
import tempfile
from unittest.mock import MagicMock

import pytest

from core.tests.test_update_manager import _bundle, _key_pair, _manifest
from core.update_center import UpdateBundleError, UpdateManager, UpdateManifestError


def _response(monkeypatch, chunks, final_url="https://github.com/CoderLuii/ChannelWatch/releases/download/v1.5.1/runtime.zip"):
    response = MagicMock()
    response.__enter__.return_value = response
    response.geturl.return_value = final_url
    response.read.side_effect = chunks
    opener = MagicMock()
    opener.open.return_value = response
    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: opener)
    return response


def test_download_streams_to_private_fd_and_rewinds(monkeypatch):
    from core.runtime_download import fetch_runtime_bundle

    response = _response(monkeypatch, [b"first", b"second", b""])
    with tempfile.TemporaryFile("w+b") as destination:
        fetch_runtime_bundle("https://github.com/runtime.zip", destination, max_bytes=11)
        assert destination.tell() == 0
        assert destination.read() == b"firstsecond"
    assert all(call.args == (65536,) for call in response.read.call_args_list)


def test_oversized_download_never_writes_over_limit(monkeypatch):
    from core.runtime_download import fetch_runtime_bundle

    _response(monkeypatch, [b"1234", b"56", b""])
    with tempfile.TemporaryFile("w+b") as destination:
        with pytest.raises(UpdateManifestError, match="size limit"):
            fetch_runtime_bundle("https://github.com/runtime.zip", destination, max_bytes=5)
        assert destination.tell() == 4


def test_final_untrusted_redirect_rejected_before_read(monkeypatch):
    from core.runtime_download import fetch_runtime_bundle

    response = _response(monkeypatch, [b"bad"], "https://untrusted.example/runtime.zip")
    with tempfile.TemporaryFile("w+b") as destination:
        with pytest.raises(UpdateManifestError):
            fetch_runtime_bundle("https://github.com/runtime.zip", destination, max_bytes=5)
        assert destination.tell() == 0
    response.read.assert_not_called()


def test_total_download_deadline_is_bounded(monkeypatch):
    from core.runtime_download import fetch_runtime_bundle

    _response(monkeypatch, [b"a", b"b"])
    ticks = iter([0, 0, 2])
    monkeypatch.setattr("core.runtime_download.time.monotonic", lambda: next(ticks))
    with tempfile.TemporaryFile("w+b") as destination:
        with pytest.raises(UpdateManifestError, match="deadline"):
            fetch_runtime_bundle("https://github.com/runtime.zip", destination, max_bytes=5, deadline_seconds=1)


def test_signature_checks_streamed_bytes_without_loading_whole_bundle(tmp_path):
    private, public = _key_pair()
    bundle = _bundle()
    payload = json.loads(_manifest(private, bundle))["payload"]
    manager = UpdateManager(config_dir=tmp_path, current_version="0.9.9", public_keys=public)
    with tempfile.TemporaryFile("w+b") as source:
        source.write(bundle)
        manager._verify_bundle_signature(payload, source)
        assert source.tell() == 0
        source.seek(0)
        source.write(b"tampered")
        with pytest.raises(UpdateBundleError, match="hash"):
            manager._verify_bundle_signature(payload, source)


def test_protocol_four_refuses_app_only_activation_before_fetch(tmp_path):
    private, public = _key_pair()
    bundle = _bundle("1.5.1")
    manifest = _manifest(private, bundle, version="1.5.1")
    fetched = []

    def fetch(url, _maximum):
        fetched.append(url)
        assert not url.endswith(".zip")
        return manifest

    manager = UpdateManager(
        config_dir=tmp_path, current_version="1.5.0", image_version="1.5.0",
        launcher_protocol=4, public_keys=public, fetcher=fetch,
    )
    result = manager.apply()
    assert result["status"] == "image_required"
    assert manager._payload_requires_image(json.loads(manifest)["payload"])
    assert not manager.active_path.exists()
    assert not manager.restart_required_path.exists()


def test_runtime_archive_accepts_stream_without_closing_it(monkeypatch):
    from core.runtime_archive import validate_runtime_archive
    from core.tests.test_runtime_transaction import _runtime_bundle, _runtime_fixture

    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "4")
    descriptor, inventory, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory, payloads)
    with tempfile.TemporaryFile("w+b") as source:
        source.write(bundle)
        selected = validate_runtime_archive(source, descriptor)
        assert set(payloads).issubset(selected)
        assert not source.closed


def test_worker_diagnostics_do_not_treat_environment_as_verified_claim(tmp_path, monkeypatch):
    import sys

    from core import update_center

    monkeypatch.setattr(sys, "_channelwatch_runtime_generation_identity", None, raising=False)
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_GENERATION_ID", "a" * 64)
    diagnostics = {
        "pid": 101,
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
        "dependencies": {"sqlalchemy": {"loaded": False}},
    }
    monkeypatch.setattr(update_center, "runtime_process_diagnostics", lambda: diagnostics)
    manager = UpdateManager(config_dir=tmp_path, current_version="1.5.0")
    worker = manager.status()["worker_runtime"]
    assert worker == {
        **diagnostics,
        "verified_generation": None,
    }


def test_worker_status_binds_selected_control_identity_and_process_diagnostics(
    tmp_path, monkeypatch
):
    import sys

    from core import update_center

    generation = (tmp_path / "channelwatch-runtime" / "releases" / ("a" * 64))
    generation.mkdir(parents=True)
    identity = {
        "path": str(generation.resolve()),
        "runtime_generation_id": "a" * 64,
        "runtime_descriptor_sha256": "b" * 64,
        "dependency_inventory_sha256": "c" * 64,
    }
    active = {
        "version": "1.5.4",
        "activation_id": "activation-1",
        **identity,
    }
    diagnostics = {
        "pid": 202,
        "python_version": "3.14.1",
        "python_executable": f"{generation}/environment/bin/python",
        "python_prefix": f"{generation}/environment",
        "dependencies": {
            "sqlalchemy": {
                "loaded": True,
                "path": f"{generation}/dependencies/site-packages/sqlalchemy/__init__.py",
                "version": "2.1.4",
            }
        },
    }
    monkeypatch.setattr(
        sys, "_channelwatch_runtime_generation_identity", identity, raising=False
    )
    monkeypatch.setattr(update_center, "runtime_process_diagnostics", lambda: diagnostics)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.5.4",
        image_version="1.5.3",
        launcher_protocol=4,
    )
    manager.active_path.write_text(json.dumps(active), encoding="utf-8")

    status = manager.status()

    assert status["active_bundle"] == active
    assert status["worker_runtime"] == {
        **diagnostics,
        "verified_generation": identity,
    }
