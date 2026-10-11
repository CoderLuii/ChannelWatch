"""Runtime-aware clients use a separate feed from immutable old clients."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.tests.test_runtime_catalog import _read, _runtime_catalog
from core.tests.test_update_manager import _bundle, _key_pair
from core.tests.test_update_v0918 import _catalog
from core.update_center import (
    canonical_payload_bytes,
    validate_trusted_url,
    verify_ed25519_signature,
)


def test_runtime_client_uses_a_distinct_feed():
    from core.update_catalog import DEFAULT_UPDATE_CATALOG_URL

    assert DEFAULT_UPDATE_CATALOG_URL.endswith("/updates/v3/stable.json")


def test_frozen_v141_client_keeps_app_only_updates(tmp_path):
    root = Path(__file__).resolve().parents[3]
    source = subprocess.run(
        ["git", "show", "dd57aa4d446ce5c37534d3cb68df7ae746ae56f9:app/core/update_catalog.py"],
        cwd=root, check=True, capture_output=True,
    ).stdout
    path = tmp_path / "frozen_v141_catalog.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("frozen_v141_catalog", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        private, public = _key_pair()
        old_feed = json.loads(_catalog(
            private, _bundle("1.4.2"), version="1.4.2", sources=["1.4.1"],
        ))
        catalog = module.normalize_catalog(
            old_feed, public_keys=public,
            verify_signature=verify_ed25519_signature,
            canonical_payload=canonical_payload_bytes,
            validate_url=validate_trusted_url,
        )
        selection = module.select_catalog_release(
            catalog, current_version="1.4.1", runtime_abi="channelwatch-runtime-v1",
            settings_schema_version=7, launcher_protocol=3,
        )
        assert selection.release["version"] == "1.4.2"
        assert module.DEFAULT_UPDATE_CATALOG_URL.endswith("/updates/v2/stable.json")
        new_feed = _runtime_catalog(private)
        with pytest.raises(TypeError, match="schema"):
            module.normalize_catalog(
                new_feed, public_keys=public,
                verify_signature=verify_ed25519_signature,
                canonical_payload=canonical_payload_bytes,
                validate_url=validate_trusted_url,
            )
        assert _read(new_feed, public)["payload"]["version"] == "1.5.1"
    finally:
        sys.modules.pop(spec.name, None)
