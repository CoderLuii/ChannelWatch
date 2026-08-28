from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "release" / "verify-minimum-image-app-update.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("minimum_image_canary", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_referenced_static_assets_include_only_local_script_and_link_paths():
    canary = _load_script()

    paths = canary.referenced_static_assets(b"""
        <html><head>
          <link rel="stylesheet" href="/_next/static/app.css?build=1">
          <link rel="preconnect" href="https://example.com">
        </head><body>
          <script src="/_next/static/app.js"></script>
          <script src="https://example.com/external.js"></script>
          <img src="/images/not-a-script-or-link.png">
        </body></html>
        """)

    assert paths == ["/_next/static/app.css", "/_next/static/app.js"]


def test_referenced_static_assets_deduplicate_repeated_members():
    canary = _load_script()

    paths = canary.referenced_static_assets(
        b'<script src="/_next/static/app.js"></script>'
        b'<script src="/_next/static/app.js"></script>'
    )

    assert paths == ["/_next/static/app.js"]


def test_active_runtime_changes_are_written_inside_the_container(monkeypatch):
    canary = _load_script()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        canary,
        "docker_exec",
        lambda name, code: calls.append((name, code)) or "",
    )

    canary.write_active("canary", "1.0.6")
    canary.remove_active("canary")

    assert [name for name, _ in calls] == ["canary", "canary"]
    assert "/config/channelwatch-runtime/active.json" in calls[0][1]
    assert '"version": "1.0.6"' in calls[0][1]
    assert ".unlink()" in calls[1][1]
