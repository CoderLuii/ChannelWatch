import json
from pathlib import Path

from core.image_metadata import resolve_image_metadata


def _write_metadata(root: Path, *, version: str = "1.0.8") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "channelwatch-image.json").write_text(
        json.dumps(
            {
                "version": version,
                "runtime_abi": "channelwatch-runtime-v1",
                "settings_schema_version": 7,
            }
        ),
        encoding="utf-8",
    )


def test_embedded_image_version_wins_over_stale_container_environment(tmp_path: Path):
    _write_metadata(tmp_path, version="1.0.0")

    metadata = resolve_image_metadata(
        image_app_dir=tmp_path,
        environ={"CHANNELWATCH_IMAGE_VERSION": "0.9.17"},
    )

    assert metadata.version == "1.0.0"
    assert metadata.source == "embedded"
    assert metadata.environment_mismatch is True


def test_historical_image_without_metadata_uses_environment_fallback(tmp_path: Path):
    metadata = resolve_image_metadata(
        image_app_dir=tmp_path,
        environ={"CHANNELWATCH_IMAGE_VERSION": "0.9.17"},
    )

    assert metadata.version == "0.9.17"
    assert metadata.source == "environment"
    assert metadata.environment_mismatch is False


def test_invalid_embedded_metadata_fails_closed_instead_of_trusting_environment(
    tmp_path: Path,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "channelwatch-image.json").write_text("{broken", encoding="utf-8")

    metadata = resolve_image_metadata(
        image_app_dir=tmp_path,
        environ={"CHANNELWATCH_IMAGE_VERSION": "9.9.9"},
    )

    assert metadata.version == "unknown"
    assert metadata.source == "invalid"
    assert metadata.environment_mismatch is True
