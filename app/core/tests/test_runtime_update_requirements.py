import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REQ = ROOT / "deploy/requirements/runtime-update.txt"
LOCK = ROOT / "deploy/requirements/runtime-update.lock"
RUNTIME = ROOT / "deploy/requirements/runtime.txt"
CONSTRAINTS = ROOT / "deploy/requirements/runtime.constraints.txt"
EXCLUDED = {"pip", "setuptools", "supervisor"}


def load_dependency_builder():
    path = ROOT / "scripts/release/build-runtime-dependencies.py"
    spec = importlib.util.spec_from_file_location("runtime_update_dependency_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def direct_names(path: Path) -> set[str]:
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        assert match, line
        names.add(normalize(match.group(1)))
    return names


def pins(path: Path) -> dict[str, str]:
    result = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ ]+)", raw)
        if match:
            result[normalize(match.group(1))] = match.group(2)
    return result


def logical_lock_lines() -> list[str]:
    logical = []
    pending = ""
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += (" " if pending else "") + line.removesuffix("\\").strip()
        if not line.endswith("\\"):
            logical.append(pending)
            pending = ""
    assert not pending
    return logical


def test_runtime_update_direct_requirements_are_exact_app_subset():
    assert direct_names(REQ) == direct_names(RUNTIME) - EXCLUDED
    assert direct_names(REQ).isdisjoint(EXCLUDED)


def test_runtime_update_lock_matches_existing_closure_versions_without_management():
    expected = pins(CONSTRAINTS)
    for name in EXCLUDED:
        expected.pop(name)
    assert len(expected) == 41
    assert pins(LOCK) == expected


def test_runtime_update_lock_is_exact_hash_only_binary_input():
    lines = logical_lock_lines()
    assert len(lines) == 41
    hash_counts = []
    for line in lines:
        assert re.fullmatch(
            r"[A-Za-z0-9_.-]+==[^ ;+]+(?: --hash=sha256:[0-9a-f]{64})+",
            line,
        ), line
        hash_counts.append(line.count("--hash=sha256:"))
    assert sum(hash_counts) == 54
    assert hash_counts.count(2) == 13
    assert hash_counts.count(1) == 28
    text = LOCK.read_text(encoding="utf-8").lower()
    assert "http://" not in text
    assert "https://" not in text
    assert " @ " not in text
    assert ".tar.gz" not in text
    assert ".zip" not in text
    assert "+" not in text


def test_runtime_dependency_builder_accepts_release_inputs():
    builder = load_dependency_builder()
    assert len(builder.parse_requested_requirements(REQ)) == 13
    assert len(builder.parse_locked_requirements(LOCK)) == 41
