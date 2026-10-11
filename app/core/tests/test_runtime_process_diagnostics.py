from __future__ import annotations

import sys
from types import SimpleNamespace

from core.runtime_identity import runtime_process_diagnostics


def test_worker_diagnostics_report_actual_interpreter_and_imported_modules(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(
        __file__="/config/generation/dependencies/site-packages/requests/__init__.py",
        __version__="2.34.2",
    ))
    monkeypatch.delitem(sys.modules, "cryptography", raising=False)
    result = runtime_process_diagnostics()
    assert result["python_executable"] == sys.executable
    assert result["python_prefix"] == sys.prefix
    assert result["python_version"] == ".".join(str(part) for part in sys.version_info[:3])
    assert result["dependencies"]["requests"] == {
        "loaded": True,
        "path": "/config/generation/dependencies/site-packages/requests/__init__.py",
        "version": "2.34.2",
    }
    assert result["dependencies"]["cryptography"] == {"loaded": False}
    assert "cryptography" not in sys.modules
