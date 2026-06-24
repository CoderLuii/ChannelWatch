import json
from pathlib import Path

import core.runtime_launcher as runtime_launcher
from core.update_center import RUNTIME_ABI, resolve_active_app_dir


def _bundle(root: Path, version: str = "0.9.10") -> Path:
    bundle_dir = root / "channelwatch-runtime" / "releases" / f"v{version}"
    (bundle_dir / "core").mkdir(parents=True)
    (bundle_dir / "ui" / "backend").mkdir(parents=True)
    (bundle_dir / "core" / "main.py").write_text("core")
    (bundle_dir / "ui" / "backend" / "main.py").write_text("ui")
    return bundle_dir


def _write_active(config_dir: Path, bundle_dir: Path, *, version: str = "0.9.10", abi: str = RUNTIME_ABI, schema: int = 7):
    active = {
        "version": version,
        "path": str(bundle_dir),
        "runtime_abi": abi,
        "settings_schema_version": schema,
    }
    active_path = config_dir / "channelwatch-runtime" / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(active))


def test_newer_compatible_active_bundle_wins(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path)
    _write_active(tmp_path, bundle_dir)

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.9",
        settings_schema_version=7,
    )

    assert selection.source == "bundle"
    assert selection.app_dir == bundle_dir.resolve()


def test_image_wins_when_image_version_is_current_or_newer(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path, "0.9.10")
    _write_active(tmp_path, bundle_dir, version="0.9.10")

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.10",
        settings_schema_version=7,
    )

    assert selection.source == "image"
    assert selection.reason == "image-version-is-current-or-newer"
    assert not (tmp_path / "channelwatch-runtime" / "active.json").exists()
    assert (tmp_path / "channelwatch-runtime" / "deactivated-active.json").exists()


def test_abi_mismatch_falls_back_to_image(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path)
    _write_active(tmp_path, bundle_dir, abi="other-runtime")

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.9",
        settings_schema_version=7,
    )

    assert selection.source == "image"
    assert selection.reason == "active-bundle-abi-mismatch"


def test_corrupt_or_missing_active_bundle_falls_back_to_image(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    (runtime_dir / "active.json").write_text("{not json")

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.9",
        settings_schema_version=7,
    )

    assert selection.source == "image"
    assert selection.reason == "no-active-bundle"


def test_runtime_launcher_records_failed_activation_and_restores_previous_bundle(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    active_path = runtime_dir / "active.json"
    rollback_path = runtime_dir / "rollback.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(
        json.dumps(
            {
                "version": "0.9.10",
                "path": str(current_dir),
                "runtime_abi": RUNTIME_ABI,
                "settings_schema_version": 7,
            }
        )
    )
    rollback_path.write_text(
        json.dumps(
            {
                "previous_active": {
                    "version": "0.9.9",
                    "path": str(previous_dir),
                    "runtime_abi": RUNTIME_ABI,
                    "settings_schema_version": 7,
                }
            }
        )
    )

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    runtime_launcher.rollback_failed_activation("import exploded")

    active = json.loads(active_path.read_text())
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert active["version"] == "0.9.9"
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert job["rolled_back_from"] == "0.9.10"
    assert job["rolled_back_to"] == "0.9.9"
