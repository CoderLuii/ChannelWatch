import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
VEX_PATH = ROOT / "deploy" / "security" / "channelwatch-v1.0.0.openvex.json"
SCRIPT_PATH = ROOT / "scripts" / "release" / "verify-vex.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_release_vex", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _document():
    return json.loads(VEX_PATH.read_text(encoding="utf-8"))


def test_release_vex_covers_exact_runtime_findings_and_architectures():
    _module().validate_vex(_document(), expected_version="1.0.0")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document["statements"].pop(),
        lambda document: document["statements"][0].update(status="affected"),
        lambda document: document["statements"][0]["products"].pop(),
        lambda document: document["statements"][0]["products"][0].update(
            {"@id": "pkg:apk/wolfi/python-3.14@3.14.6-r0?arch=x86_64&distro=wolfi-20230201"}
        ),
    ],
)
def test_release_vex_rejects_incomplete_or_stale_dispositions(mutation):
    document = copy.deepcopy(_document())
    mutation(document)
    module = _module()
    with pytest.raises(module.VexValidationError):
        module.validate_vex(document, expected_version="1.0.0")


def test_runtime_sources_preserve_vex_execute_path_claims():
    production_sources = [
        path
        for root in (ROOT / "app" / "core", ROOT / "app" / "ui" / "backend")
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in production_sources)

    assert "HTTPPasswordMgr" not in combined
    assert "import poplib" not in combined
    assert "from poplib" not in combined
    assert "import stringprep" not in combined
    assert "from stringprep" not in combined
    assert "import tarfile" not in combined
    assert "from tarfile" not in combined

    disk_source = (ROOT / "app" / "core" / "alerts" / "disk_space.py").read_text(
        encoding="utf-8"
    )
    updater_source = (ROOT / "app" / "core" / "update_center.py").read_text(
        encoding="utf-8"
    )
    assert "build_safe_dvr_request" in disk_source
    assert 'headers={"Host": request.host_header}' in disk_source
    assert 'parsed.hostname.encode("ascii")' in updater_source
