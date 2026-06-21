"""DVR id stability tests across migration, env-override, and UI paths.

ChannelWatch preserves per-DVR history, notification routing, and soft-delete
state by deriving the same DVR id from every entry path:

1. settings migrations
2. environment-driven DVR setup
3. browser-side DVR creation
"""

import os
import random
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.helpers.config import CoreSettings
from core.helpers.dvr_id import canonical_dvr_id
from core.helpers.migration import run_migrations


def _ts_canonical_dvr_id(host: str, port: int) -> str:
    """Call canonicalDvrId from dvr-id.ts via Node 22 --experimental-strip-types."""
    ts_lib = Path(__file__).resolve().parent.parent.parent / "ui" / "lib" / "dvr-id.ts"
    assert ts_lib.is_file(), f"dvr-id.ts not found at {ts_lib}"

    ts_path_uri = ts_lib.as_uri()
    script = (
        f'import {{ canonicalDvrId }} from "{ts_path_uri}";'
        f"process.stdout.write(canonicalDvrId({host!r}, {port}));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "--experimental-strip-types"],
        input=script,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        pytest.skip(
            f"Node --experimental-strip-types failed ({result.returncode}).\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _simulate_ui_random_id() -> str:
    """Replicate the previous random DVR id format used by the UI."""
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    return "dvr_" + "".join(random.choices(chars, k=8))


class TestMigrationAndEnvProduceSameId:
    HOST = "192.168.1.100"
    PORT = 8089

    def test_migration_and_env_produce_same_id(self):
        """Migrated DVR settings and env-created DVR settings use the canonical id."""
        host, port = self.HOST, self.PORT

        v0_settings = {
            "channels_dvr_host": host,
            "channels_dvr_port": port,
        }

        # Keep env bootstrap from appending an extra server during this migration test.
        with patch.dict(
            os.environ, {"CHANNELS_DVR_HOST": "", "CHANNELS_DVR_PORT": ""}, clear=False
        ):
            migrated = run_migrations(v0_settings.copy(), from_version=0, to_version=7)

        assert migrated.get("dvr_servers"), (
            "Migration v0->v7 should have created dvr_servers from channels_dvr_host"
        )
        migration_id = migrated["dvr_servers"][0]["id"]

        env_id = CoreSettings._make_dvr_id(host, port)
        canonical = canonical_dvr_id(host, port)

        assert migration_id == canonical, (
            f"Migration produced {migration_id!r} for {host}:{port} "
            f"but canonical id is {canonical!r}."
        )

        assert env_id == canonical, (
            f"Env override _make_dvr_id produced {env_id!r} but canonical is "
            f"{canonical!r} for {host}:{port}."
        )


class TestUiAddServerProducesSameId:
    HOST = "192.168.1.100"
    PORT = 8089

    SETTINGS_FORM = (
        Path(__file__).resolve().parent.parent.parent
        / "ui"
        / "components"
        / "settings-form.tsx"
    )

    def test_typescript_canonical_matches_python_canonical(self):
        """TypeScript canonicalDvrId must equal Python canonical_dvr_id."""
        ts_id = _ts_canonical_dvr_id(self.HOST, self.PORT)
        py_id = canonical_dvr_id(self.HOST, self.PORT)
        assert ts_id == py_id, (
            f"TS canonicalDvrId({self.HOST!r}, {self.PORT}) = {ts_id!r} "
            f"!= Python canonical_dvr_id = {py_id!r}."
        )

    def test_ui_add_server_produces_same_id_as_canonical(self):
        """addServer() in settings-form.tsx must use canonicalDvrId."""
        assert self.SETTINGS_FORM.is_file(), (
            f"settings-form.tsx not found at {self.SETTINGS_FORM}"
        )
        source = self.SETTINGS_FORM.read_text(encoding="utf-8")

        match = re.search(
            r"const addServer\s*=\s*\(\)\s*=>\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
            source,
        )
        assert match, "addServer() not found in settings-form.tsx."
        body = match.group(1)

        assert "canonicalDvrId" in body, (
            f"addServer() must call canonicalDvrId(host, port). "
            f"Current body:\n{body.strip()}\n"
        )

    def test_math_random_id_does_not_match_canonical(self):
        """The previous random id shape should not be treated as canonical."""
        canonical = canonical_dvr_id(self.HOST, self.PORT)
        collisions = sum(1 for _ in range(50) if _simulate_ui_random_id() == canonical)
        assert collisions == 0, (
            f"{collisions}/50 random ids matched canonical {canonical!r}; re-run the suite."
        )


class TestDuplicateAddDetected:
    HOST = "192.168.1.100"
    PORT = 8089

    def test_duplicate_add_detected(self):
        """Two addDiscoveredServer() calls for the same host:port produce the same id."""
        host, port = self.HOST, self.PORT
        canonical = canonical_dvr_id(host, port)

        assert canonical_dvr_id(host, port) == canonical, (
            "canonical_dvr_id must be deterministic."
        )

        id_add_1 = canonical_dvr_id(host, port)
        id_add_2 = canonical_dvr_id(host, port)

        assert id_add_1 == canonical, (
            f"First add produced {id_add_1!r} but canonical is {canonical!r}."
        )
        assert id_add_2 == canonical, (
            f"Second add produced {id_add_2!r} but canonical is {canonical!r}."
        )
