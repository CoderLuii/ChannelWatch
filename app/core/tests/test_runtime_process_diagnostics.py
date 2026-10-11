from __future__ import annotations

import sys
from types import SimpleNamespace

from core import runtime_identity


def test_worker_diagnostics_report_actual_interpreter_and_imported_modules(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(
        __file__="/config/generation/dependencies/site-packages/requests/__init__.py",
        __version__="2.34.2",
    ))
    monkeypatch.delitem(sys.modules, "cryptography", raising=False)
    result = runtime_identity.runtime_process_diagnostics()
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


def test_loaded_sqlalchemy_and_httptools_report_fixed_distribution_versions(
    monkeypatch,
):
    metadata_lookups = []
    monkeypatch.setitem(
        sys.modules,
        "sqlalchemy",
        SimpleNamespace(
            __file__="/config/generation/dependencies/site-packages/sqlalchemy/__init__.py",
            __version__="2.1.4",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "httptools",
        SimpleNamespace(
            __file__="/config/generation/dependencies/site-packages/httptools/__init__.py",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "httptools.parser.parser",
        SimpleNamespace(
            __file__="/config/generation/dependencies/site-packages/httptools/parser/parser.cpython-314-x86_64-linux-gnu.so",
        ),
    )

    def distribution_version(name):
        metadata_lookups.append(name)
        return {"httptools": "0.9.0"}[name]

    monkeypatch.setattr(runtime_identity.importlib_metadata, "version", distribution_version)

    dependencies = runtime_identity.runtime_process_diagnostics()["dependencies"]

    assert dependencies["sqlalchemy"] == {
        "loaded": True,
        "path": "/config/generation/dependencies/site-packages/sqlalchemy/__init__.py",
        "version": "2.1.4",
    }
    assert dependencies["httptools"] == {
        "loaded": True,
        "path": "/config/generation/dependencies/site-packages/httptools/__init__.py",
        "version": "0.9.0",
    }
    assert metadata_lookups == ["httptools"]
    assert "httptools.parser.parser" not in dependencies


def test_unloaded_optional_dependencies_are_not_imported_or_environment_driven(
    monkeypatch,
):
    monkeypatch.delitem(sys.modules, "sqlalchemy", raising=False)
    monkeypatch.delitem(sys.modules, "httptools", raising=False)
    monkeypatch.delitem(sys.modules, "arbitrary_package", raising=False)
    monkeypatch.setenv(
        "CHANNELWATCH_RUNTIME_DIAGNOSTIC_MODULES",
        "arbitrary_package,httpx",
    )

    dependencies = runtime_identity.runtime_process_diagnostics()["dependencies"]

    assert dependencies["sqlalchemy"] == {"loaded": False}
    assert dependencies["httptools"] == {"loaded": False}
    assert "arbitrary_package" not in dependencies
    assert "arbitrary_package" not in sys.modules
    assert "httpx" not in dependencies


def test_distribution_metadata_failure_is_bounded_and_deterministic(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "httptools",
        SimpleNamespace(
            __file__="/config/generation/dependencies/site-packages/httptools/__init__.py",
        ),
    )

    def unavailable(_name):
        raise RuntimeError("host-specific metadata failure details")

    monkeypatch.setattr(runtime_identity.importlib_metadata, "version", unavailable)

    assert runtime_identity.runtime_process_diagnostics()["dependencies"]["httptools"] == {
        "loaded": True,
        "path": "/config/generation/dependencies/site-packages/httptools/__init__.py",
        "version": "",
        "version_error": "distribution_metadata_unavailable",
    }
