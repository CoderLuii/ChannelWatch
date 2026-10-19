import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[3]
TEST_RELEASE_GIT_SHA = "0123456789abcdef0123456789abcdef01234567"
TEST_RELEASE_URL = "https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.17"
TEST_BUNDLE_URL = (
    "https://github.com/CoderLuii/ChannelWatch/releases/download/"
    "v0.9.17/channelwatch-app-v0.9.17.zip"
)


def _load_script(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_shell_step(workflow: str, name: str) -> tuple[str, str]:
    marker = f"      - name: {name}\n"
    block = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
    run_marker = "        run: |\n"
    shell = block.split(run_marker, 1)[1]
    return block, "\n".join(
        line.removeprefix("          ") for line in shell.splitlines()
    )


def _write_oci_json_blob(layout: Path, value: dict) -> dict:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    blob = layout / "blobs" / "sha256" / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(raw)
    return {
        "mediaType": value["mediaType"],
        "digest": f"sha256:{digest}",
        "size": len(raw),
    }


def _synthetic_oci_layout(
    root: Path,
    *,
    platforms: tuple[str, ...] = ("amd64", "arm64"),
    include_variant: bool = False,
    omit_config_media_type: bool = False,
) -> tuple[Path, str]:
    layout = root / "image.oci"
    (layout / "blobs" / "sha256").mkdir(parents=True)
    platform_descriptors = []
    for architecture in platforms:
        config_bytes = f"config-{architecture}".encode()
        config_digest = hashlib.sha256(config_bytes).hexdigest()
        (layout / "blobs" / "sha256" / config_digest).write_bytes(config_bytes)
        config = {
            "digest": f"sha256:{config_digest}",
            "size": len(config_bytes),
        }
        if not omit_config_media_type:
            config["mediaType"] = "application/vnd.oci.image.config.v1+json"
        layer_bytes = f"layer-{architecture}".encode()
        layer_digest = hashlib.sha256(layer_bytes).hexdigest()
        (layout / "blobs" / "sha256" / layer_digest).write_bytes(layer_bytes)
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": config,
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": f"sha256:{layer_digest}",
                    "size": len(layer_bytes),
                }
            ],
        }
        descriptor = _write_oci_json_blob(layout, manifest)
        descriptor["platform"] = {"os": "linux", "architecture": architecture}
        if include_variant:
            descriptor["platform"]["variant"] = "v8"
        platform_descriptors.append(descriptor)
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": platform_descriptors,
    }
    index_descriptor = _write_oci_json_blob(layout, index)
    (layout / "index.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": [index_descriptor]}),
        encoding="utf-8",
    )
    (layout / "oci-layout").write_text(
        json.dumps({"imageLayoutVersion": "1.0.0"}), encoding="utf-8"
    )
    return layout, index_descriptor["digest"]


def test_release_body_for_0912_uses_dependency_copy(monkeypatch, capsys):
    module = _load_script(
        "render_release_body",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v0.9.12",
        "releaseDate": "2026-08-11",
        "changelogHighlights": [
            "Keep idle Channels DVR event streams connected.",
            "Stop dashboard stream requests from rebuilding core settings.",
        ],
        "changelogSections": {
            "Changed": [
                "Keep idle Channels DVR event streams connected.",
            ],
            "Fixed": [
                "Stop dashboard stream requests from rebuilding core settings.",
            ],
        },
        "dockerTag": "0.9.12",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.12"])

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.12 - Dependency maintenance\n")
    assert "## Fixed" in output
    assert "## Changed" in output
    assert "adds the new in-app **Update Center**" not in output
    assert "`coderluii/channelwatch:0.9.12`" in output


def test_release_body_for_0913_uses_reporting_copy(monkeypatch, capsys):
    module = _load_script(
        "render_release_body_0913",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v0.9.13",
        "releaseDate": "2026-08-12",
        "changelogHighlights": ["Enable reliable live problem reporting."],
        "changelogSections": {
            "Fixed": ["Enable reliable live problem reporting."],
            "Security": ["Keep private support attachments out of public issues."],
        },
        "dockerTag": "0.9.13",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.13"])

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.13 - Reporting reliability\n")
    assert "## Fixed" in output
    assert "## Security" in output
    assert "`coderluii/channelwatch:0.9.13`" in output


def test_release_body_for_0914_uses_reporting_and_update_copy(monkeypatch, capsys):
    module = _load_script("render_release_body_0914", "scripts/release/render-release-body.py")
    metadata = {
        "versionTag": "v0.9.14", "releaseDate": "2026-08-13",
        "changelogHighlights": ["Make reporting and update recovery clearer."],
        "changelogSections": {"Fixed": ["Make reporting and update recovery clearer."]},
        "dockerTag": "0.9.14",
    }
    monkeypatch.setattr(module, "load_exporter", lambda: SimpleNamespace(collect_metadata=lambda *args: metadata))
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.14"])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.14 - Reporting and update reliability\n")
    assert "## Fixed" in output
    assert "`coderluii/channelwatch:0.9.14`" in output


def test_release_body_for_0915_uses_update_and_reporting_copy(monkeypatch, capsys):
    module = _load_script("render_release_body_0915", "scripts/release/render-release-body.py")
    metadata = {
        "versionTag": "v0.9.15", "releaseDate": "2026-08-14",
        "changelogHighlights": ["Improve update restart recovery and report privacy."],
        "changelogSections": {
            "Fixed": ["Improve update restart recovery."],
            "Security": ["Strengthen public report previews."],
        },
        "dockerTag": "0.9.15",
    }
    monkeypatch.setattr(module, "load_exporter", lambda: SimpleNamespace(collect_metadata=lambda *args: metadata))
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.15"])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.15 - Update and reporting reliability\n")
    assert "## Fixed" in output
    assert "## Security" in output
    assert "`coderluii/channelwatch:0.9.15`" in output


def test_release_body_preserves_0910_repair_copy(monkeypatch, capsys):
    module = _load_script(
        "render_release_body_0910",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v0.9.10",
        "changelogHighlights": ["Show v0.9.10 as container image update required"],
        "dockerTag": "0.9.10",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.10"])

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.10 - Runtime and Config Repair\n")
    assert "Show v0.9.10 as container image update required" in output
    assert "\n## Docs\n" in output
    assert "\n## Images\n" in output


def test_update_bundle_highlights_come_from_changelog():
    module = _load_script(
        "build_update_bundle",
        "scripts/release/build-update-bundle.py",
    )
    exporter = SimpleNamespace(
        parse_changelog=lambda version: {
            "changelogHighlights": [
                "Keep idle Channels DVR event streams connected.",
                "Stop dashboard stream requests from rebuilding core settings.",
            ]
        }
    )

    assert module.release_highlights("0.9.12", exporter=exporter) == [
        "Keep idle Channels DVR event streams connected.",
        "Stop dashboard stream requests from rebuilding core settings.",
    ]


def test_update_bundle_copies_required_legal_files(tmp_path):
    module = _load_script(
        "build_update_bundle_legal_files",
        "scripts/release/build-update-bundle.py",
    )

    module.copy_release_legal_files(tmp_path, version="1.1.0")

    legal_root = tmp_path / "core" / "release_legal"
    assert (legal_root / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert (legal_root / "NOTICE").read_bytes() == (
        ROOT / "docs/legal/NOTICE"
    ).read_bytes()
    for name in ("THIRD_PARTY_LICENSES.md", "CORRESPONDING_SOURCE.md"):
        rendered = (legal_root / name).read_text()
        assert rendered.startswith("# ChannelWatch v1.1.0 - ")
        assert rendered.split("\n", 1)[1] == (ROOT / "docs/legal" / name).read_text().split("\n", 1)[1]


def test_runtime_application_staging_uses_image_payload_roots(tmp_path, monkeypatch):
    update_builder = _load_script(
        "build_update_bundle_runtime_staging",
        "scripts/release/build-update-bundle.py",
    )
    runtime_builder = _load_script(
        "build_runtime_bundle_staging_contract",
        "scripts/release/build-runtime-bundle.py",
    )
    fixture_root = tmp_path / "source"
    for relative, content in {
        "app/core/main.py": "core\n",
        "app/ui/backend/main.py": "backend\n",
        "app/ui/out/index.html": "frontend\n",
        "app/ui/public/images/logo.svg": "<svg/>\n",
    }.items():
        path = fixture_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(update_builder, "ROOT", fixture_root)
    monkeypatch.setattr(
        update_builder,
        "copy_release_legal_files",
        lambda root, **_kwargs: (
            (root / "core" / "release_legal").mkdir(parents=True),
            (root / "core" / "release_legal" / "LICENSE").write_text(
                "license\n", encoding="utf-8"
            ),
        ),
    )
    monkeypatch.setattr(update_builder, "copy_copyleft_release_files", lambda _root: None)
    stage = tmp_path / "runtime-app"

    update_builder.stage_runtime_application_payload(stage, version="1.5.0")

    payload = runtime_builder._collect_payload(
        stage,
        allowed_prefixes=("core/", "ui/backend/"),
        label="Application payload",
    )
    assert "core/main.py" in payload
    assert "ui/backend/main.py" in payload
    assert "core/release_legal/LICENSE" in payload
    assert any(path.startswith("ui/backend/static_ui/") for path in payload)
    assert not any(path.startswith("app/") for path in payload)
    assert not any(".git" in path.split("/") for path in payload)


def test_update_bundle_zip_is_reproducible_and_normalizes_metadata(tmp_path):
    module = _load_script(
        "build_update_bundle_reproducible_zip",
        "scripts/release/build-update-bundle.py",
    )
    source = tmp_path / "source"
    source.mkdir()
    ordinary = source / "ordinary.py"
    executable = source / "helper"
    ordinary.write_bytes(b"ordinary\n")
    executable.write_bytes(b"executable\n")
    executable.chmod(0o755)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    module.write_zip(source, first, source_date_epoch=1_900_000_000)
    os.utime(ordinary, (1_800_000_000, 1_800_000_000))
    os.utime(executable, (1_700_000_000, 1_700_000_000))
    module.write_zip(source, second, source_date_epoch=1_900_000_000)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["helper", "ordinary.py"]
        modes = {
            info.filename: (info.external_attr >> 16) & 0o777
            for info in archive.infolist()
        }
        assert modes == {"helper": 0o755, "ordinary.py": 0o644}


def test_update_bundle_enforces_24_hour_automatic_install_delay():
    module = _load_script(
        "build_update_bundle_automatic_delay",
        "scripts/release/build-update-bundle.py",
    )

    assert module.resolve_automatic_install_after(
        "2026-08-24T00:00:00Z", None
    ) == "2026-08-25T00:00:00Z"
    with pytest.raises(ValueError, match="at least 24 hours"):
        module.resolve_automatic_install_after(
            "2026-08-24T00:00:00Z", "2026-08-24T23:59:59Z"
        )
    assert module.resolve_publication_time(
        "2026-08-25T00:00:00Z",
        None,
        "2026-08-25T06:00:00Z",
    ) == "2026-08-25T06:00:00Z"
    with pytest.raises(ValueError, match="earlier than"):
        module.resolve_publication_time(
            "2026-08-25T06:00:01Z",
            "2026-08-25T06:00:00Z",
            None,
        )
    with pytest.raises(ValueError, match="required"):
        module.resolve_publication_time("2026-08-25T00:00:00Z", None, None)


def test_update_bundle_requires_explicit_catalog_compatibility():
    module = _load_script(
        "build_update_bundle_explicit_compatibility",
        "scripts/release/build-update-bundle.py",
    )

    sources, protocols = module.explicit_catalog_compatibility(
        command_source_versions=None,
        command_launcher_protocols=None,
        configured_source_versions=["0.9.18"],
        configured_launcher_protocols=[3, 1, 2],
    )
    assert sources == ["0.9.18"]
    assert protocols == [1, 2, 3]

    with pytest.raises(ValueError, match="explicit non-empty source-version"):
        module.explicit_catalog_compatibility(
            command_source_versions=None,
            command_launcher_protocols=None,
            configured_source_versions=None,
            configured_launcher_protocols=[1, 2, 3],
        )
    with pytest.raises(ValueError, match="explicit non-empty launcher-protocol"):
        module.explicit_catalog_compatibility(
            command_source_versions=None,
            command_launcher_protocols=None,
            configured_source_versions=["0.9.18"],
            configured_launcher_protocols=None,
        )


def test_update_bundle_retains_deterministic_v2_catalog_history(tmp_path):
    module = _load_script(
        "build_update_bundle_catalog_history",
        "scripts/release/build-update-bundle.py",
    )
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            {
                "schema": 1,
                "releases": [
                    {"version": "0.9.18", "marker": "bridge"},
                    {"version": "0.9.19", "marker": "intermediate"},
                ],
            }
        ),
        encoding="utf-8",
    )

    retained = module.load_catalog_history("0.9.20", path=history)

    assert [release["version"] for release in retained] == ["0.9.19", "0.9.18"]
    assert retained[1]["marker"] == "bridge"

    history.write_text('{"schema":1,"releases":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="retain the permanent v0.9.18 bridge"):
        module.load_catalog_history("0.9.20", path=history)
    assert module.load_catalog_history("0.9.18", path=history) == []


def test_future_release_verifier_requires_the_exact_pinned_v1_bridge():
    module = _load_script(
        "verify_pinned_v1_bridge",
        "scripts/release/verify-pinned-v1-bridge.py",
    )
    payload = {
        "version": "0.9.18",
        "version_tag": "v0.9.18",
        "minimum_image_version": "0.9.11",
        "updater_protocol": 2,
        "recommended_image_version": "0.9.18",
    }

    class Verifier:
        def verify_update_assets(self, _manifest, _bundle, **expected):
            assert expected["expected_version"] == "0.9.18"
            assert expected["expected_bundle_url"] == module.BRIDGE_BUNDLE_URL
            return {"payload": dict(payload)}

    assert module.verify_bridge_bytes(b"manifest", b"bundle", verifier=Verifier())[
        "payload"
    ] == payload

    payload["minimum_image_version"] = "0.9.10"
    with pytest.raises(ValueError, match="not pinned"):
        module.verify_bridge_bytes(b"manifest", b"bundle", verifier=Verifier())


def test_publication_window_is_bound_to_actual_public_availability():
    module = _load_script(
        "verify_publication_window",
        "scripts/release/verify-publication-window.py",
    )
    publication = datetime(2026, 8, 25, 6, tzinfo=timezone.utc)
    automatic = datetime(2026, 8, 27, 6, tzinfo=timezone.utc)
    catalog = {
        "schema": 2,
        "payload": {
            "releases": [
                {
                    "version": "0.9.18",
                    "publication_time": publication.isoformat(),
                    "automatic_install_after": automatic.isoformat(),
                }
            ]
        },
    }

    assert module.verify_publication_window(
        catalog,
        version="0.9.18",
        prospective_publication=automatic - timedelta(hours=24),
    ) == (publication, automatic)
    with pytest.raises(ValueError, match="full 24-hour"):
        module.verify_publication_window(
            catalog,
            version="0.9.18",
            prospective_publication=automatic - timedelta(hours=24) + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="still in the future"):
        module.verify_publication_window(
            catalog,
            version="0.9.18",
            prospective_publication=publication - timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="cannot be less than 24 hours"):
        module.verify_publication_window(
            catalog,
            version="0.9.18",
            prospective_publication=publication,
            minimum_delay=timedelta(hours=23),
        )
    with pytest.raises(ValueError, match="include a timezone"):
        module.verify_publication_window(
            catalog,
            version="0.9.18",
            prospective_publication=datetime(2026, 8, 25, 6),
        )


@pytest.mark.parametrize("release_count", (0, 2))
def test_publication_window_requires_one_exact_release(release_count: int):
    module = _load_script(
        f"verify_publication_window_count_{release_count}",
        "scripts/release/verify-publication-window.py",
    )
    release = {
        "version": "0.9.18",
        "publication_time": "2026-08-25T12:00:00Z",
        "automatic_install_after": "2026-08-27T06:00:00Z",
    }
    catalog = {
        "schema": 2,
        "payload": {"releases": [dict(release) for _ in range(release_count)]},
    }
    with pytest.raises(ValueError, match="exactly one"):
        module.verify_publication_window(
            catalog,
            version="0.9.18",
            prospective_publication=datetime(2026, 8, 25, 6, tzinfo=timezone.utc),
        )


def test_oci_descriptor_follows_and_validates_nested_image_index(tmp_path: Path):
    module = _load_script(
        "describe_oci_image",
        "scripts/release/describe-oci-image.py",
    )
    layout, nested_digest = _synthetic_oci_layout(tmp_path)

    described = module.describe_layout(layout)

    assert described["image_index"]["digest"] == nested_digest
    assert [item["platform"] for item in described["platforms"]] == [
        "linux/amd64",
        "linux/arm64",
    ]
    assert f"sha256:{hashlib.sha256((layout / 'index.json').read_bytes()).hexdigest()}" != nested_digest


@pytest.mark.parametrize(
    "platforms",
    (("amd64",), ("amd64", "amd64"), ("amd64", "arm64", "s390x")),
)
def test_oci_descriptor_rejects_missing_duplicate_or_extra_platforms(
    tmp_path: Path, platforms: tuple[str, ...]
):
    module = _load_script(
        f"describe_oci_platforms_{len(platforms)}_{platforms[-1]}",
        "scripts/release/describe-oci-image.py",
    )
    layout, _digest = _synthetic_oci_layout(tmp_path, platforms=platforms)

    with pytest.raises(ValueError, match="exactly linux/amd64 and linux/arm64"):
        module.describe_layout(layout)


def test_oci_descriptor_rejects_blob_tampering_and_wrong_size(tmp_path: Path):
    module = _load_script(
        "describe_oci_tampering",
        "scripts/release/describe-oci-image.py",
    )
    layout, nested_digest = _synthetic_oci_layout(tmp_path)
    nested_blob = layout / "blobs" / "sha256" / nested_digest.removeprefix("sha256:")
    nested_blob.write_bytes(nested_blob.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="does not match"):
        module.describe_layout(layout)

    layout, _nested_digest = _synthetic_oci_layout(tmp_path / "second")
    root = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    root["manifests"][0]["size"] += 1
    (layout / "index.json").write_text(json.dumps(root), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        module.describe_layout(layout)


def test_oci_descriptor_rejects_variant_and_missing_media_type(tmp_path: Path):
    module = _load_script(
        "describe_oci_variant_media",
        "scripts/release/describe-oci-image.py",
    )
    variant, _digest = _synthetic_oci_layout(tmp_path / "variant", include_variant=True)
    with pytest.raises(ValueError, match="unreviewed variants"):
        module.describe_layout(variant)

    missing_media, _digest = _synthetic_oci_layout(
        tmp_path / "missing-media", omit_config_media_type=True
    )
    with pytest.raises(ValueError, match="invalid media type"):
        module.describe_layout(missing_media)


def test_oci_descriptor_rejects_invalid_layout_metadata(tmp_path: Path):
    module = _load_script(
        "describe_oci_layout_metadata",
        "scripts/release/describe-oci-image.py",
    )
    layout, _digest = _synthetic_oci_layout(tmp_path)
    (layout / "oci-layout").write_text(
        json.dumps({"imageLayoutVersion": "0.9.0"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="imageLayoutVersion 1.0.0"):
        module.describe_layout(layout)


def test_copyleft_license_manifest_is_pinned_and_complete():
    module = _load_script(
        "copyleft_release_licenses",
        "scripts/release/copyleft_licenses.py",
    )

    assert module.SPDX_LICENSE_LIST_COMMIT == (
        "5bf6d9610255540bfbee6890765a616042bf1e11"
    )
    assert {artifact.filename for artifact in module.COPYLEFT_LICENSES} == {
        "GPL-1.0-only.txt",
        "GPL-2.0-only.txt",
        "GPL-3.0-only.txt",
        "LGPL-2.0-only.txt",
        "LGPL-2.1-only.txt",
        "LGPL-3.0-only.txt",
        "GCC-exception-3.1.txt",
    }
    expected_digests = {
        artifact.filename: artifact.sha256 for artifact in module.COPYLEFT_LICENSES
    }
    assert expected_digests["LGPL-2.0-only.txt"] == (
        "86dc99d7e5060915ab1dfc1378b7dd351c62088bfa74067e8aa1868c6fdba7d8"
    )
    assert expected_digests["LGPL-3.0-only.txt"] == (
        "996af0513df21f7496288951c41428a03c174e9e4a9d63665c57d670f845ccb1"
    )
    assert all(
        len(artifact.sha256) == 64
        and artifact.url.startswith(
            "https://raw.githubusercontent.com/spdx/license-list-data/"
            f"{module.SPDX_LICENSE_LIST_COMMIT}/text/"
        )
        for artifact in module.COPYLEFT_LICENSES
    )


def test_copyleft_license_download_retries_transport_errors(monkeypatch):
    module = _load_script(
        "copyleft_license_transport_retry", "scripts/release/copyleft_licenses.py"
    )
    payload = b"license text\n"
    artifact = module.LicenseArtifact("GPL-3.0-only.txt", hashlib.sha256(payload).hexdigest())
    calls = []
    sleeps = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise urllib.error.URLError(ConnectionResetError("connection reset"))
        response = io.BytesIO(payload)
        response.geturl = lambda: artifact.url
        return response

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)

    assert module._download(artifact) == payload
    assert calls == [artifact.url] * 3
    assert sleeps == [1, 2]


def test_copyleft_license_download_transport_retry_is_bounded(monkeypatch):
    module = _load_script(
        "copyleft_license_transport_bound", "scripts/release/copyleft_licenses.py"
    )
    calls = []
    sleeps = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.URLError(ConnectionResetError("connection reset"))

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(urllib.error.URLError):
        module._download(module.COPYLEFT_LICENSES[0])
    assert len(calls) == 3
    assert sleeps == [1, 2]


@pytest.mark.parametrize("invalid", ["digest", "redirect"])
def test_copyleft_license_download_does_not_retry_untrusted_content(monkeypatch, invalid):
    module = _load_script(
        "copyleft_license_content_boundary", "scripts/release/copyleft_licenses.py"
    )
    payload = b"license text\n"
    digest = hashlib.sha256(payload).hexdigest() if invalid == "redirect" else "0" * 64
    artifact = module.LicenseArtifact("GPL-3.0-only.txt", digest)
    calls = []
    sleeps = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        response = io.BytesIO(payload)
        response.geturl = lambda: (
            "https://example.com/license.txt" if invalid == "redirect" else artifact.url
        )
        return response

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(ValueError):
        module._download(artifact)
    assert calls == [artifact.url]
    assert sleeps == []


def test_copyleft_license_download_does_not_retry_http_errors(monkeypatch):
    module = _load_script(
        "copyleft_license_http_boundary", "scripts/release/copyleft_licenses.py"
    )
    calls = []
    sleeps = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(urllib.error.HTTPError):
        module._download(module.COPYLEFT_LICENSES[0])
    assert len(calls) == 1
    assert sleeps == []


def test_copyleft_license_archive_is_deterministic(tmp_path):
    module = _load_script(
        "copyleft_release_archive",
        "scripts/release/copyleft_licenses.py",
    )
    source = tmp_path / "licenses"
    source.mkdir()
    (source / "GPL-3.0-only.txt").write_text("GPL text\n", encoding="utf-8")
    (source / "CORRESPONDING_SOURCE.md").write_text(
        "# Source map\n", encoding="utf-8"
    )
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    module.write_deterministic_archive(source, first)
    os.utime(source / "GPL-3.0-only.txt", (1_900_000_000, 1_900_000_000))
    module.write_deterministic_archive(source, second)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == [
            "CORRESPONDING_SOURCE.md",
            "GPL-3.0-only.txt",
        ]


def test_copyleft_archive_includes_exact_image_license_material(tmp_path):
    module = _load_script(
        "copyleft_exact_image_licenses",
        "scripts/release/copyleft_licenses.py",
    )
    output = tmp_path / "licenses"
    output.mkdir()
    for architecture, marker in (("amd64", "amd64"), ("arm64", "arm64")):
        image_root = tmp_path / architecture
        common = image_root / "usr/share/common-licenses"
        package_doc = image_root / "usr/share/doc/libexample"
        empty_package_doc = image_root / "usr/share/doc/perl"
        common.mkdir(parents=True)
        package_doc.mkdir(parents=True)
        empty_package_doc.mkdir(parents=True)
        (common / "MPL-2.0").write_text("MPL full text\n", encoding="utf-8")
        (package_doc / "copyright").write_text(
            f"Debian copyright {marker}\n", encoding="utf-8"
        )
        (package_doc / "README.Debian").write_text(
            "not license material\n", encoding="utf-8"
        )

        module.copy_image_license_material(image_root, output, architecture)

    archive_path = tmp_path / "licenses.zip"
    module.write_deterministic_archive(output, archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert (
            "image-licenses/amd64/usr/share/common-licenses/MPL-2.0" in names
        )
        assert (
            "image-licenses/arm64/usr/share/common-licenses/MPL-2.0" in names
        )
        assert "image-licenses/amd64/usr/share/doc/libexample/copyright" in names
        assert "image-licenses/arm64/usr/share/doc/libexample/copyright" in names
        assert not any(name.endswith("README.Debian") for name in names)
        assert archive.read(
            "image-licenses/amd64/usr/share/common-licenses/MPL-2.0"
        ) == b"MPL full text\n"
        assert archive.read(
            "image-licenses/arm64/usr/share/common-licenses/MPL-2.0"
        ) == b"MPL full text\n"
        assert archive.read(
            "image-licenses/amd64/usr/share/doc/libexample/copyright"
        ) == b"Debian copyright amd64\n"
        assert archive.read(
            "image-licenses/arm64/usr/share/doc/libexample/copyright"
        ) == b"Debian copyright arm64\n"


def test_image_license_copy_rejects_nonempty_package_without_copyright(tmp_path):
    module = _load_script(
        "copyleft_image_license_nonempty_package",
        "scripts/release/copyleft_licenses.py",
    )
    image_root = tmp_path / "image"
    common = image_root / "usr/share/common-licenses"
    package_doc = image_root / "usr/share/doc/libexample"
    common.mkdir(parents=True)
    package_doc.mkdir(parents=True)
    (common / "MPL-2.0").write_text("MPL full text\n", encoding="utf-8")
    (package_doc / "README.Debian").write_text(
        "package documentation\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Image license file is missing"):
        module.copy_image_license_material(image_root, tmp_path / "out", "amd64")


def test_image_license_copy_rejects_non_directory_package_entry(tmp_path):
    module = _load_script(
        "copyleft_image_license_non_directory_package",
        "scripts/release/copyleft_licenses.py",
    )
    image_root = tmp_path / "image"
    common = image_root / "usr/share/common-licenses"
    package_docs = image_root / "usr/share/doc"
    common.mkdir(parents=True)
    package_docs.mkdir(parents=True)
    (common / "MPL-2.0").write_text("MPL full text\n", encoding="utf-8")
    (package_docs / "README").write_text("unexpected entry\n", encoding="utf-8")

    with pytest.raises(ValueError, match="is not a directory"):
        module.copy_image_license_material(image_root, tmp_path / "out", "amd64")


def test_image_license_copy_rejects_empty_package_symlink_outside_root(tmp_path):
    module = _load_script(
        "copyleft_image_license_empty_symlink_boundary",
        "scripts/release/copyleft_licenses.py",
    )
    image_root = tmp_path / "image"
    common = image_root / "usr/share/common-licenses"
    package_docs = image_root / "usr/share/doc"
    common.mkdir(parents=True)
    package_docs.mkdir(parents=True)
    (common / "MPL-2.0").write_text("MPL full text\n", encoding="utf-8")
    outside = tmp_path / "outside-empty-doc"
    outside.mkdir()
    try:
        (package_docs / "perl").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(ValueError, match="escapes image root"):
        module.copy_image_license_material(image_root, tmp_path / "out", "amd64")


def test_image_license_copy_rejects_symlinks_outside_exported_root(tmp_path):
    module = _load_script(
        "copyleft_image_license_boundary",
        "scripts/release/copyleft_licenses.py",
    )
    image_root = tmp_path / "image"
    common = image_root / "usr/share/common-licenses"
    package_doc = image_root / "usr/share/doc/libexample"
    common.mkdir(parents=True)
    package_doc.mkdir(parents=True)
    (package_doc / "copyright").write_text("package notice\n", encoding="utf-8")
    outside = tmp_path / "outside-license"
    outside.write_text("host content\n", encoding="utf-8")
    try:
        (common / "escaped").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(ValueError, match="escapes image root"):
        module.copy_image_license_material(image_root, tmp_path / "out", "amd64")


def test_corresponding_source_map_pins_exact_release_sources():
    source_map = (ROOT / "docs/legal/CORRESPONDING_SOURCE.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83",
        "a66d3a463d0be3cc537c269b0d3418746dda512417a975559a0eaa8a6406baaf",
        "0e24db28cf5b7788413afe3fdc63075ca22bc4ecf74f5472304b391c2110e629",
        "7cc547b3ff8d45d540cd23144227af126a79d60c",
        "c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73",
        "glibc/2.36-9+deb12u14",
        "gcc-12/12.2.0-14+deb12u1",
        "gmp/2%3A6.2.1+dfsg1-1.1",
        "gnutls28/3.7.9-2+deb12u7",
        "util-linux/2.38.1-5+deb12u3",
        "libunistring/1.0-2",
        "openssl/3.0.22-1~deb12u1",
        "zeroconf 0.151.5",
        "28c2ec9d772007eedf11b41a9c9fd3d5c684c17b00721ff8f1ee31b20ad286a1",
        "5bf6d9610255540bfbee6890765a616042bf1e11",
        "996af0513df21f7496288951c41428a03c174e9e4a9d63665c57d670f845ccb1",
        "86dc99d7e5060915ab1dfc1378b7dd351c62088bfa74067e8aa1868c6fdba7d8",
    ):
        assert required in source_map


def test_release_config_declares_161_dependency_update_on_published_153_image():
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )
    policy_module = _load_script(
        "release_version_policy_current_dependency_update",
        "scripts/release/release_version_policy.py",
    )

    assert config["version"] == "1.6.1"
    assert config["image_required"] is False
    assert config["delivery_mode"] == "app_update"
    assert config["minimum_image_version"] == "1.5.3"
    assert config["updater_protocol"] == 4
    assert config["minimum_launcher_protocol"] == 4
    assert config["recommended_image_version"] == "1.6.1"
    assert config["publication_time"].endswith("Z")
    assert config["automatic_install_after"].endswith("Z")
    assert config["automatic_install_allowed"] is False
    assert config["compatible_source_application_versions"] == ["1.5.3", "1.6.0"]
    assert config["compatible_launcher_protocols"] == [4]
    assert config["runtime_generation_required"] is True
    assert not config.get("runtime_variants")
    assert re.fullmatch(r"[0-9a-f]{64}", config["runtime_bootstrap_sha256"])
    assert not config.get("runtime_descriptor")
    assert config["minimum_image_sha256"] == (
        "sha256:" + config["runtime_bootstrap_sha256"]
    )
    assert config["runtime_compatibility_evidence"] == {}
    assert "recovers_unpublished_image_milestone" not in config
    assert config["unpublished_predecessor_tags"] == []
    assert config["unpublished_release_tags"] == [
        "v1.4.2",
        "v1.4.3",
        "v1.4.4",
        "v1.5.0",
        "v1.5.1",
        "v1.5.2",
        "v1.5.4",
    ]
    assert config["release_heading"] == "## 10/19/2026"
    assert config["verification_assets"] is True
    policy = policy_module.validate_release_config(config)
    assert policy.image_milestone is False
    assert policy.retained_capability_floor is True
    assert policy.minimum_image_version == "1.5.3"
    history = json.loads(
        (ROOT / "scripts/release/update-catalog-history.json").read_text(
            encoding="utf-8"
        )
    )["releases"]
    assert "1.5.0" not in {release["version"] for release in history}
    assert "1.5.1" not in {release["version"] for release in history}
    assert "1.5.2" not in {release["version"] for release in history}
    foundation = next(release for release in history if release["version"] == "1.5.3")
    assert foundation["image_required"] is True
    assert foundation["delivery_mode"] == "image_required"
    assert foundation["minimum_image_version"] == "1.5.3"
    assert foundation["updater_protocol"] == 4
    release_141 = next(release for release in history if release["version"] == "1.4.1")
    assert release_141["bundle_sha256"] == (
        "89c4cd4f0d0aebff5224e84c5a51a28c514067cd66486ca26cbde3f17e96c8cd"
    )
    assert release_141["published_at"] == "2026-10-10T19:10:00Z"
    publication = datetime.fromisoformat(
        config["publication_time"].removesuffix("Z") + "+00:00"
    )
    automatic = datetime.fromisoformat(
        config["automatic_install_after"].removesuffix("Z") + "+00:00"
    )
    assert publication.tzinfo == timezone.utc
    assert automatic.tzinfo == timezone.utc
    assert automatic - publication == timedelta(days=7)


def test_current_release_body_uses_public_release_heading_hierarchy(
    monkeypatch, capsys
):
    module = _load_script(
        "render_release_body_current",
        "scripts/release/render-release-body.py",
    )
    config = json.loads(module.RELEASE_CONFIG.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["render-release-body.py", "--version", config["version"]],
    )

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.splitlines()[0] == config["release_heading"]
    assert re.fullmatch(r"## \d{2}/\d{2}/\d{4}", output.splitlines()[0])
    assert re.findall(r"^## .+$", output, re.MULTILINE) == [
        config["release_heading"]
    ]
    for heading in ("Important", "Added", "Changed", "Fixed", "Docs", "Images"):
        assert f"\n### {heading}\n" in output
    assert "\n### License and verification\n" in output
    assert "\n### Security\n" not in output
    assert "channelwatch-v1.6.1-THIRD-PARTY-LICENSES.md" in output
    assert "`coderluii/channelwatch:1.6.1`" in output
    assert "`ghcr.io/coderluii/channelwatch:1.6.1`" in output
    assert "scratch/" not in output
    assert "C:\\" not in output
    assert "file://" not in output


def test_release_version_policy_enforces_single_digit_patch_cadence():
    module = _load_script(
        "release_version_policy",
        "scripts/release/release_version_policy.py",
    )

    milestone = module.validate_release_config(
        {
            "version": "1.2.0",
            "image_required": True,
            "delivery_mode": "image_required",
            "minimum_image_version": "1.2.0",
            "automatic_install_allowed": False,
        }
    )
    assert milestone.image_milestone is True

    patch_release = module.validate_release_config(
        {
            "version": "1.2.9",
            "image_required": False,
            "delivery_mode": "app_update",
            "minimum_image_version": "1.2.0",
        }
    )
    assert patch_release.image_milestone is False

    with pytest.raises(ValueError, match="patch versions stop at 9"):
        module.release_version_policy("1.2.10")
    with pytest.raises(ValueError, match="X.Y.0 releases"):
        module.validate_release_config(
            {
                "version": "1.3.0",
                "image_required": False,
                "delivery_mode": "app_update",
                "minimum_image_version": "1.3.0",
            }
        )
    with pytest.raises(ValueError, match="must remain in-app"):
        module.validate_release_config(
            {
                "version": "1.3.1",
                "image_required": True,
                "delivery_mode": "image_required",
                "minimum_image_version": "1.3.0",
            }
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"updater_protocol": 2},
        {"minimum_launcher_protocol": 3},
        {"compatible_launcher_protocols": [3]},
        {"runtime_generation_required": True},
        {"runtime_bootstrap_sha256": "a" * 64},
        {"runtime_variants": []},
        {"runtime_descriptor": {}},
    ],
)
def test_release_version_policy_rejects_invalid_dependency_image_milestones(
    mutation,
):
    module = _load_script(
        "dependency_image_milestone_policy",
        "scripts/release/release_version_policy.py",
    )
    config = {
        "version": "1.5.0",
        "image_required": True,
        "delivery_mode": "image_required",
        "minimum_image_version": "1.5.0",
        "automatic_install_allowed": False,
        "updater_protocol": 4,
        "minimum_launcher_protocol": 4,
        "compatible_launcher_protocols": [4],
        "runtime_generation_required": False,
        **mutation,
    }

    with pytest.raises(ValueError, match="protocol-4 image milestone"):
        module.validate_release_config(config)


def test_release_impact_classifier_keeps_v1_patch_in_app():
    module = _load_script(
        "release_impact_version_policy",
        "scripts/release/classify-release-impact.py",
    )
    empty = module.classify_paths([])
    policy_module = _load_script(
        "release_version_policy_for_classifier",
        "scripts/release/release_version_policy.py",
    )
    policy = policy_module.release_version_policy("1.4.1")

    result = module.apply_release_version_policy(empty, policy)

    assert result.delivery_mode == "app_update"
    assert result.image_required is False
    assert result.triggering_paths == ()


def test_release_version_surfaces_use_161_release():
    module = _load_script(
        "export_release_metadata",
        "scripts/release/export-site-release-metadata.py",
    )

    metadata = module.collect_metadata(
        source_ref=None,
        release_url=None,
    )

    assert metadata["version"] == "1.6.1"
    assert metadata["versionTag"] == "v1.6.1"
    assert metadata["dockerTag"] == "1.6.1"
    assert metadata["helmChartVersion"] == "1.6.1"
    assert metadata["helmAppVersion"] == "1.6.1"


def test_release_body_for_120_links_license_and_sbom_assets(
    monkeypatch, capsys, tmp_path
):
    module = _load_script(
        "render_release_body_100_legal_assets",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v1.2.0",
        "releaseDate": "2026-08-28",
        "changelogHighlights": [
            "v0.9.9 needs one image pull while preserving /config.",
            "Bundle release license notices.",
        ],
        "changelogSections": {
            "Important": [
                "v0.9.9 needs one image pull while preserving /config."
            ],
            "Security": ["Bundle release license notices."],
        },
        "dockerTag": "1.2.0",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    release_config = tmp_path / "release-config.json"
    release_config.write_text(
        json.dumps(
            {
                "version": "1.2.0",
                "release_heading": (
                    "# ChannelWatch v1.2.0 - Updated container dependencies"
                ),
                "verification_assets": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "RELEASE_CONFIG", release_config)
    monkeypatch.setattr(
        sys,
        "argv",
        ["render-release-body.py", "--version", "1.2.0"],
    )

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith(
        "# ChannelWatch v1.2.0 - Updated container dependencies\n"
    )
    assert "## Important" in output
    assert "v0.9.9 needs one image pull while preserving /config." in output
    assert output.index("## Important") < output.index("## Security")
    assert "## License and verification" in output
    assert "channelwatch-v1.2.0-THIRD-PARTY-LICENSES.md" in output
    assert "channelwatch-v1.2.0-CORRESPONDING-SOURCE.md" in output
    assert "channelwatch-v1.2.0-COPYLEFT-LICENSES.zip" in output
    assert "channelwatch-v1.2.0-SHA256SUMS.txt" in output
    assert "Exact amd64 and arm64 SPDX and CycloneDX SBOMs" in output
    assert "every other attached asset is covered" in output
    assert "`coderluii/channelwatch:1.2.0`" in output
    assert "`coderluii/channelwatch:latest`" in output
    assert "`ghcr.io/coderluii/channelwatch:1.2.0`" in output
    assert "`ghcr.io/coderluii/channelwatch:latest`" in output


def test_release_workflow_uses_explicit_config_and_python_gate():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts/release/release-config.json" in workflow
    assert 'grep -Eiq "container image update required|image-required"' not in workflow
    assert "python -m pytest app/core/tests" in workflow
    assert "python -m compileall app/core app/ui/backend" in workflow
    assert 'expected_heading="$(jq -r \'.release_heading\' scripts/release/release-config.json)"' in workflow
    assert "GitHub Release body must start with '${expected_heading}'" in workflow
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:",
        1,
    )[0]
    assert "fetch-depth: 0" in release_job
    assert '.draft and .tag_name == \\"${TAG}\\"' in release_job
    assert "is already published and is immutable" in release_job
    create_draft_step = release_job.split(
        "      - name: Create or update draft GitHub Release", 1
    )[1].split("\n      - name: Upload update assets", 1)[0]
    assert 'release_json=""' in create_draft_step
    assert 'if ! release_json="$(gh api ' in create_draft_step
    assert "JSON 404 body" in create_draft_step
    assert "2>/dev/null || true" not in create_draft_step
    assert "--arg tag_name \"${TAG}\"" in release_job
    assert "--arg target_commitish \"${RELEASE_SHA}\"" in release_job


@pytest.mark.parametrize(
    "step_name",
    (
        "Download and verify existing draft release assets",
        "Attach verified license and SBOM assets to draft release",
        "Publish release after image verification gates",
    ),
)
def test_release_workflow_resolves_drafts_from_authenticated_release_list(
    step_name,
):
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    _block, shell = _workflow_shell_step(workflow, step_name)

    # GitHub's release-by-tag endpoint returns 404 for an unpublished draft,
    # even though that draft is present in the authenticated release list.
    assert 'releases/tags/${TAG}' not in shell
    assert 'releases?per_page=100' in shell
    assert '.draft == true and .tag_name == $tag' in shell
    assert 'Exactly one draft release for ${TAG} is required.' in shell or (
        'missing or already published; published releases are immutable.' in shell
    )


def test_release_workflow_passes_version_between_isolated_action_shells(tmp_path):
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    _parse_block, parse_shell = _workflow_shell_step(workflow, "Parse version")
    verify_block, verify_shell = _workflow_shell_step(
        workflow, "Verify CHANGELOG entry"
    )
    output_path = tmp_path / "github-output"
    release_version = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )["version"]
    release_tag = f"v{release_version}"
    parse_env = {
        **os.environ,
        "RELEASE_TAG": release_tag,
        "GITHUB_OUTPUT": str(output_path),
    }

    parsed = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", parse_shell],
        cwd=ROOT,
        env=parse_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert parsed.returncode == 0, parsed.stderr
    output_values = dict(
        line.split("=", 1)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    )
    assert output_values == {"version": release_version}
    assert "VERSION: ${{ steps.version.outputs.version }}" in verify_block
    assert 'version_re="${VERSION//' in verify_shell
    assert 'version_re="${version//' not in verify_shell

    shell_root = tmp_path / "shell-root"
    changelog = shell_root / "docs" / "releases" / "CHANGELOG.md"
    changelog.parent.mkdir(parents=True)
    changelog.write_text(
        (ROOT / "docs/releases/CHANGELOG.md").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )

    verified = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", verify_shell],
        cwd=shell_root,
        env={
            **os.environ,
            "RELEASE_TAG": release_tag,
            "VERSION": output_values["version"],
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert verified.returncode == 0, verified.stderr


def test_release_impact_classifier_keeps_app_only_changes_bundle_compatible():
    module = _load_script(
        "classify_release_impact_app_only",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(
        [
            "app/core/main.py",
            "app/ui/components/report-problem-dialog.tsx",
            "app/ui/backend/support_report.py",
            "app/core/tests/test_support_report.py",
            "docs/releases/CHANGELOG.md",
        ]
    )

    assert result.image_required is False
    assert result.triggering_paths == ()


def test_release_impact_classifier_keeps_bundle_packaging_app_deliverable():
    module = _load_script(
        "classify_release_impact_bundle_packaging",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(["scripts/release/build-update-bundle.py"])

    assert result.delivery_mode == "app_update"
    assert result.image_required is False


def test_release_impact_classifier_allows_fresh_notification_defaults_only():
    module = _load_script(
        "classify_release_impact_fresh_notification_defaults",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/core/docker-entrypoint.py": (
            "APP = 'channelwatch'\n"
            "DEFAULT_SETTINGS = {\n"
            "    'alert_channel_watching': True,\n"
            "    'rd_alert_failed': False,\n"
            "}\n"
            "def main():\n"
            "    return APP\n"
        )
    }
    after = {
        "app/core/docker-entrypoint.py": (
            "APP = 'channelwatch'\n"
            "DEFAULT_SETTINGS = {\n"
            "    'alert_channel_watching': False,\n"
            "    'rd_alert_failed': True,\n"
            "    'notification_preferences_version': 1,\n"
            "}\n"
            "def main():\n"
            "    return APP\n"
        )
    }

    result = module.classify_changes(before, after)

    assert result.delivery_mode == "app_update"
    assert result.image_required is False
    assert result.triggering_paths == ()


def test_release_impact_classifier_rejects_other_entrypoint_default_changes():
    module = _load_script(
        "classify_release_impact_other_entrypoint_defaults",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/core/docker-entrypoint.py": "DEFAULT_SETTINGS = {'auth_mode': ''}\n"
    }
    after = {
        "app/core/docker-entrypoint.py": "DEFAULT_SETTINGS = {'auth_mode': 'none'}\n"
    }

    result = module.classify_changes(before, after)

    assert result.delivery_mode == "image_required"
    assert result.image_required is True
    assert result.triggering_paths == ("app/core/docker-entrypoint.py",)


def test_release_impact_classifier_rejects_entrypoint_runtime_change_with_defaults():
    module = _load_script(
        "classify_release_impact_entrypoint_runtime_with_defaults",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/core/docker-entrypoint.py": (
            "DEFAULT_SETTINGS = {'alert_channel_watching': True}\n"
            "def main():\n"
            "    return 1\n"
        )
    }
    after = {
        "app/core/docker-entrypoint.py": (
            "DEFAULT_SETTINGS = {'alert_channel_watching': False}\n"
            "def main():\n"
            "    return 2\n"
        )
    }

    result = module.classify_changes(before, after)

    assert result.delivery_mode == "image_required"
    assert result.image_required is True


@pytest.mark.parametrize(
    ("before_version", "before_protocol", "after_version", "after_protocol"),
    (
        ("0.9.17", 2, "0.9.18", 3),
        ("0.9.18", 3, "0.9.19", 4),
    ),
)
def test_release_impact_classifier_requires_image_for_launcher_protocol_changes(
    before_version, before_protocol, after_version, after_protocol
):
    module = _load_script(
        "classify_release_impact_launcher_protocol",
        "scripts/release/classify-release-impact.py",
    )
    base = "FROM example.invalid/runtime@sha256:" + "a" * 64 + "\n"
    before = {
        "deploy/docker/Dockerfile": (
            base
            + f"ARG VERSION={before_version}\n"
            + f'ENV CHANNELWATCH_LAUNCHER_PROTOCOL="{before_protocol}"\n'
        )
    }
    after = {
        "deploy/docker/Dockerfile": (
            base
            + f"ARG VERSION={after_version}\n"
            + f'ENV CHANNELWATCH_LAUNCHER_PROTOCOL="{after_protocol}"\n'
        )
    }

    result = module.classify_changes(before, after)

    assert result.delivery_mode == "image_required"
    assert result.image_required is True
    assert result.triggering_paths == ("deploy/docker/Dockerfile",)


def test_release_impact_classifier_requires_image_for_runtime_launcher_changes():
    module = _load_script(
        "classify_release_impact_runtime_launcher",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(["app/core/runtime_launcher.py"])

    assert result.delivery_mode == "image_required"
    assert result.image_required is True
    assert result.triggering_paths == ("app/core/runtime_launcher.py",)


def test_release_impact_classifier_accepts_exact_minimum_image_launcher_replay(
    tmp_path,
):
    module = _load_script(
        "classify_release_impact_verified_minimum_image",
        "scripts/release/classify-release-impact.py",
    )
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"exact bundle")
    phase = {"index_matches": True, "referenced_assets_verified": 14}
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": 1,
                "status": "passed",
                "source_image_version": "1.0.0",
                "image_index_digest": "sha256:" + "a" * 64,
                "platform_manifest_digest": "sha256:" + "b" * 64,
                "pulled_image_id": "sha256:" + "c" * 64,
                "platform": "linux/amd64",
                "target_application_version": "1.0.8",
                "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "dynamic_activation": phase,
                "ui_restart": phase,
                "rollback_reapply": phase,
                "container_restart": phase,
            }
        ),
        encoding="utf-8",
    )

    result = module.apply_verified_minimum_image_app_update(
        module.classify_paths(["app/core/runtime_launcher.py"]),
        config={
            "version": "1.0.8",
            "minimum_image_version": "1.0.0",
            "minimum_image_sha256": "sha256:" + "a" * 64,
            "delivery_mode": "app_update",
        },
        evidence_path=evidence,
        bundle_path=bundle,
    )

    assert result.delivery_mode == "app_update"
    assert result.image_required is False
    assert result.triggering_paths == ()


def test_release_impact_classifier_accepts_minimum_image_replay_for_retained_floor(
    tmp_path,
):
    module = _load_script(
        "classify_release_impact_retained_minimum_image",
        "scripts/release/classify-release-impact.py",
    )
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"exact retained-floor bundle")
    phase = {"index_matches": True, "referenced_assets_verified": 12}
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": 1,
                "status": "passed",
                "source_image_version": config["minimum_image_version"],
                "image_index_digest": config["minimum_image_sha256"],
                "platform_manifest_digest": "sha256:" + "b" * 64,
                "pulled_image_id": "sha256:" + "c" * 64,
                "platform": "linux/amd64",
                "target_application_version": config["version"],
                "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "dynamic_activation": phase,
                "ui_restart": phase,
                "rollback_reapply": phase,
                "container_restart": phase,
            }
        ),
        encoding="utf-8",
    )

    result = module.apply_verified_minimum_image_app_update(
        module.classify_paths(["app/core/runtime_launcher.py"]),
        config=config,
        evidence_path=evidence,
        bundle_path=bundle,
    )

    assert result.delivery_mode == "app_update"
    assert result.image_required is False
    assert result.triggering_paths == ()


def test_release_impact_classifier_rejects_minimum_image_replay_for_image_milestone(
    tmp_path,
):
    module = _load_script(
        "classify_release_impact_image_milestone",
        "scripts/release/classify-release-impact.py",
    )
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )
    config.update(
        {
            "version": "1.7.0",
            "image_required": True,
            "delivery_mode": "image_required",
            "minimum_image_version": "1.7.0",
            "recommended_image_version": "1.7.0",
            "runtime_generation_required": False,
        }
    )
    config.pop("runtime_bootstrap_sha256")
    config.pop("minimum_image_sha256")
    policy = module.validate_release_config(config)
    assert policy.image_milestone is True
    assert policy.retained_capability_floor is False
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"image milestone bundle")
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")

    with pytest.raises(module.ReleaseImpactMismatch, match="validated in-app release"):
        module.apply_verified_minimum_image_app_update(
            module.classify_paths(["app/core/runtime_launcher.py"]),
            config=config,
            evidence_path=evidence,
            bundle_path=bundle,
        )


def test_release_impact_classifier_rejects_patch_zero_without_validated_floor(
    tmp_path,
):
    module = _load_script(
        "classify_release_impact_invalid_retained_floor",
        "scripts/release/classify-release-impact.py",
    )
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )
    config["version"] = "1.7.0"
    config["recommended_image_version"] = "1.7.0"
    config["compatible_launcher_protocols"] = [3]
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"invalid retained-floor bundle")
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")

    with pytest.raises(module.ReleaseImpactMismatch, match="validated in-app release"):
        module.apply_verified_minimum_image_app_update(
            module.classify_paths(["app/core/runtime_launcher.py"]),
            config=config,
            evidence_path=evidence,
            bundle_path=bundle,
        )


def test_release_impact_classifier_rejects_minimum_image_evidence_for_other_bundle(
    tmp_path,
):
    module = _load_script(
        "classify_release_impact_wrong_minimum_image_bundle",
        "scripts/release/classify-release-impact.py",
    )
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"different bundle")
    phase = {"index_matches": True, "referenced_assets_verified": 14}
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": 1,
                "status": "passed",
                "source_image_version": "1.0.0",
                "image_index_digest": "sha256:" + "a" * 64,
                "platform_manifest_digest": "sha256:" + "b" * 64,
                "pulled_image_id": "sha256:" + "c" * 64,
                "platform": "linux/amd64",
                "target_application_version": "1.0.8",
                "bundle_sha256": "0" * 64,
                "dynamic_activation": phase,
                "ui_restart": phase,
                "rollback_reapply": phase,
                "container_restart": phase,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.ReleaseImpactMismatch, match="evidence is invalid"):
        module.apply_verified_minimum_image_app_update(
            module.classify_paths(["app/core/runtime_launcher.py"]),
            config={
                "version": "1.0.8",
                "minimum_image_version": "1.0.0",
                "minimum_image_sha256": "sha256:" + "a" * 64,
                "delivery_mode": "app_update",
            },
            evidence_path=evidence,
            bundle_path=bundle,
        )


def test_release_impact_classifier_requires_image_for_runtime_surfaces():
    module = _load_script(
        "classify_release_impact_runtime",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(
        [
            "app/bin/channelwatch",
            "app/core/helpers/atomic_io.py",
            "app/core/helpers/migration.py",
            "deploy/requirements/runtime.txt",
            "deploy/docker/Dockerfile",
            "deploy/docker/Dockerfile.dockerignore",
            "app/ui/pnpm-lock.yaml",
            "app/core/update_center.py",
            "app/ui/components/report-problem-dialog.tsx",
        ]
    )

    assert result.image_required is True
    assert result.triggering_paths == (
        "app/bin/channelwatch",
        "deploy/docker/Dockerfile.dockerignore",
        "deploy/requirements/runtime.txt",
    )
    assert result.image_refresh_recommended is True
    assert result.refresh_paths == (
        "app/core/helpers/atomic_io.py",
        "app/core/helpers/migration.py",
        "app/ui/pnpm-lock.yaml",
    )


def test_release_impact_classifier_keeps_update_center_app_deliverable():
    module = _load_script(
        "classify_release_impact_update_center_app",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(["app/core/update_center.py"])

    assert result.delivery_mode == "app_update"
    assert result.image_required is False
    assert result.image_refresh_recommended is False


def test_release_impact_classifier_keeps_update_policy_app_deliverable():
    module = _load_script(
        "classify_release_impact_update_policy_app",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(["app/core/update_policy.py"])

    assert result.delivery_mode == "app_update"
    assert result.image_required is False
    assert result.image_refresh_recommended is False


def test_release_impact_classifier_requires_image_for_supervisor_template():
    module = _load_script(
        "classify_release_impact_supervisor_template",
        "scripts/release/classify-release-impact.py",
    )
    supervisor_template = (
        ROOT / "deploy/config/supervisor/supervisord.conf.template"
    )
    assert supervisor_template.is_file()
    supervisor_path = supervisor_template.relative_to(ROOT).as_posix()

    result = module.classify_paths([supervisor_path])

    assert result.image_required is True
    assert result.triggering_paths == (supervisor_path,)


def test_release_impact_classifier_requires_historical_proof_for_entrypoint_override():
    module = _load_script(
        "classify_release_impact_entrypoint_compatibility",
        "scripts/release/classify-release-impact.py",
    )
    result = module.classify_paths(["app/core/docker-entrypoint.py"])

    config = {
        "version": "0.9.18",
        "runtime_compatibility_evidence": {
            module.ENTRYPOINT_COMPATIBILITY_PATH: (
                module.expected_entrypoint_compatibility_declaration()
            )
        },
    }
    with pytest.raises(module.ReleaseImpactMismatch, match="compatibility verification"):
        module.apply_verified_runtime_compatibility(
            result,
            config=config,
            manifest_path=None,
            bundle_path=None,
            audited_change_paths={module.ENTRYPOINT_COMPATIBILITY_PATH},
        )


def _bridge_result(module, tag: str) -> dict[str, object]:
    result = {
        "tag": tag,
        "manifest_version": "0.9.18",
        "bundle_version": "0.9.18",
        "check_status": "available",
        "apply_status": "restarting",
        "applied_active_version": "0.9.18",
        "active_version": "0.9.18",
        "journal_replayed": tag in {"v0.9.16", "v0.9.17"},
        "source_acceptance": "verified",
    }
    return result


def _write_bridge_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    manifest = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.zip"
    manifest.write_text(
        json.dumps(
            {
                "payload": {
                    "version": "0.9.18",
                    "image_required": False,
                    "delivery_mode": "app_update_with_image_refresh",
                    "minimum_image_version": "0.9.11",
                    "updater_protocol": 2,
                    "recommended_image_version": "0.9.18",
                }
            }
        ),
        encoding="utf-8",
    )
    bundle.write_bytes(b"test bundle")
    return manifest, bundle


def _write_historical_image_evidence(tmp_path: Path, bundle: Path) -> Path:
    locks = json.loads(
        (ROOT / "scripts/release/historical-image-lock.json").read_text(
            encoding="utf-8"
        )
    )
    bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    results = []
    by_version = {lock["version"]: lock for lock in locks["images"]}
    for lock in locks["images"]:
        scenario = (
            "image_refresh_recovery"
            if lock["support"] == "image_pull_only"
            else "activation_success"
        )
        base = {
            "scenario": scenario,
            "scenario_key": f"{scenario}:{lock['version']}",
            "source_version": lock["version"],
            "source_sha": lock["source_sha"],
            "image_index_digest": lock["index_digest"],
            "amd64_digest": lock["amd64_digest"],
            "launcher_protocol": lock["launcher_protocol"],
            "bundle_sha256": bundle_sha,
            "check_status": "available",
            "apply_status": "restarting",
        }
        if lock["support"] == "image_pull_only":
            base.update(
                {
                    "image_refresh_required": True,
                    "recovery_job_status": "success",
                    "recovery_quorum_verified": True,
                    "recovery_image_runtime_verified": True,
                    "managed_key_verified": True,
                    "supervisor_stable": True,
                    "stale_control_file_count": 0,
                    "result": "passed_with_documented_image_only_limitation",
                }
            )
            if lock["version"] == "0.9.9":
                base.update(
                    {
                        "immutable_false_success_observed": True,
                        "legacy_core_launcher_failure_observed": True,
                        "legacy_ui_image_runtime_verified": True,
                        "recovery_image_cleared_false_success": True,
                    }
                )
            else:
                base.update(
                    {
                        "portal_api_verified": True,
                        "immutable_entrypoint_failure_observed": True,
                        "legacy_restart_loop_observed": True,
                        "legacy_restart_count_at_least": 2,
                        "legacy_staged_identity_preserved": True,
                        "recovery_image_cleared_failed_activation": True,
                        "restart_count": 2,
                    }
                )
        else:
            base.update(
                {
                    "final_job_status": "success",
                    "core_bundle": True,
                    "ui_bundle": True,
                    "portal_api_verified": True,
                    "restart_count_delta": 1,
                    "supervisor_stable": True,
                    "rollback_target_verified": True,
                    "active_identity_verified": True,
                    "restart_count": 1,
                    "managed_key_verified": True,
                    "stale_control_file_count": 0,
                    "result": "passed",
                }
            )
        results.append(base)
    for version, component in (("0.9.15", "ui"), ("0.9.17", "core")):
        results.append(
            {
                "scenario": "activation_failure",
                "scenario_key": f"activation_failure:{version}:{component}",
                "source_version": version,
                "source_sha": by_version[version]["source_sha"],
                "image_index_digest": by_version[version]["index_digest"],
                "amd64_digest": by_version[version]["amd64_digest"],
                "launcher_protocol": by_version[version]["launcher_protocol"],
                "failed_component": component,
                "bundle_sha256": bundle_sha,
                "portal_api_verified": True,
                "restart_count_delta": 2,
                "final_job_status": "failed",
                "rollback_applied": True,
                "failed_identity_quarantined": True,
                "image_runtime_restored": True,
                "fault_applied": True,
                "rollback_target_verified": True,
                "scheduler_attempt_verified": True,
                "supervisor_stable": True,
                "stale_control_file_count": 0,
                "result": "passed",
            }
        )
    for version in ("0.9.15", "0.9.17"):
        for case in ("manifest", "bundle"):
            results.append(
                {
                    "scenario": "tamper_rejection",
                    "scenario_key": f"tamper_rejection:{version}:{case}",
                    "source_version": version,
                    "source_sha": by_version[version]["source_sha"],
                    "image_index_digest": by_version[version]["index_digest"],
                    "amd64_digest": by_version[version]["amd64_digest"],
                    "launcher_protocol": by_version[version]["launcher_protocol"],
                    "bundle_sha256": bundle_sha,
                    "tamper_case": case,
                    "tamper_applied": True,
                    "fetch_transport_verified": True,
                    "rejected_before_selection": True,
                    "active_unchanged": True,
                    "candidate_release_absent": True,
                    "supervisor_stable": True,
                    "stale_control_file_count": 0,
                    "restart_count_delta": 0,
                    "result": "passed",
                }
            )
    evidence = tmp_path / "historical-images.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": 2,
                "target_version": "0.9.18",
                "platform": "linux/amd64",
                "results": results,
                "passed": True,
            }
        ),
        encoding="utf-8",
    )
    return evidence


def test_release_impact_classifier_replays_exact_historical_evidence(
    monkeypatch, tmp_path
):
    module = _load_script(
        "classify_release_impact_entrypoint_replay",
        "scripts/release/classify-release-impact.py",
    )
    manifest, bundle = _write_bridge_artifacts(tmp_path)
    image_evidence = _write_historical_image_evidence(tmp_path, bundle)
    calls = []

    def verify_tag(tag, **kwargs):
        calls.append((tag, kwargs))
        return _bridge_result(module, tag)

    monkeypatch.setattr(
        module,
        "_load_legacy_bridge_verifier",
        lambda: SimpleNamespace(verify_tag=verify_tag),
    )
    config = {
        "version": "0.9.18",
        "runtime_compatibility_evidence": {
            module.ENTRYPOINT_COMPATIBILITY_PATH: (
                module.expected_entrypoint_compatibility_declaration()
            )
        },
    }
    result = module.classify_paths(
        ["app/core/docker-entrypoint.py", "deploy/requirements/runtime.txt"]
    )
    overridden = module.apply_verified_runtime_compatibility(
        result,
        config=config,
        manifest_path=manifest,
        bundle_path=bundle,
        historical_image_evidence=image_evidence,
        audited_change_paths={module.ENTRYPOINT_COMPATIBILITY_PATH},
    )

    assert [tag for tag, _kwargs in calls] == list(module.LEGACY_BRIDGE_TAGS)
    assert all(call[1]["manifest_path"] == manifest for call in calls)
    assert all(call[1]["bundle_path"] == bundle for call in calls)
    assert overridden.delivery_mode == "image_required"
    assert overridden.image_required is True
    assert overridden.triggering_paths == ("deploy/requirements/runtime.txt",)
    assert overridden.refresh_paths == ("app/core/docker-entrypoint.py",)


def _mutate_historical_image_evidence(path: Path, mutate) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_historical_image_evidence_requires_schema_two(tmp_path):
    module = _load_script(
        "classify_release_impact_historical_schema",
        "scripts/release/classify-release-impact.py",
    )
    _manifest, bundle = _write_bridge_artifacts(tmp_path)
    evidence = _write_historical_image_evidence(tmp_path, bundle)
    _mutate_historical_image_evidence(
        evidence, lambda document: document.update({"schema": 1})
    )

    with pytest.raises(module.ReleaseImpactMismatch, match="header is invalid"):
        module.verify_historical_image_evidence(evidence, bundle_path=bundle)


def test_historical_image_evidence_uses_exact_generator_scenario_keys(tmp_path):
    module = _load_script(
        "classify_release_impact_historical_scenarios",
        "scripts/release/classify-release-impact.py",
    )
    _manifest, bundle = _write_bridge_artifacts(tmp_path)
    evidence = _write_historical_image_evidence(tmp_path, bundle)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    keys = [item["scenario_key"] for item in document["results"]]

    assert len(keys) == 15
    assert len(set(keys)) == 15
    assert set(keys) == module._expected_historical_image_evidence_keys()
    assert "activation_success:0.9.10" not in keys
    assert "image_refresh_recovery:0.9.9" in keys
    assert "image_refresh_recovery:0.9.10" in keys


def test_historical_image_evidence_rejects_mismatched_generator_key(tmp_path):
    module = _load_script(
        "classify_release_impact_historical_scenario_key",
        "scripts/release/classify-release-impact.py",
    )
    _manifest, bundle = _write_bridge_artifacts(tmp_path)
    evidence = _write_historical_image_evidence(tmp_path, bundle)

    def corrupt_key(document):
        row = next(
            item
            for item in document["results"]
            if item["scenario_key"] == "image_refresh_recovery:0.9.10"
        )
        row["scenario_key"] = "activation_success:0.9.10"

    _mutate_historical_image_evidence(evidence, corrupt_key)

    with pytest.raises(module.ReleaseImpactMismatch, match="scenario key is invalid"):
        module.verify_historical_image_evidence(evidence, bundle_path=bundle)


def test_historical_image_evidence_requires_v0910_image_pull_lock(
    monkeypatch, tmp_path
):
    module = _load_script(
        "classify_release_impact_historical_v0910_lock",
        "scripts/release/classify-release-impact.py",
    )
    _manifest, bundle = _write_bridge_artifacts(tmp_path)
    evidence = _write_historical_image_evidence(tmp_path, bundle)
    lock = json.loads(
        (ROOT / "scripts/release/historical-image-lock.json").read_text(
            encoding="utf-8"
        )
    )
    next(
        item for item in lock["images"] if item["version"] == "0.9.10"
    )["support"] = "app_update"
    fake_root = tmp_path / "fake-root"
    fake_lock = fake_root / "scripts/release/historical-image-lock.json"
    fake_lock.parent.mkdir(parents=True)
    fake_lock.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", fake_root)

    with pytest.raises(
        module.ReleaseImpactMismatch, match="support classifications are invalid"
    ):
        module.verify_historical_image_evidence(evidence, bundle_path=bundle)


def test_historical_image_evidence_rejects_duplicate_scenario_variant(tmp_path):
    module = _load_script(
        "classify_release_impact_historical_duplicate",
        "scripts/release/classify-release-impact.py",
    )
    _manifest, bundle = _write_bridge_artifacts(tmp_path)
    evidence = _write_historical_image_evidence(tmp_path, bundle)

    def duplicate_success(document):
        row = next(
            item
            for item in document["results"]
            if item["scenario_key"] == "activation_success:0.9.15"
        )
        document["results"].append(dict(row))

    _mutate_historical_image_evidence(evidence, duplicate_success)

    with pytest.raises(module.ReleaseImpactMismatch, match="duplicate scenario"):
        module.verify_historical_image_evidence(evidence, bundle_path=bundle)


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_historical_image_evidence_requires_exact_scenario_matrix(tmp_path, mutation):
    module = _load_script(
        f"classify_release_impact_historical_matrix_{mutation}",
        "scripts/release/classify-release-impact.py",
    )
    _manifest, bundle = _write_bridge_artifacts(tmp_path)
    evidence = _write_historical_image_evidence(tmp_path, bundle)

    def mutate(document):
        if mutation == "missing":
            document["results"] = [
                item
                for item in document["results"]
                if not (
                    item.get("scenario") == "activation_failure"
                    and item.get("source_version") == "0.9.15"
                    and item.get("failed_component") == "ui"
                )
            ]
        else:
            document["results"].append(
                {
                    "scenario": "tamper_rejection",
                    "scenario_key": "tamper_rejection:0.9.15:signature",
                    "source_version": "0.9.15",
                    "launcher_protocol": 1,
                    "tamper_case": "signature",
                    "rejected_before_selection": True,
                    "active_unchanged": True,
                    "restart_count_delta": 0,
                    "result": "passed",
                }
            )

    _mutate_historical_image_evidence(evidence, mutate)

    with pytest.raises(module.ReleaseImpactMismatch, match="scenario matrix is invalid"):
        module.verify_historical_image_evidence(evidence, bundle_path=bundle)


@pytest.mark.parametrize(
    ("scenario", "source_version", "variant_field", "variant", "field", "value", "message"),
    (
        (
            "activation_success",
            "0.9.11",
            None,
            None,
            "portal_api_verified",
            1,
            "v0.9.11",
        ),
        (
            "activation_failure",
            "0.9.15",
            "failed_component",
            "ui",
            "failed_identity_quarantined",
            False,
            "activation-failure",
        ),
        (
            "tamper_rejection",
            "0.9.17",
            "tamper_case",
            "bundle",
            "restart_count_delta",
            1,
            "tamper-rejection",
        ),
        (
            "image_refresh_recovery",
            "0.9.9",
            None,
            None,
            "recovery_quorum_verified",
            False,
            "image-refresh recovery",
        ),
        (
            "image_refresh_recovery",
            "0.9.10",
            None,
            None,
            "recovery_image_cleared_failed_activation",
            False,
            "image-refresh recovery",
        ),
    ),
)
def test_historical_image_evidence_validates_scenario_semantics(
    tmp_path,
    scenario,
    source_version,
    variant_field,
    variant,
    field,
    value,
    message,
):
    module = _load_script(
        f"classify_release_impact_historical_semantics_{source_version}_{field}",
        "scripts/release/classify-release-impact.py",
    )
    _manifest, bundle = _write_bridge_artifacts(tmp_path)
    evidence = _write_historical_image_evidence(tmp_path, bundle)

    def mutate(document):
        for item in document["results"]:
            if item.get("source_version") != source_version:
                continue
            if item.get("scenario") != scenario:
                continue
            if variant_field is not None and item.get(variant_field) != variant:
                continue
            item[field] = value
            return
        raise AssertionError("test scenario row was not found")

    _mutate_historical_image_evidence(evidence, mutate)

    with pytest.raises(module.ReleaseImpactMismatch, match=message):
        module.verify_historical_image_evidence(evidence, bundle_path=bundle)


def test_release_impact_classifier_declares_immutable_image_pull_exceptions():
    module = _load_script(
        "classify_release_impact_v099_exception",
        "scripts/release/classify-release-impact.py",
    )

    declaration = module.expected_entrypoint_compatibility_declaration()

    assert {"v0.9.9", "v0.9.10"}.isdisjoint(declaration["source_tags"])
    assert {"v0.9.9", "v0.9.10"}.isdisjoint(
        declaration["expected_activations"]
    )
    assert declaration["image_pull_only_sources"] == {
        **{
            tag: {
                "required_image_version": "0.9.18",
                "preserve_config": True,
                "in_app_update_supported": False,
                "pre_pull_false_success_possible": True,
                "recovery_image_repairs_marker": True,
                "reason": "published_image_cannot_activate_bridge_bundle",
            }
            for tag in ("v0.9.9", "v0.9.10")
        }
    }


def test_release_impact_classifier_rejects_incomplete_historical_result(
    monkeypatch, tmp_path
):
    module = _load_script(
        "classify_release_impact_incomplete_replay",
        "scripts/release/classify-release-impact.py",
    )
    manifest, bundle = _write_bridge_artifacts(tmp_path)
    image_evidence = _write_historical_image_evidence(tmp_path, bundle)

    def verify_tag(tag, **_kwargs):
        result = _bridge_result(module, tag)
        if tag == "v0.9.16":
            result["source_acceptance"] = "unverified"
        return result

    monkeypatch.setattr(
        module,
        "_load_legacy_bridge_verifier",
        lambda: SimpleNamespace(verify_tag=verify_tag),
    )
    config = {
        "version": "0.9.18",
        "runtime_compatibility_evidence": {
            module.ENTRYPOINT_COMPATIBILITY_PATH: (
                module.expected_entrypoint_compatibility_declaration()
            )
        },
    }
    with pytest.raises(module.ReleaseImpactMismatch, match="v0.9.16"):
        module.apply_verified_runtime_compatibility(
            module.classify_paths(["app/core/docker-entrypoint.py"]),
            config=config,
            manifest_path=manifest,
            bundle_path=bundle,
            historical_image_evidence=image_evidence,
            audited_change_paths={module.ENTRYPOINT_COMPATIBILITY_PATH},
        )


def test_release_impact_classifier_rejects_modified_evidence_declaration():
    module = _load_script(
        "classify_release_impact_modified_declaration",
        "scripts/release/classify-release-impact.py",
    )
    declaration = module.expected_entrypoint_compatibility_declaration()
    declaration["source_tags"] = list(module.LEGACY_BRIDGE_TAGS[:-1])
    with pytest.raises(module.ReleaseImpactMismatch, match="audited v0.9.18"):
        module.apply_verified_runtime_compatibility(
            module.classify_paths(["app/core/docker-entrypoint.py"]),
            config={
                "version": "0.9.18",
                "runtime_compatibility_evidence": {
                    module.ENTRYPOINT_COMPATIBILITY_PATH: declaration
                },
            },
            manifest_path=None,
            bundle_path=None,
        )


def test_release_impact_classifier_downgrades_v0918_runtime_only_after_replay(
    monkeypatch, tmp_path
):
    module = _load_script(
        "classify_release_impact_v0918_runtime",
        "scripts/release/classify-release-impact.py",
    )
    manifest, bundle = _write_bridge_artifacts(tmp_path)
    image_evidence = _write_historical_image_evidence(tmp_path, bundle)
    monkeypatch.setattr(
        module,
        "_load_legacy_bridge_verifier",
        lambda: SimpleNamespace(
            verify_tag=lambda tag, **_kwargs: _bridge_result(module, tag)
        ),
    )
    docker_base = "FROM example.invalid/runtime@sha256:" + "a" * 64 + "\n"
    before = {
        "app/core/docker-entrypoint.py": "old entrypoint\n",
        "app/core/runtime_launcher.py": "old launcher\n",
        "deploy/docker/Dockerfile": (
            docker_base
            + "ARG VERSION=0.9.17\n"
            + 'ENV CHANNELWATCH_LAUNCHER_PROTOCOL="2"\n'
        ),
    }
    after = {
        "app/core/docker-entrypoint.py": "v0.9.18 entrypoint\n",
        "app/core/runtime_launcher.py": "v0.9.18 launcher\n",
        "deploy/docker/Dockerfile": (
            docker_base
            + "ARG VERSION=0.9.18\n"
            + 'ENV CHANNELWATCH_LAUNCHER_PROTOCOL="3"\n'
        ),
    }
    overridden = module.apply_verified_runtime_compatibility(
        module.classify_changes(before, after),
        config={
            "version": "0.9.18",
            "runtime_compatibility_evidence": {
                path: module.expected_entrypoint_compatibility_declaration()
                for path in module.AUDITED_RUNTIME_COMPATIBILITY_PATHS
            },
        },
        manifest_path=manifest,
        bundle_path=bundle,
        historical_image_evidence=image_evidence,
        audited_change_paths=module.audited_runtime_change_paths(before, after),
    )
    assert overridden.delivery_mode == "app_update_with_image_refresh"
    assert overridden.image_required is False
    assert overridden.triggering_paths == ()
    assert overridden.refresh_paths == (
        "app/core/docker-entrypoint.py",
        "app/core/runtime_launcher.py",
        "deploy/docker/Dockerfile",
    )


def test_release_impact_classifier_audits_only_protocol_two_to_three():
    module = _load_script(
        "classify_release_impact_exact_launcher_transition",
        "scripts/release/classify-release-impact.py",
    )
    base = "FROM example.invalid/runtime@sha256:" + "a" * 64 + "\n"

    def dockerfile(version, protocol):
        return (
            base
            + f"ARG VERSION={version}\n"
            + f'ENV CHANNELWATCH_LAUNCHER_PROTOCOL="{protocol}"\n'
        )

    exact = module.audited_runtime_change_paths(
        {"deploy/docker/Dockerfile": dockerfile("0.9.17", 2)},
        {"deploy/docker/Dockerfile": dockerfile("0.9.18", 3)},
    )
    future = module.audited_runtime_change_paths(
        {"deploy/docker/Dockerfile": dockerfile("0.9.18", 3)},
        {"deploy/docker/Dockerfile": dockerfile("0.9.19", 4)},
    )

    assert exact == {"deploy/docker/Dockerfile"}
    assert future == set()


def test_release_impact_classifier_ignores_scanner_comment_refinement():
    module = _load_script(
        "classify_release_impact_scanner_comments",
        "scripts/release/classify-release-impact.py",
    )
    base = "FROM example.invalid/runtime@sha256:" + "a" * 64 + "\n"
    before = {
        "deploy/docker/Dockerfile": (
            base
            + "# nosemgrep\n"
            + "# trivy:ignore:DS-0002\n"
            + "# hadolint ignore=DL3002,DL3066\n"
            + "ARG VERSION=0.9.17\n"
        )
    }
    after = {
        "deploy/docker/Dockerfile": (
            base
            + "# trivy:ignore:DS-0002\n"
            + "# hadolint ignore=DL3002,DL3066\n"
            + "# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root\n"
            + "ARG VERSION=0.9.18\n"
            + 'ENV CHANNELWATCH_LAUNCHER_PROTOCOL="3"\n'
        )
    }

    assert module.audited_runtime_change_paths(before, after) == {
        "deploy/docker/Dockerfile"
    }


def test_release_impact_classifier_rejects_unsupported_compatibility_override():
    module = _load_script(
        "classify_release_impact_unsupported_compatibility",
        "scripts/release/classify-release-impact.py",
    )
    with pytest.raises(module.ReleaseImpactMismatch, match="non-audited"):
        module.apply_verified_runtime_compatibility(
            module.classify_paths(["deploy/requirements/runtime.txt"]),
            config={
                "version": "0.9.18",
                "runtime_compatibility_evidence": {
                    "deploy/requirements/runtime.txt": {
                        "kind": "legacy_update_bridge_v1"
                    }
                },
            },
            manifest_path=None,
            bundle_path=None,
        )


def test_release_impact_classifier_rejects_declared_mismatch():
    module = _load_script(
        "classify_release_impact_mismatch",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(["app/ui/lib/api.ts"])

    try:
        module.verify_declared_impact(result, declared_image_required=True)
    except module.ReleaseImpactMismatch as exc:
        assert "declares image_required=true" in str(exc)
        assert "classifier requires false" in str(exc)
    else:
        raise AssertionError("release impact mismatch was accepted")


def test_release_impact_classifier_ignores_version_only_package_and_helm_changes():
    module = _load_script(
        "classify_release_impact_version_only",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/ui/package.json": '{"version":"0.9.14","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/Chart.yaml": "version: 0.9.14\nappVersion: 0.9.14\nname: channelwatch\n",
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.14\n  pullPolicy: IfNotPresent\n",
    }
    after = {
        "app/ui/package.json": '{"version":"0.9.15","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/Chart.yaml": "version: 0.9.15\nappVersion: 0.9.15\nname: channelwatch\n",
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.15\n  pullPolicy: IfNotPresent\n",
    }

    result = module.classify_changes(before, after)

    assert result.image_required is False


def test_release_impact_classifier_detects_dependency_and_helm_runtime_changes():
    module = _load_script(
        "classify_release_impact_structured_runtime",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/ui/package.json": '{"version":"0.9.14","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.14\n  pullPolicy: IfNotPresent\n",
    }
    after = {
        "app/ui/package.json": '{"version":"0.9.15","dependencies":{"next":"16.4.0"}}',
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.15\n  pullPolicy: Always\n",
    }

    result = module.classify_changes(before, after)

    assert result.image_required is False
    assert result.delivery_mode == "app_update_with_image_refresh"
    assert result.refresh_paths == (
        "app/ui/package.json",
        "deploy/helm/channelwatch/values.yaml",
    )


def test_release_impact_classifier_ignores_only_docker_version_default():
    module = _load_script("classify_docker_version", "scripts/release/classify-release-impact.py")
    before = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.14\nLABEL version=$VERSION\n"}
    after = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.15\nLABEL version=$VERSION\n"}
    assert module.classify_changes(before, after).image_required is False


def test_release_impact_classifier_detects_other_dockerfile_changes():
    module = _load_script("classify_docker_runtime", "scripts/release/classify-release-impact.py")
    before = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.14\n"}
    after = {"deploy/docker/Dockerfile": "FROM busybox\nARG VERSION=0.9.15\n"}
    result = module.classify_changes(before, after)
    assert result.image_required is True
    assert result.triggering_paths == ("deploy/docker/Dockerfile",)


def test_release_impact_classifier_cli_forwards_ephemeral_public_key(
    tmp_path: Path, monkeypatch, capsys
):
    module = _load_script(
        "classify_release_ephemeral_cli",
        "scripts/release/classify-release-impact.py",
    )
    encoded = base64.b64encode(b"k" * 32).decode()
    captured = {}
    monkeypatch.setattr(module, "changed_paths", lambda _base, _target: [])

    def capture(result, **kwargs):
        captured.update(kwargs)
        return result

    monkeypatch.setattr(module, "apply_verified_runtime_compatibility", capture)
    config = tmp_path / "release-config.json"
    config.write_text(
        json.dumps(
            {
                "version": "0.9.19",
                "image_required": False,
                "delivery_mode": "app_update",
            }
        ),
        encoding="utf-8",
    )

    assert (
        module.main(
            [
                "--base-ref",
                "base",
                "--target-ref",
                "target",
                "--config",
                str(config),
                "--public-key",
                f"ephemeral={encoded}",
            ]
        )
        == 0
    )
    assert captured["public_keys"] == {"ephemeral": encoded}
    assert '"delivery_mode": "app_update"' in capsys.readouterr().out


@pytest.mark.parametrize(
    "values",
    (
        ["missing-separator"],
        ["=Zm9v"],
        ["key=not-base64!"],
        [f"key={base64.b64encode(b'short').decode()}"],
        [
            f"key={base64.b64encode(b'a' * 32).decode()}",
            f"key={base64.b64encode(b'b' * 32).decode()}",
        ],
    ),
)
def test_release_impact_classifier_rejects_invalid_public_key_overrides(values):
    module = _load_script(
        "classify_release_invalid_key",
        "scripts/release/classify-release-impact.py",
    )
    with pytest.raises(ValueError, match="public-key"):
        module.parse_public_key_overrides(values)


def test_release_impact_classifier_uses_official_keys_when_override_is_omitted():
    module = _load_script(
        "classify_release_official_keys",
        "scripts/release/classify-release-impact.py",
    )
    assert module.parse_public_key_overrides(None) is None


def test_release_workflow_gates_declared_impact_and_live_manifest():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    candidate = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "classify-release-impact.py" in workflow
    assert 'version="${RELEASE_TAG#v}"' in workflow
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    assert "fetch-depth: 0" in release_job
    assert release_job.index("verify-update-assets.py") < release_job.index(
        "verify-legacy-update-bridge.py"
    )
    assert release_job.index("verify-legacy-update-bridge.py") < release_job.index(
        "classify-release-impact.py"
    )
    assert release_job.index("classify-release-impact.py") < release_job.index(
        "Create or update draft GitHub Release"
    )
    assert (
        '--compatibility-manifest "dist/update/channelwatch-update-${TAG}.json"'
        in release_job
    )
    assert (
        '--compatibility-bundle "dist/update/channelwatch-app-${TAG}.zip"'
        in release_job
    )
    assert candidate.index("verify-update-assets.py") < candidate.index(
        "verify-legacy-update-bridge.py"
    )
    assert candidate.index("verify-legacy-update-bridge.py") < candidate.index(
        "classify-release-impact.py"
    )
    assert candidate.index("classify-release-impact.py") < candidate.index(
        "Encrypt qualified candidate for the final sealer"
    )
    assert "--historical-compatibility-verified" not in workflow + candidate
    assert workflow.count("--minimum-image-app-update-evidence") == 2
    assert workflow.count("--minimum-image-app-update-bundle") == 2
    assert "Verify live stable update manifest" in workflow
    assert "verify-live-update-manifest.py" in workflow
    assert "- sync-site" in workflow
    live_job = workflow.split("  verify-live-update-manifest:", 1)[1]
    assert '--attempts "90"' in live_job
    assert '--interval "10"' in live_job


def test_candidate_artifact_sealing_round_trip_and_tamper_rejection(
    tmp_path: Path, monkeypatch
):
    module = _load_script(
        "seal_candidate_artifact",
        "scripts/release/seal-candidate-artifact.py",
    )
    source = tmp_path / "candidate.tar"
    sealed = tmp_path / "candidate.sealed"
    opened = tmp_path / "opened.tar"
    source.write_bytes(os.urandom((2 * module.CHUNK_BYTES) + 17))
    monkeypatch.setenv(module.KEY_ENV, "test-signing-material-not-for-production")

    module.seal(source, sealed)
    module.open_sealed(sealed, opened)

    assert opened.read_bytes() == source.read_bytes()
    assert sealed.stat().st_mode & 0o777 == 0o600
    assert opened.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv(module.KEY_ENV, "a-different-protected-signing-secret")
    wrong_key = tmp_path / "wrong-key.tar"
    with pytest.raises(Exception):
        module.open_sealed(sealed, wrong_key)
    assert not wrong_key.exists()
    monkeypatch.setenv(module.KEY_ENV, "test-signing-material-not-for-production")
    tampered = bytearray(sealed.read_bytes())
    tampered[-module.TAG_BYTES - 1] ^= 1
    sealed.write_bytes(tampered)
    rejected = tmp_path / "rejected.tar"
    with pytest.raises(Exception):
        module.open_sealed(sealed, rejected)
    assert not rejected.exists()


def test_candidate_transport_round_trip_is_context_bound_and_encrypts_without_secret(
    tmp_path: Path, monkeypatch
):
    module = _load_script(
        "candidate_transport",
        "scripts/release/candidate-transport.py",
    )
    context_value = {
        "schema": 1,
        "repository": "CoderLuii/ChannelWatch",
        "run_id": "123456",
        "run_attempt": "1",
        "source_sha": "a" * 40,
        "release_tag": "v1.5.0",
        "release_config_sha256": "b" * 64,
    }
    context = tmp_path / "context.json"
    context.write_bytes(module._canonical(context_value))
    public_key = tmp_path / "public.json"
    sealed_private = tmp_path / "private.sealed"
    source = tmp_path / "candidate.oci.tar"
    encrypted = tmp_path / "candidate.transport"
    opened = tmp_path / "opened.oci.tar"
    source.write_bytes(os.urandom((2 * module.CHUNK_BYTES) + 31))
    monkeypatch.setenv("CHANNELWATCH_UPDATE_SIGNING_KEY", "test-production-signing-secret")
    sealer = module._load_sealer()
    original_seal = sealer.seal

    def assert_private_mode_before_sealing(private_path, destination):
        assert private_path.stat().st_mode & 0o777 == 0o600
        return original_seal(private_path, destination)

    monkeypatch.setattr(sealer, "seal", assert_private_mode_before_sealing)
    monkeypatch.setattr(module, "_load_sealer", lambda: sealer)

    module.generate_keypair(context, public_key, sealed_private)
    monkeypatch.delenv("CHANNELWATCH_UPDATE_SIGNING_KEY")
    module.encrypt(source, encrypted, public_key, context, source.name)

    assert encrypted.read_bytes()[: len(module.MAGIC)] == module.MAGIC
    assert source.read_bytes() not in encrypted.read_bytes()
    monkeypatch.setenv("CHANNELWATCH_UPDATE_SIGNING_KEY", "test-production-signing-secret")
    module.decrypt(encrypted, opened, sealed_private, context, source.name)
    assert opened.read_bytes() == source.read_bytes()
    assert opened.stat().st_mode & 0o777 == 0o600

    wrong_context_value = {**context_value, "run_attempt": "2"}
    wrong_context = tmp_path / "wrong-context.json"
    wrong_context.write_bytes(module._canonical(wrong_context_value))
    for bad_context, bad_name in (
        (wrong_context, source.name),
        (context, "different.oci.tar"),
    ):
        rejected = tmp_path / f"rejected-{bad_name}"
        with pytest.raises(ValueError):
            module.decrypt(encrypted, rejected, sealed_private, bad_context, bad_name)
        assert not rejected.exists()


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="The release workflow shell regression requires bash and jq.",
)
def test_candidate_transport_workflow_shell_produces_exact_canonical_context(
    tmp_path: Path,
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )
    signing_step = next(
        step
        for step in workflow["jobs"]["candidate-signing"]["steps"]
        if step.get("name") == "Sign, verify, and seal immutable candidate inputs"
    )
    lines = signing_step["run"].splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith("config_sha256=")
    )
    end = next(
        index
        for index, line in enumerate(lines[start:], start)
        if "--sealed-private-key-out" in line
    )
    workflow_fragment = "\n".join(lines[start : end + 1])

    release_root = tmp_path / "release"
    scripts = release_root / "scripts" / "release"
    scripts.mkdir(parents=True)
    for name in (
        "candidate-transport.py",
        "seal-candidate-artifact.py",
        "release-config.json",
    ):
        shutil.copyfile(ROOT / "scripts" / "release" / name, scripts / name)
    (release_root / "dist" / "candidate-initial").mkdir(parents=True)
    signing_key = base64.b64encode(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode("ascii")
    shell = f"""\
set -euo pipefail
signing_key="${{CHANNELWATCH_UPDATE_SIGNING_KEY}}"
{workflow_fragment}
printf 'exact candidate bytes' > dist/candidate-initial/plain.bin
python scripts/release/candidate-transport.py encrypt \\
  --context dist/candidate-initial/transport-context.json \\
  --public-key dist/candidate-initial/transport-public.json \\
  --input dist/candidate-initial/plain.bin \\
  --output dist/candidate-initial/plain.transport \\
  --artifact-name plain.bin
CHANNELWATCH_UPDATE_SIGNING_KEY="${{signing_key}}" python scripts/release/candidate-transport.py decrypt \\
  --context dist/candidate-initial/transport-context.json \\
  --sealed-private-key dist/candidate-initial/transport-private.sealed \\
  --input dist/candidate-initial/plain.transport \\
  --output dist/candidate-initial/plain.opened \\
  --artifact-name plain.bin
cmp dist/candidate-initial/plain.bin dist/candidate-initial/plain.opened
CHANNELWATCH_UPDATE_SIGNING_KEY="${{signing_key}}" python scripts/release/seal-candidate-artifact.py seal \\
  --input dist/candidate-initial/plain.bin \\
  --output dist/candidate-initial/plain.sealed
CHANNELWATCH_UPDATE_SIGNING_KEY="${{signing_key}}" python scripts/release/seal-candidate-artifact.py open \\
  --input dist/candidate-initial/plain.sealed \\
  --output dist/candidate-initial/plain.unsealed
cmp dist/candidate-initial/plain.bin dist/candidate-initial/plain.unsealed
"""
    environment = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
        "CHANNELWATCH_UPDATE_SIGNING_KEY": signing_key,
        "GITHUB_REPOSITORY": "CoderLuii/ChannelWatch",
        "GITHUB_RUN_ID": "38109517736",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "26df97e7eef46ea6f2f58dce492311683e617523",
        "GITHUB_REF_NAME": "v1.4.4",
    }

    subprocess.run(
        ["bash", "-c", shell],
        cwd=release_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    transport = _load_script(
        "candidate_transport_workflow_context",
        "scripts/release/candidate-transport.py",
    )
    context = release_root / "dist" / "candidate-initial" / "transport-context.json"
    value = json.loads(context.read_text(encoding="utf-8"))
    assert context.read_bytes() == transport._canonical(value)


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("tar") is None,
    reason="The release workflow archive regression requires bash and tar.",
)
def test_signed_candidate_workflow_archive_round_trip_preserves_dist_layout(
    tmp_path: Path,
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )
    signing_step = next(
        step
        for step in workflow["jobs"]["candidate-signing"]["steps"]
        if step.get("name") == "Sign, verify, and seal immutable candidate inputs"
    )
    signing_lines = signing_step["run"].splitlines()
    archive_start = next(
        index for index, line in enumerate(signing_lines) if line.startswith("tar --sort")
    )
    archive_end = next(
        index
        for index, line in enumerate(signing_lines[archive_start:], archive_start)
        if "--output dist/candidate-initial/signed-assets.sealed" in line
    )
    archive_fragment = "\n".join(signing_lines[archive_start : archive_end + 1])
    opener_step = next(
        step
        for step in workflow["jobs"]["candidate-qualification"]["steps"]
        if step.get("name") == "Open signed assets before qualification starts"
    )

    signer_root = tmp_path / "signer"
    signer_scripts = signer_root / "scripts" / "release"
    signer_scripts.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts/release/seal-candidate-artifact.py",
        signer_scripts / "seal-candidate-artifact.py",
    )
    for name in ("candidate-one", "candidate-two"):
        candidate = signer_root / "dist" / name
        candidate.mkdir(parents=True)
        (candidate / "marker.txt").write_text(name, encoding="utf-8")
    (signer_root / "dist" / "candidate-initial").mkdir()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    signing_key = base64.b64encode(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode("ascii")
    environment = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
        "CHANNELWATCH_UPDATE_SIGNING_KEY": signing_key,
        "RUNNER_TEMP": str(runner_temp),
    }
    subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail\nsigning_key="${{CHANNELWATCH_UPDATE_SIGNING_KEY}}"\n{archive_fragment}',
        ],
        cwd=signer_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    signer_artifact = runner_temp / "candidate-signer"
    signer_artifact.mkdir()
    shutil.copyfile(
        signer_root / "dist" / "candidate-initial" / "signed-assets.sealed",
        signer_artifact / "signed-assets.sealed",
    )
    dependency_payload = runner_temp / "dependency-payload" / "dependencies" / "site-packages"
    dependency_payload.mkdir(parents=True)
    runtime_dependencies = runner_temp / "runtime-dependencies"
    runtime_dependencies.mkdir()
    dependency_archive = runtime_dependencies / "channelwatch-runtime-dependencies-amd64.tar"
    subprocess.run(
        [
            "tar",
            "-cf",
            str(dependency_archive),
            "-C",
            str(dependency_payload.parents[1]),
            ".",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    dependency_digest = hashlib.sha256(dependency_archive.read_bytes()).hexdigest()
    (runtime_dependencies / f"{dependency_archive.name}.sha256").write_text(
        f"{dependency_digest}  {dependency_archive.name}\n",
        encoding="utf-8",
    )
    qualification_root = tmp_path / "qualification"
    qualification_scripts = qualification_root / "scripts" / "release"
    qualification_scripts.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts/release/seal-candidate-artifact.py",
        qualification_scripts / "seal-candidate-artifact.py",
    )
    subprocess.run(
        ["bash", "-c", opener_step["run"]],
        cwd=qualification_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert (
        qualification_root / "dist" / "candidate-one" / "marker.txt"
    ).read_text(encoding="utf-8") == "candidate-one"
    assert (
        qualification_root / "dist" / "candidate-two" / "marker.txt"
    ).read_text(encoding="utf-8") == "candidate-two"


def test_candidate_transport_rejects_tamper_truncation_and_wrong_private_key(
    tmp_path: Path, monkeypatch
):
    module = _load_script(
        "candidate_transport_tamper",
        "scripts/release/candidate-transport.py",
    )
    context_value = {
        "schema": 1,
        "repository": "CoderLuii/ChannelWatch",
        "run_id": "42",
        "run_attempt": "3",
        "source_sha": "c" * 40,
        "release_tag": "v1.5.1",
        "release_config_sha256": "d" * 64,
    }
    context = tmp_path / "context.json"
    context.write_bytes(module._canonical(context_value))
    source = tmp_path / "candidate.tar"
    source.write_bytes(os.urandom(4097))
    monkeypatch.setenv("CHANNELWATCH_UPDATE_SIGNING_KEY", "test-production-signing-secret")
    public_one = tmp_path / "one.public.json"
    private_one = tmp_path / "one.private.sealed"
    public_two = tmp_path / "two.public.json"
    private_two = tmp_path / "two.private.sealed"
    module.generate_keypair(context, public_one, private_one)
    module.generate_keypair(context, public_two, private_two)
    monkeypatch.delenv("CHANNELWATCH_UPDATE_SIGNING_KEY")
    encrypted = tmp_path / "candidate.transport"
    module.encrypt(source, encrypted, public_one, context, source.name)
    monkeypatch.setenv("CHANNELWATCH_UPDATE_SIGNING_KEY", "test-production-signing-secret")

    variants = {
        "tampered": bytearray(encrypted.read_bytes()),
        "truncated": bytearray(encrypted.read_bytes()[:-1]),
    }
    variants["tampered"][-module.TAG_BYTES - 1] ^= 1
    for label, content in variants.items():
        candidate = tmp_path / f"{label}.transport"
        candidate.write_bytes(content)
        output = tmp_path / f"{label}.out"
        with pytest.raises(Exception):
            module.decrypt(candidate, output, private_one, context, source.name)
        assert not output.exists()

    wrong_key_output = tmp_path / "wrong-key.out"
    with pytest.raises(ValueError):
        module.decrypt(encrypted, wrong_key_output, private_two, context, source.name)
    assert not wrong_key_output.exists()


def test_candidate_transport_rejects_symlink_and_hardlink_inputs(tmp_path: Path):
    module = _load_script(
        "candidate_transport_links",
        "scripts/release/candidate-transport.py",
    )
    source = tmp_path / "source.bin"
    source.write_bytes(b"candidate")
    symlink = tmp_path / "source-symlink.bin"
    symlink.symlink_to(source)
    hardlink = tmp_path / "source-hardlink.bin"
    os.link(source, hardlink)

    with pytest.raises(ValueError, match="regular, unlinked"):
        module._regular_file(symlink, "input")
    with pytest.raises(ValueError, match="regular, unlinked"):
        module._regular_file(hardlink, "input")


def test_candidate_workflow_uploads_only_sealed_production_assets():
    candidate = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    publication = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    upload = candidate.split(
        "      - name: Retain sealed nonpublishing candidate evidence", 1
    )[1]

    assert "seal-candidate-artifact.py seal" in candidate
    assert "path: dist/candidate-sealed/*.sealed" in upload
    assert "path: dist/candidate-one/" not in upload
    assert publication.count("seal-candidate-artifact.py open") >= 3


def test_release_workflow_serializes_publication_and_preserves_immutability():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "group: channelwatch-release-publication" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "\npermissions: {}\n" in workflow
    assert "verify-release-candidate.py" in workflow
    assert '--sha "${RELEASE_SHA}"' in workflow
    assert "--main-ref origin/main" in workflow
    assert "is already published and is immutable" in workflow
    assert '.draft and .tag_name == \\"${TAG}\\"' in workflow
    assert '.tag_name == \\"${TAG}\\" or' not in workflow
    assert '--target "${RELEASE_SHA}"' in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "python-version: '3.14'" in workflow
    assert "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1" in workflow
    assert "node-version: '24'" in workflow
    assert "package-manager-cache: false" in workflow

    assert workflow.count("scripts/release/verify-minimum-image-app-update.py") == 2
    assert "Verify app update frontend on the minimum compatible image" in workflow
    assert "Reverify app update frontend on the minimum compatible image" in workflow
    assert '--minimum-image-version "${minimum_image_version}"' in workflow
    assert workflow.count(
        '--image-index-digest "$(jq -r \'.minimum_image_sha256\' scripts/release/release-config.json)"'
        ) == 4
    assert '--bundle "dist/candidate-one/channelwatch-app-v${version}.zip"' in workflow
    assert '--bundle "dist/update/channelwatch-app-${RELEASE_TAG}.zip"' in workflow
    candidate_canary = workflow.split(
        "      - name: Verify app update frontend on the minimum compatible image",
        1,
    )[1].split("\n      - name:", 1)[0]
    assert (
        '--runtime-bundle "dist/candidate-one/channelwatch-runtime-v${version}-amd64.zip"'
        in candidate_canary
    )
    assert (
        '--runtime-catalog "dist/candidate-one/channelwatch-update-catalog-v3.json"'
        in candidate_canary
    )
    reverify_block = workflow.split(
        "      - name: Reverify app update frontend on the minimum compatible image",
        1,
    )[1].split("\n      - name:", 1)[0]
    assert "VERSION: ${{ steps.version.outputs.version }}" in reverify_block
    assert '--version "${VERSION}"' in reverify_block
    assert '--bundle "dist/update/channelwatch-app-${RELEASE_TAG}.zip"' in reverify_block
    assert (
        '--runtime-bundle "dist/update/channelwatch-runtime-${RELEASE_TAG}-amd64.zip"'
        in reverify_block
    )
    assert (
        '--runtime-catalog "dist/update/channelwatch-update-catalog-v3.json"'
        in reverify_block
    )
    assert "${TAG}" not in reverify_block

    publish_job = workflow.split("  publish-github-release:", 1)[1].split(
        "\n  verify-public-release-assets:", 1
    )[0]
    assert "fetch-depth: 0" in publish_job
    assert publish_job.index("Install update verifier dependencies") < publish_job.index(
        "Verify pinned bridge or rerun v0.9.18 historical canaries"
    )
    assert "-c deploy/requirements/runtime.constraints.txt" in publish_job
    assert "-r deploy/requirements/runtime.txt" in publish_job
    assert "missing or already published; published releases are immutable" in publish_job
    assert ".draft == true" in publish_job
    assert "'.target_commitish'" in publish_job


def test_every_release_verifier_installs_pinned_runtime_dependencies_first():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    job_starts = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", workflow))
    checked = []

    for index, match in enumerate(job_starts):
        end = job_starts[index + 1].start() if index + 1 < len(job_starts) else len(workflow)
        job_name = match.group(1)
        block = workflow[match.start():end]
        for verifier in (
            "scripts/release/verify-pinned-v1-bridge.py",
            "scripts/release/verify-update-assets.py",
            "scripts/release/verify-image-upgrade.py",
        ):
            for invocation in re.finditer(re.escape(verifier), block):
                prefix = block[: invocation.start()]
                assert "actions/setup-python@" in prefix, job_name
                assert "python-version: '3.14'" in prefix, job_name
                if verifier == "scripts/release/verify-image-upgrade.py":
                    assert "Download the native verifier dependency closure" in prefix, job_name
                    assert "Prepare the native verifier dependency closure" in prefix, job_name
                    assert (
                        "runtime-dependencies-${{ matrix.architecture }}-${{ github.sha }}"
                        in prefix
                    ), job_name
                    assert "sha256sum --check" in prefix, job_name
                    assert (
                        "PYTHONPATH=${dependency_root}/dependencies/site-packages"
                        in prefix
                    ), job_name
                else:
                    assert "-c deploy/requirements/runtime.constraints.txt" in prefix, job_name
                    assert "-r deploy/requirements/runtime.txt" in prefix, job_name
                checked.append((job_name, verifier))

    assert checked


def test_release_workflow_uses_one_tag_driven_candidate_run():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:",
        1,
    )[0]

    assert "workflow_dispatch:" not in workflow
    assert "RELEASE_TAG: ${{ github.ref_name }}" in workflow
    assert "RELEASE_SHA: ${{ github.sha }}" in workflow
    assert workflow.count("ref: ${{ env.RELEASE_SHA }}") == 7
    assert "if: github.event_name == 'push'" in release_job
    assert "needs: [signed-candidate, native-image-qualification]" in release_job
    assert "promote-release-identity" not in workflow
    assert "APPROVED_ARTIFACT_ID: ${{ needs.signed-candidate.outputs.artifact_id }}" in release_job
    assert "The verification job did not produce an approved candidate artifact" in release_job


def test_release_workflow_has_one_candidate_publication_run_per_release():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    trigger = workflow.split("on:", 1)[1].split("\nconcurrency:", 1)[0]
    assert 'push:\n    tags:\n      - "v*"' in trigger
    assert "push:\n    branches:" not in trigger
    assert "pull_request:\n    branches:\n      - main" in trigger
    assert "cleanup-failed-v0918" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert 'run-name: "${{ github.event_name == \'push\' && github.ref_name' in workflow


def test_release_runs_the_complete_backend_suite_without_retry():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:",
        1,
    )[0]
    gate = release_job.split("      - name: Run Python release gates", 1)[1].split(
        "\n      - name: Build frontend for app bundle",
        1,
    )[0]

    assert "python -m pytest app/core/tests" in gate
    assert "--deselect" not in gate
    assert "retry" not in gate.lower()
    assert "||" not in gate


def test_release_workflow_scans_verified_single_platform_archives():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    candidate_scan = workflow.split(
        "      - name: Scan exact nonpublishing candidate image", 1
    )[1].split(
        "\n      - name: Retain accepted upstream image vulnerabilities", 1
    )[0]
    release_scan = workflow.split(
        "      - name: Scan exact release candidate images", 1
    )[1].split(
        "\n      - name: Retain accepted upstream image vulnerabilities", 1
    )[0]

    for scan, descriptor in (
        (
            candidate_scan,
            'descriptor="dist/candidate-one/channelwatch-image-${tag}.json"',
        ),
        (release_scan, 'descriptor="${RUNNER_TEMP}/release-image.json"'),
    ):
        assert descriptor in scan
        assert 'arch="${platform#linux/}"' in scan
        assert "docker run --rm --pull=never" in scan
        assert '"${SKOPEO_IMAGE}" copy' in scan
        assert '--override-arch "${arch}"' in scan
        assert "oci:/work/channelwatch.oci:release-candidate" in scan
        assert 'docker-archive:/output/channelwatch-scan-${arch}.tar:' in scan
        assert "expected_config_digest=" in scan
        assert ".config.digest" in scan
        assert "actual_config_digest=" in scan
        assert 'config_architecture="$(tar -xOf' in scan
        assert 'config_os="$(tar -xOf' in scan
        assert '"${config_architecture}" != "${arch}"' in scan
        assert '"${config_os}" != "linux"' in scan
        assert '"${actual_config_digest}" != "${expected_config_digest}"' in scan
        assert scan.count('--input "${scan_archive}"') == 2
        assert '--input "${OCI_LAYOUT}"' not in scan
        assert '--platform "${platform}"' not in scan

    assert 'docker pull "${SKOPEO_IMAGE}"' in release_scan


def test_release_workflow_publishes_only_the_scanned_multiarch_archive():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    candidate = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    image_job = workflow.split("  build-and-push:", 1)[1].split(
        "\n  update-dockerhub-description:", 1
    )[0]

    first_candidate_build = candidate.index(
        "      - name: Build first exact multi-architecture image"
    )
    second_candidate_build = candidate.index(
        "      - name: Build second exact multi-architecture image"
    )
    recovery_canary_build = candidate.index(
        "      - name: Load exact-source AMD64 image for historical recovery canaries"
    )
    candidate_scan = candidate.index(
        "      - name: Scan exact nonpublishing candidate image"
    )
    candidate_upload = candidate.index(
        "      - name: Retain sealed nonpublishing candidate evidence"
    )
    assert (
        first_candidate_build
        < second_candidate_build
        < recovery_canary_build
        < candidate_scan
        < candidate_upload
    )
    assert candidate.count("docker/build-push-action@") == 4
    assert candidate.count("no-cache: true") == 2
    assert candidate.count("provenance: false") == 3
    assert candidate.count("rewrite-timestamp=true") == 2
    assert candidate.count("SOURCE_DATE_EPOCH=${{ env.CANDIDATE_SOURCE_EPOCH }}") == 3
    assert "load: true" in candidate
    assert "push: false" in candidate
    assert "Verify pinned bridge or run v0.9.18 historical canaries" in candidate
    assert 'if [ "${GITHUB_SHA}" != "${EXPECTED_SHA}" ]' in candidate
    assert 'cmp --silent "${archive}" "${second_archive}"' in candidate
    assert "channelwatch-image-${tag}.oci.tar" in candidate

    import_index = image_job.index("      - name: Download exact approved candidate image")
    scan_index = image_job.index("      - name: Scan exact release candidate images")
    docker_login_index = image_job.index("      - name: Login to Docker Hub")
    ghcr_login_index = image_job.index("      - name: Login to GHCR")
    publish_index = image_job.index(
        "      - name: Publish the scanned images and assemble exact manifests"
    )

    assert import_index < scan_index < docker_login_index
    assert scan_index < ghcr_login_index < publish_index
    assert "docker/build-push-action@" not in image_job
    assert "docker/metadata-action@" not in image_job
    assert "docker/setup-qemu-action@" not in image_job
    assert "docker/setup-buildx-action@" not in image_job
    assert "sha256sum --check SHA256SUMS.txt" in image_job
    assert "describe-oci-image.py" in image_job
    assert 'tar -xf "${archive}"' in image_job
    assert 'cmp --silent "${descriptor}"' in image_job
    assert "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567" in image_job
    scan_block = image_job.split(
        "      - name: Scan exact release candidate images", 1
    )[1].split("\n      - name: Retain accepted upstream image vulnerabilities", 1)[0]
    scan_commands = scan_block.split("            trivy image", 1)[1]
    vulnerability_scan, enforced_scan = scan_commands.split(
        "            trivy image", 1
    )
    assert "--scanners vuln" in vulnerability_scan
    assert "--severity CRITICAL,HIGH,MEDIUM,LOW" in vulnerability_scan
    assert "--exit-code 0" in vulnerability_scan
    assert "--scanners secret,misconfig" in enforced_scan
    assert "--severity CRITICAL,HIGH" in enforced_scan
    assert "--exit-code 1" in enforced_scan
    assert "Retain accepted upstream image vulnerabilities" in image_job
    assert scan_block.count('--input "${scan_archive}"') == 2
    assert '--input "${OCI_LAYOUT}"' not in scan_block
    assert '--platform "${platform}"' not in scan_block

    syft_index = image_job.index("      - name: Install pinned Syft")
    download_assets_index = image_job.index(
        "      - name: Download and verify existing draft release assets"
    )
    license_assets_index = image_job.index(
        "      - name: Generate and verify exact-image license assets"
    )
    attach_assets_index = image_job.index(
        "      - name: Attach verified license and SBOM assets to draft release"
    )
    assert (
        scan_index
        < syft_index
        < download_assets_index
        < license_assets_index
        < attach_assets_index
    )
    assert attach_assets_index < docker_login_index
    assert (
        "anchore/sbom-action/download-syft@"
        "66cbf4bc1f1c0d2edc94016e65bc221b6bb0ad6c"
    ) in image_job
    assert "syft-version: v1.54.1" in image_job
    assert '"oci-dir:${OCI_LAYOUT}"' in image_job
    assert '"spdx-json=${spdx}"' in image_job
    assert '"cyclonedx-json=${cyclonedx}"' in image_job
    assert 'any(.packages[]; .name == "zeroconf")' in image_job
    assert 'any(.components[]; .name == "zeroconf")' in image_job
    assert "channelwatch-${TAG}-LICENSE.txt" in image_job
    assert "channelwatch-${TAG}-NOTICE.txt" in image_job
    assert "channelwatch-${TAG}-THIRD-PARTY-LICENSES.md" in image_job
    assert "channelwatch-${TAG}-CORRESPONDING-SOURCE.md" in image_job
    assert "channelwatch-${TAG}-COPYLEFT-LICENSES.zip" in image_job
    assert "channelwatch-${TAG}-SHA256SUMS.txt" in image_job
    assert "scripts/release/copyleft_licenses.py" in image_job
    assert "--image-license-root" in image_job
    assert '--platform "linux/${arch}"' in image_job
    assert '"channelwatch-legal-${arch}:release")' in image_job
    assert 'docker cp -L "${container_id}:/usr/share/doc/."' in image_job
    assert 'docker cp -L "${container_id}:/usr/share/common-licenses/."' in image_job
    assert '--override-arch "${arch}"' in image_job
    assert "docker-archive:/output/channelwatch-legal-${arch}.tar" in image_job
    assert "expected_checksum_count=\"${#required_assets[@]}\"" in image_job
    assert (
        "Release checksum manifest must cover all ${expected_checksum_count} "
        "non-checksum assets"
    ) in image_job
    for existing_asset in (
        "channelwatch-app-${TAG}.zip",
        "channelwatch-update-${TAG}.json",
        "channelwatch-catalog-${TAG}.json",
        "channelwatch-${TAG}.openvex.json",
    ):
        assert existing_asset in image_job
    assert "Signed assets must come from the matching exact-SHA draft release" in image_job
    assert 'gh release download "${TAG}"' in image_job
    assert 'gh release upload "${TAG}" dist/release/*' in image_job
    assert "Draft release contains an unexpected or missing asset" in image_job
    assert "find dist/release -maxdepth 1 -type f -exec basename" in image_job
    assert "--jq '.assets[].name'" in image_job
    assert "License assets may only be attached to the matching draft release" in image_job
    assert "Draft release target does not match ${RELEASE_SHA}" in image_job

    publish_job = image_job[publish_index:]
    assert "quay.io/containers/skopeo:v1.22.3-immutable@sha256:f45eb1e14223a5ba0da4246274679a083653dab1402acc1fcb70a6339b50cf00" in workflow
    assert "Verify pinned publication helper is available" in workflow
    assert 'docker pull "${SKOPEO_IMAGE}"' in workflow
    assert 'root_index="$(cat "${OCI_LAYOUT}/index.json")"' in publish_job
    assert 'image_index_digest="$(jq -er' in publish_job
    assert 'image_index_blob="${OCI_LAYOUT}/blobs/sha256/${image_index_digest#sha256:}"' in publish_job
    assert "OCI layout does not reference exactly one nested image index" in publish_job
    assert "Scanned OCI layout is missing its nested image-index blob" in publish_job
    assert "oci:/work/channelwatch.oci:release-candidate" in publish_job
    assert publish_job.count("--preserve-digests") == 1
    assert "preflight_registry" in publish_job
    assert 'dockerhub_state="$(preflight_registry "${DOCKERHUB_IMAGE}")"' in publish_job
    assert 'ghcr_state="$(preflight_registry "${GHCR_IMAGE}")"' in publish_job
    assert publish_job.index("dockerhub_state=") < publish_job.index(
        'dockerhub_digest="$(publish_registry'
    )
    assert publish_job.index("ghcr_state=") < publish_job.index(
        'dockerhub_digest="$(publish_registry'
    )
    assert "Refusing to overwrite existing immutable tag" in publish_job
    assert 'if [ "${image_index_digest}" != "${APPROVED_IMAGE_INDEX_DIGEST}" ]' in publish_job
    assert (
        "      APPROVED_IMAGE_INDEX_DIGEST: "
        "${{ needs.build-update-bundle-and-release.outputs.approved_image_index_digest }}"
    ) in image_job.split("    steps:", 1)[0]
    assert "Published manifest does not contain the exact scanned platform descriptors" in publish_job
    assert '"docker://${repository}@${version_digest}"' not in publish_job
    assert '"${VERSION}-amd64"' not in publish_job
    assert '"${VERSION}-arm64"' not in publish_job
    assert "steps.publish.outputs.dockerhub_digest" in image_job
    assert "steps.build.outputs.digest" not in image_job
    assert "\n          push: true\n" not in image_job
    assert image_job.count(
        "DOCKER_CONFIG: ${{ runner.temp }}/docker-auth/.docker"
    ) == 4
    assert '--volume "${DOCKER_CONFIG}:/auth:ro"' in publish_job

    attest_job = image_job.split("      - name: Attest build provenance", 1)[1]
    attest_uses_index = attest_job.index("uses: actions/attest-build-provenance@")
    assert attest_job.index(
        "DOCKER_CONFIG: ${{ runner.temp }}/docker-auth/.docker"
    ) < attest_uses_index
    assert attest_job.index("HOME: ${{ runner.temp }}/docker-auth") < attest_uses_index


def test_live_catalog_requires_the_exact_approved_bytes():
    module = _load_script("verify_live_catalog_digest", "scripts/release/verify-live-update-manifest.py")
    catalog = b'{"payload":{"version":"1.6.0","minimum_image_version":"1.4.0"}}'
    expected = hashlib.sha256(catalog).hexdigest()
    module.verify_catalog_digest(catalog, expected)
    with pytest.raises(ValueError, match="approved release catalog"):
        module.verify_catalog_digest(catalog.replace(b"1.4.0", b"1.5.3"), expected)
    with pytest.raises(ValueError, match="SHA-256 is invalid"):
        module.verify_catalog_digest(catalog, "")


def test_release_workflow_verifies_generated_and_live_update_assets():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    assert "verify-update-assets.py" in release_job
    assert '--expected-channel "${EXPECTED_CHANNEL}"' in release_job
    assert '--expected-runtime-abi "${EXPECTED_RUNTIME_ABI}"' in release_job
    assert (
        '--expected-settings-schema-version "${EXPECTED_SETTINGS_SCHEMA_VERSION}"'
        in release_job
    )
    assert '--expected-image-required "${expected_image_required}"' in release_job
    assert '--catalog "dist/update/channelwatch-catalog-${TAG}.json"' in release_job
    assert '--expected-delivery-mode "${expected_delivery_mode}"' in release_job
    assert (
        '--expected-recommended-image-version '
        '"${expected_recommended_image_version}"'
    ) in release_job
    assert '--expected-git-sha "${EXPECTED_GIT_SHA}"' in release_job
    assert '--expected-release-url "https://github.com/' in release_job
    assert '--expected-bundle-url "https://github.com/' in release_job
    assert '--source-root "${RELEASE_SOURCE_ROOT}"' in release_job
    assert "EXPECTED_GIT_SHA: ${{ env.RELEASE_SHA }}" in release_job
    assert "EXPECTED_CHANNEL: stable" in release_job
    assert "EXPECTED_RUNTIME_ABI: channelwatch-runtime-v1" in release_job
    assert 'EXPECTED_SETTINGS_SCHEMA_VERSION: "7"' in release_job

    live_script = (ROOT / "scripts/release/verify-live-update-manifest.py").read_text(
        encoding="utf-8"
    )
    assert "verify_update_assets" in live_script
    assert "bundle_url" in live_script
    assert "verifier.fetch_bytes" in live_script


def test_publication_requires_byte_identical_exact_sha_candidate_assets():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    candidate = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]

    assert "actions: read" in release_job
    assert "Open the exact qualified candidate before release checks" in release_job
    assert "APPROVED_ARTIFACT_ID: ${{ needs.signed-candidate.outputs.artifact_id }}" in release_job
    assert "The verification job did not produce an approved candidate artifact" in release_job
    assert "Build signed app bundle" not in release_job
    assert "seal-candidate-artifact.py open" in release_job
    assert "channelwatch-app-${TAG}.zip" in release_job
    assert "channelwatch-update-${TAG}.json" in release_job
    assert "channelwatch-catalog-${TAG}.json" in release_job
    assert 'image_descriptor="channelwatch-image-${TAG}.json"' in release_job
    assert 'image_digest="channelwatch-image-${TAG}.digest"' in release_job
    assert 'image_archive="channelwatch-image-${TAG}.oci.tar"' in release_job
    assert "approved_candidate_artifact_id:" in release_job
    assert "approved_image_index_digest:" in release_job
    assert "approved_image_descriptor_sha256:" in release_job
    assert "approved_image_archive_sha256:" in release_job
    assert "sha256sum --check SHA256SUMS.txt" in release_job
    assert (
        "name: channelwatch-release-candidate-${{ github.sha }}"
        in candidate
    )
    assert 'run-name: "${{ github.event_name == \'push\' && github.ref_name' in candidate
    assert "push:" in candidate
    assert '- "v*"' in candidate
    assert "branches:\n      - main\n  pull_request:" not in candidate
    assert "workflow_dispatch:" not in candidate
    assert "if: startsWith(github.ref, 'refs/tags/v')" in candidate
    assert "CHANNELWATCH_BUILD_ID: ${{ github.sha }}" in candidate
    assert not (ROOT / ".github/workflows/release-candidate.yml").exists()
    assert not (ROOT / ".github/workflows/ci.yml").exists()
    assert list((ROOT / ".github/workflows").glob("*.yml")) == [
        ROOT / ".github/workflows/docker-publish.yml"
    ]
    assert "EXPECTED_TAG: ${{ github.ref_name }}" in candidate
    assert "EXPECTED_SHA: ${{ github.sha }}" in candidate
    assert 'actual_tag="v${actual_version}"' in candidate
    assert '"${GITHUB_REF}" != "refs/tags/${actual_tag}"' in candidate
    assert 'tag_object_type="$(git cat-file -t "refs/tags/${EXPECTED_TAG}")"' in candidate
    assert '"${remote_main}" != "${EXPECTED_SHA}"' in candidate
    assert 'git push origin "${RELEASE_SHA}:refs/tags/${RELEASE_TAG}"' not in candidate
    upload_block = candidate.split(
        "      - name: Retain sealed nonpublishing candidate evidence", 1
    )[1]
    assert "path: dist/candidate-sealed/*.sealed" in upload_block
    assert "path: dist/candidate-one/" not in upload_block
    assert "seal-candidate-artifact.py seal" in candidate
    assert "pnpm exec playwright test" in candidate


def test_publication_window_gate_runs_at_every_public_boundary():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    candidate = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    build_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    publish_job = workflow.split("  publish-github-release:", 1)[1].split(
        "\n  verify-public-release-assets:", 1
    )[0]
    sync_job = workflow.split("  sync-site:", 1)[1].split(
        "\n  verify-live-update-manifest:", 1
    )[0]

    assert candidate.index("verify-update-assets.py") < candidate.index(
        "verify-publication-window.py"
    ) < candidate.index("Encrypt qualified candidate for the final sealer")
    assert build_job.index("verify-update-assets.py") < build_job.index(
        "verify-publication-window.py"
    ) < build_job.index("Create or update draft GitHub Release")
    assert "--prospective-publication-time" not in build_job
    assert "needs: build-update-bundle-and-release" in workflow.split(
        "\n  build-and-push:", 1
    )[1].split("\n  update-dockerhub-description:", 1)[0]
    assert "- build-and-push" in publish_job
    assert "catalog_sha256:" in build_job
    assert publish_job.index("EXPECTED_CATALOG_SHA256:") < publish_job.index(
        "verify-publication-window.py"
    ) < publish_job.index("--method PATCH")
    assert sync_job.index("Verify publication window before stable-feed dispatch") < sync_job.index(
        "Dispatch website sync"
    )
    assert sync_job.index("verify-publication-window.py") < sync_job.index(
        "repos/CoderLuii/ChannelWatch-site/dispatches"
    )
    assert sync_job.count("EXPECTED_CATALOG_SHA256:") == 1


def test_public_release_assets_are_verified_before_stable_feed_dispatch():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    public_job = workflow.split("  verify-public-release-assets:", 1)[1].split(
        "\n  sync-site:", 1
    )[0]
    sync_job = workflow.split("  sync-site:", 1)[1].split(
        "\n  verify-live-update-manifest:", 1
    )[0]

    assert "- publish-github-release" in public_job
    assert "Verify public release identity and asset inventory" in public_job
    assert "releases/download/${TAG}/${asset}" in public_job
    assert "--retry 12 --retry-all-errors" in public_job
    assert "sha256sum --check" in public_job
    assert "verify-update-assets.py" in public_job
    assert "verify-legacy-update-bridge.py" in public_job
    assert "verify-pinned-v1-bridge.py" in public_job
    assert 'if [ "${version}" = "0.9.18" ]' in public_job
    assert "verify-publication-window.py" in public_job
    assert "verify-vex.py" in public_job
    assert "channelwatch-${tag}-SHA256SUMS.txt" in public_job
    assert "- verify-public-release-assets" in sync_job


def test_release_workflow_publishes_bridge_and_v2_before_image_aliases():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    build_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    sync_job = workflow.split("  sync-site:", 1)[1].split(
        "\n  verify-live-update-manifest:", 1
    )[0]
    live_job = workflow.split("  verify-live-update-manifest:", 1)[1].split(
        "\n  advance-compatible-aliases:", 1
    )[0]
    alias_job = workflow.split("  advance-compatible-aliases:", 1)[1]

    assert "catalog_url: ${{ steps.assets.outputs.catalog_url }}" in build_job
    assert "channelwatch-catalog-${TAG}.json" in build_job
    assert "verify-pinned-v1-bridge.py" in build_job
    assert 'if [ "${VERSION}" = "0.9.18" ]' in build_job
    assert "UPDATE_CATALOG_URL:" in sync_job
    assert "update_catalog_url: $update_catalog_url" in sync_job
    assert 'if [ "${VERSION}" = "0.9.18" ]' in sync_job
    assert '--arg update_manifest_url "${stable_manifest_url}"' in sync_job
    assert '--catalog-url "https://channelwatch.coderluii.dev/updates/v2/stable.json"' in live_job
    assert "verify-pinned-v1-bridge.py" in live_job
    assert "verify-legacy-update-bridge.py" not in live_job
    assert "Verify pinned public v1 bridge" in live_job
    live_verifier = (ROOT / "scripts/release/verify-live-update-manifest.py").read_text(
        encoding="utf-8"
    )
    assert 'parser.add_argument("--bridge-version", default="0.9.18")' in live_verifier
    assert "trusted_bridge" in live_verifier
    assert "current_release" in live_verifier
    assert "- verify-live-update-manifest" in alias_job
    assert 'compatible_tag="${version%.*}"' not in alias_job
    assert 'for alias_tag in latest' in alias_job
    assert "does not reference the verified version manifest" in alias_job
    assert "type=raw,value=latest" not in workflow


def test_release_workflow_verifies_and_publishes_versioned_openvex():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    verify_index = release_job.index("      - name: Verify release VEX")
    draft_index = release_job.index("      - name: Render and validate release body")
    upload_index = release_job.index("      - name: Upload update assets")

    assert verify_index < draft_index < upload_index
    assert 'deploy/security/channelwatch-${TAG}.openvex.json' in release_job
    assert "python scripts/release/verify-vex.py" in release_job
    assert '"${vex}"' in release_job


def test_ci_trivy_scan_renders_helm_and_fails_if_chart_targets_are_skipped():
    workflow = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    security_job = workflow.split("  security:", 1)[1]

    assert "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567" in security_job
    assert "--ignorefile .trivyignore.yaml" in security_job
    assert "--skip-files deploy/docker/Dockerfile.dockerignore" in security_job
    assert "--helm-set-string secretConfig.secretStorageKey=" not in security_job
    assert (
        "--helm-set-string secretConfig.apiKey="
        "ci-only-placeholder-not-a-real-secret"
    ) in security_job
    assert "channelwatch-trivy.json" in security_job
    assert "channelwatch-trivy-secret.json" in security_job
    assert "CHANNELWATCH_TRIVY_REPORT" in security_job
    assert "CHANNELWATCH_TRIVY_SECRET_REPORT" in security_job
    assert "TRIVY_REPORT:" not in security_job.replace(
        "CHANNELWATCH_TRIVY_REPORT:", ""
    ).replace(
        "CHANNELWATCH_TRIVY_SECRET_REPORT:", ""
    )
    assert 'if result.get("Type") == "helm"' in security_job
    assert "managed-local Helm targets" in security_job
    assert "managed-local Helm render unexpectedly contains a Secret" in security_job
    assert "explicit-Secret Helm targets" in security_job
    assert "templates/configmap.yaml" in security_job
    assert "deploy/helm/channelwatch/templates/deployment.yaml" in security_job
    assert "deploy/helm/channelwatch/templates/secret.yaml" in security_job
    assert '"templates/deployment.yaml"' in security_job
    assert '"templates/secret.yaml"' in security_job


def test_ci_python_job_disables_cross_trust_dependency_caching():
    workflow = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    python_job = workflow.split("  python:", 1)[1].split("\n  frontend:", 1)[0]

    assert "fetch-depth: 0" in python_job
    assert "cache: pip" not in python_job
    assert "cache-dependency-path:" not in python_job


def test_release_runner_routes_only_trusted_tag_pushes_to_configured_runner():
    workflow_path = ROOT / ".github" / "workflows" / "docker-publish.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    expected = (
        "${{ github.event_name == 'push' && "
        "startsWith(github.ref, 'refs/tags/v') && "
        "github.actor == 'CoderLuii' && "
        "vars.CHANNELWATCH_RELEASE_RUNNER || 'ubuntu-latest' }}"
    )

    assert workflow["jobs"]
    protected_jobs = {
        "candidate-source-gates",
        "candidate-signing",
        "runtime-capability",
        "legacy-app-upgrade",
        "native-image-qualification",
        "candidate-qualification",
        "signed-candidate",
        "build-update-bundle-and-release",
        "build-and-push",
        "publish-github-release",
    }
    ordinary_jobs = {
        name: job
        for name, job in workflow["jobs"].items()
        if name != "runtime-dependencies" and name not in protected_jobs
    }
    assert all(job["runs-on"] == expected for job in ordinary_jobs.values())
    assert all(
        workflow["jobs"][name]["runs-on"] == "ubuntu-24.04"
        for name in protected_jobs
        - {"runtime-capability", "legacy-app-upgrade", "native-image-qualification"}
    )
    capability_job = workflow["jobs"]["runtime-capability"]
    assert capability_job["runs-on"] == "${{ matrix.runner }}"
    assert capability_job["strategy"]["matrix"]["include"] == [
        {"architecture": "amd64", "runner": "ubuntu-24.04"},
        {"architecture": "arm64", "runner": "ubuntu-24.04-arm"},
    ]
    legacy_job = workflow["jobs"]["legacy-app-upgrade"]
    assert legacy_job["runs-on"] == "${{ matrix.runner }}"
    assert legacy_job["strategy"]["matrix"]["include"] == [
        {"architecture": "amd64", "runner": "ubuntu-24.04"},
        {"architecture": "arm64", "runner": "ubuntu-24.04-arm"},
    ]
    native_job = workflow["jobs"]["native-image-qualification"]
    assert native_job["runs-on"] == "${{ matrix.runner }}"
    assert native_job["strategy"]["matrix"]["include"] == [
        {"architecture": "amd64", "runner": "ubuntu-24.04"},
        {"architecture": "arm64", "runner": "ubuntu-24.04-arm"},
    ]
    runtime_job = workflow["jobs"]["runtime-dependencies"]
    source_gates = workflow_text.split("  candidate-source-gates:", 1)[1].split(
        "\n  candidate-signing:", 1
    )[0]
    assert runtime_job["runs-on"] == "${{ matrix.runner }}"
    assert runtime_job["strategy"]["matrix"]["include"] == [
        {
            "architecture": "amd64",
            "runner": "ubuntu-24.04",
            "machine": "x86_64",
            "wheel_platform": "manylinux_2_36_x86_64",
        },
        {
            "architecture": "arm64",
            "runner": "ubuntu-24.04-arm",
            "machine": "aarch64",
            "wheel_platform": "manylinux_2_36_aarch64",
        },
    ]
    assert "runs-on: self-hosted" not in workflow_text
    assert "runs-on: [self-hosted" not in workflow_text
    cleanup = next(
        step
        for step in workflow["jobs"]["build-and-push"]["steps"]
        if step.get("name") == "Free up disk space"
    )
    assert cleanup["if"] == "runner.environment == 'github-hosted'"


def test_production_signing_key_isolated_to_first_shell_step_on_fresh_runners():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )
    expected_jobs = {
        "candidate-signing",
        "runtime-capability",
        "legacy-app-upgrade",
        "native-image-qualification",
        "candidate-qualification",
        "signed-candidate",
        "build-update-bundle-and-release",
        "build-and-push",
        "publish-github-release",
    }
    actual_jobs = set()
    for name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        secret_steps = [
            (index, step)
            for index, step in enumerate(steps)
            if "CHANNELWATCH_UPDATE_SIGNING_KEY" in str(step)
        ]
        if not secret_steps:
            continue
        actual_jobs.add(name)
        if name in {"runtime-capability", "legacy-app-upgrade", "native-image-qualification"}:
            assert job["runs-on"] == "${{ matrix.runner }}"
        else:
            assert job["runs-on"] == "ubuntu-24.04"
        assert len(secret_steps) == 1
        secret_index, secret_step = secret_steps[0]
        earlier_runs = [step for step in steps[:secret_index] if "run" in step]
        if name in {"runtime-capability", "legacy-app-upgrade"}:
            assert len(earlier_runs) == 1
            assert earlier_runs[0]["name"] == "Install the existing headless Chromium harness"
            assert earlier_runs[0]["run"] == (
                "corepack enable\n"
                'test "$(pnpm --version)" = "11.28.5"\n'
                "pnpm install --frozen-lockfile\n"
                "pnpm exec playwright install --with-deps chromium\n"
            )
        elif name == "native-image-qualification":
            assert len(earlier_runs) == 1
            assert earlier_runs[0]["name"] == "Install exact binary opener dependencies"
            assert "--only-binary=:all: --require-hashes" in earlier_runs[0]["run"]
        else:
            assert earlier_runs == []
        for earlier_step in steps[:secret_index]:
            assert "cache" not in str(earlier_step.get("uses", "")).lower()
            for key, value in earlier_step.get("with", {}).items():
                if "cache" in str(key).lower():
                    assert value is False
        assert secret_step.get("run")
        assert "unset CHANNELWATCH_UPDATE_SIGNING_KEY" in secret_step["run"]
        assert all(
            "CHANNELWATCH_UPDATE_SIGNING_KEY" not in str(step)
            for step in steps[secret_index + 1 :]
        )
    assert actual_jobs == expected_jobs


def test_native_image_qualification_uses_exact_sealed_oci_on_native_hosts():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["native-image-qualification"]
    text = str(job)
    assert job["needs"] == ["signed-candidate", "runtime-dependencies"]
    assert "verify-native-image.py" in text
    assert "channelwatch-image-${tag}.oci.tar" in text
    assert "channelwatch-image-${tag}.json" in text
    assert "--architecture \"${NATIVE_ARCHITECTURE}\"" in text
    assert ".launcher_protocol == 4" in text
    assert "docker/build-push-action@" not in text
    assert "setup-qemu" not in text
    assert "pnpm exec playwright install --with-deps chromium" in text
    upload = next(
        step
        for step in job["steps"]
        if step.get("name") == "Retain native image qualification receipt"
    )
    assert upload["if"] == "always()"
    release = workflow["jobs"]["build-update-bundle-and-release"]
    assert set(release["needs"]) == {"signed-candidate", "native-image-qualification"}
    evidence_download = next(
        step
        for step in release["steps"]
        if step.get("name") == "Download exact native image qualification evidence"
    )
    assert evidence_download["with"]["pattern"] == "native-image-*-${{ github.sha }}"
    assert evidence_download["with"]["merge-multiple"] is True
    release_text = str(release)
    assert "candidate_seal_sha256" in release_text
    assert "container_log_sha256 == $log_sha256" in release_text
    assert ".launcher_protocol == 4" in release_text
    assert "channelwatch-native-image-${TAG}-evidence.json" in release_text
    assert "channelwatch-native-image-${TAG}-${architecture}.json" in release_text
    assert "channelwatch-native-image-${TAG}-${architecture}.log" in release_text
    assert '["amd64", "arm64"]' in release_text


def test_native_signer_requirements_cover_both_linux_architectures():
    requirements = (
        ROOT / "scripts" / "release" / "signer-requirements.txt"
    ).read_text(encoding="utf-8")
    assert "cryptography==50.0.2" in requirements
    assert "cffi==2.1.1" in requirements
    assert "9dab55f57c74c3cad24c323bacbbd04be4705ba6eb0d92e920b1fc4837ed5079" in requirements
    assert "87e9ce85beb6b328ba370cc6e6aea483c92617b4c95b1d33a49297eb662bfb04" in requirements
    assert "b0431303acaea1089ad4b3e9ce4e6518193def1118d4073ca848635ee4ea2e96" in requirements
    assert "58acb8ab8e295e6c5ea12f888cbb13cf21511ef2a3303a23f4325c29d17fe5c1" in requirements
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )
    native_job = workflow["jobs"]["native-image-qualification"]
    assert "-r scripts/release/signer-requirements.txt" in str(native_job)


def test_native_image_upgrade_uses_verified_runtime_closure_after_key_scope():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["native-image-qualification"]["steps"]
    assert workflow["jobs"]["native-image-qualification"]["needs"] == [
        "signed-candidate",
        "runtime-dependencies",
    ]
    names = [step.get("name") for step in steps]
    download_index = names.index("Download the native verifier dependency closure")
    open_index = names.index("Open exact candidate before native qualification")
    prepare_index = names.index("Prepare the native verifier dependency closure")
    qualify_index = names.index(
        "Qualify published older installations on the foundation image"
    )

    assert download_index < open_index < prepare_index < qualify_index
    download = steps[download_index]
    assert download["with"]["name"] == (
        "runtime-dependencies-${{ matrix.architecture }}-${{ github.sha }}"
    )
    prepare = steps[prepare_index]
    prepare_text = prepare["run"]
    assert "sha256sum --check" in prepare_text
    assert "tarfile.open" in prepare_text
    assert "member.issym()" in prepare_text
    assert "member.islnk()" in prepare_text
    assert "member.isdev()" in prepare_text
    assert "max_members = 10_000" in prepare_text
    assert "max_total_size = 512 * 1024 * 1024" in prepare_text
    assert 'set(inventory) != {' in prepare_text
    assert '"builder_image_digest": builder_digest' in prepare_text
    assert '"architecture": architecture' in prepare_text
    assert '"wheel_platform": wheel_platform' in prepare_text
    assert '"httpx" not in requirements["direct"]' in prepare_text
    assert "sha256.fullmatch(value) is None" in prepare_text
    assert "archive_files != set(expected_files)" in prepare_text
    assert "Runtime dependency archive has untracked directories." in prepare_text
    assert "Extracted runtime dependency content is invalid." in prepare_text
    assert "dependencies/site-packages" in prepare_text
    assert 'echo "PYTHONPATH=${dependency_root}/dependencies/site-packages"' in prepare_text
    assert "CHANNELWATCH_UPDATE_SIGNING_KEY" not in str(steps[prepare_index:])

    qualify_text = steps[qualify_index]["run"]
    for helper in (
        "ASSET_VERIFIER",
        "NATIVE_VERIFIER",
        "CAPABILITY_VERIFIER",
    ):
        assert helper in qualify_text
    assert "verify_signed_baseline_assets" in qualify_text
    assert "scripts/release/image-upgrade-baselines.json" in qualify_text

    runtime_build = next(
        step
        for step in workflow["jobs"]["runtime-dependencies"]["steps"]
        if step.get("name") == "Build and execute deterministic native dependency closure twice"
    )["run"]
    assert 'python -I -B - "/work/runtime-${ARCHITECTURE}-one/dependencies/site-packages"' in runtime_build


@pytest.mark.parametrize("unsafe_member", [None, "extra.py", "symlink"])
def test_native_verifier_runtime_closure_accepts_inventory_and_rejects_unsafe_members(
    tmp_path, unsafe_member
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )
    prepare = next(
        step
        for step in workflow["jobs"]["native-image-qualification"]["steps"]
        if step.get("name") == "Prepare the native verifier dependency closure"
    )["run"]
    validator = prepare.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    payload = b"verified runtime dependency\n"
    payload_path = "dependencies/site-packages/probe.py"
    digest = hashlib.sha256(payload).hexdigest()
    builder_digest = "sha256:" + "4" * 64
    inventory = {
        "schema": 1,
        "build": {
            "builder_image_digest": builder_digest,
            "pip_version": "26.2.1",
            "python_version": "3.14.8",
        },
        "platform": {
            "architecture": "amd64",
            "python_implementation": "cpython",
            "python_cache_tag": "cpython-314",
            "python_version": "3.14.8",
            "os_family": "linux",
            "libc_family": "glibc",
            "minimum_libc_version": "2.36",
            "wheel_platform": "manylinux_2_36_x86_64",
        },
        "requirements": {
            "direct": ["httpx"],
            "locked": [{"name": "httpx", "version": "0.28.1", "hashes": ["5" * 64]}],
        },
        "artifacts": [],
        "dependencies": [],
        "files": [
            {"path": payload_path, "sha256": digest, "size": len(payload), "mode": "0644"}
        ],
        "counts": {"files": 1, "bytes": len(payload)},
    }
    archive = tmp_path / "runtime.tar"
    with tarfile.open(archive, "w") as handle:
        for name in (".", "dependencies", "dependencies/site-packages"):
            row = tarfile.TarInfo(name)
            row.type = tarfile.DIRTYPE
            row.mode = 0o755
            handle.addfile(row)
        inventory_bytes = (json.dumps(inventory, sort_keys=True) + "\n").encode()
        row = tarfile.TarInfo("dependencies/inventory.json")
        row.size = len(inventory_bytes)
        row.mode = 0o644
        handle.addfile(row, io.BytesIO(inventory_bytes))
        row = tarfile.TarInfo(payload_path)
        row.size = len(payload)
        row.mode = 0o644
        handle.addfile(row, io.BytesIO(payload))
        if unsafe_member == "extra.py":
            row = tarfile.TarInfo("dependencies/site-packages/extra.py")
            row.size = 1
            row.mode = 0o644
            handle.addfile(row, io.BytesIO(b"x"))
        elif unsafe_member == "symlink":
            row = tarfile.TarInfo("dependencies/site-packages/link.py")
            row.type = tarfile.SYMTYPE
            row.linkname = "probe.py"
            handle.addfile(row)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            validator,
            str(archive),
            str(tmp_path / "extract"),
            "amd64",
            builder_digest,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    if unsafe_member is None:
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "extract" / payload_path).read_bytes() == payload
        return
    assert result.returncode != 0
    expected = (
        "Runtime dependency archive and inventory differ."
        if unsafe_member == "extra.py"
        else "Runtime dependency archive contains an unsafe member type."
    )
    assert expected in result.stderr


def test_release_workflow_builds_native_runtime_generations_and_seals_exact_assets():
    workflow_path = ROOT / ".github" / "workflows" / "docker-publish.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    signer_checkout = next(
        step for step in workflow["jobs"]["candidate-signing"]["steps"]
        if step.get("name") == "Checkout exact audited signer"
    )
    assert signer_checkout["with"]["fetch-depth"] == 0
    assert signer_checkout["with"]["ref"] == "${{ github.sha }}"
    assert signer_checkout["with"]["persist-credentials"] is False
    runtime_job = workflow["jobs"]["runtime-dependencies"]
    source_gates = workflow_text.split("  candidate-source-gates:", 1)[1].split(
        "\n  candidate-signing:", 1
    )[0]
    signer = workflow_text.split("  candidate-signing:", 1)[1].split(
        "\n  runtime-capability:", 1
    )[0]
    capability = workflow_text.split("  runtime-capability:", 1)[1].split(
        "\n  candidate-qualification:", 1
    )[0]
    candidate = workflow_text.split("  candidate-qualification:", 1)[1].split(
        "\n  signed-candidate:", 1
    )[0]
    final_sealer = workflow_text.split("  signed-candidate:", 1)[1].split(
        "\n  build-update-bundle-and-release:", 1
    )[0]
    release_job = workflow_text.split("  build-update-bundle-and-release:", 1)[
        1
    ].split("\n  build-and-push:", 1)[0]
    public_job = workflow_text.split("  verify-public-release-assets:", 1)[1].split(
        "\n  sync-site:", 1
    )[0]
    sync_job = workflow_text.split("  sync-site:", 1)[1].split(
        "\n  verify-live-update-manifest:", 1
    )[0]
    live_job = workflow_text.split("  verify-live-update-manifest:", 1)[1].split(
        "\n  advance-compatible-aliases:", 1
    )[0]

    runtime_text = workflow_text.split("  runtime-dependencies:", 1)[1].split(
        "\n  candidate-source-gates:", 1
    )[0]
    assert runtime_job["runs-on"] == "${{ matrix.runner }}"
    assert [row["runner"] for row in runtime_job["strategy"]["matrix"]["include"]] == [
        "ubuntu-24.04",
        "ubuntu-24.04-arm",
    ]
    assert "docker/setup-qemu-action@" not in runtime_text
    assert "docker/setup-buildx-action@" not in runtime_text
    assert "docker run --rm --pull=never" in runtime_text
    assert "--platform" not in runtime_text
    assert 'image_architecture="$(docker image inspect' in runtime_text
    assert 'if [ "${image_architecture}" != "${ARCHITECTURE}" ]' in runtime_text
    assert runtime_text.count("build-runtime-dependencies.py") == 1
    assert "for build in one two" in runtime_text
    assert "diff -ru" in runtime_text
    assert "python -I -" in runtime_text
    for module in (
        "bcrypt",
        "cryptography",
        "httptools",
        "pydantic_core",
        "sqlalchemy",
        "uvloop",
        "yaml",
    ):
        assert f"import {module}" in runtime_text

    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in signer
    assert workflow["jobs"]["candidate-source-gates"]["runs-on"] == "ubuntu-24.04"
    assert "path: app/ui/out/" in source_gates
    assert "app/ui/dist" not in source_gates
    assert source_gates.count("build-runtime-bundle.py") == 1
    assert 'for candidate in candidate-one candidate-two' in source_gates
    assert "build-runtime-bundle.py" not in signer
    assert "unsigned-runtime-bundles-${{ github.sha }}" in signer
    assert 'runtime_one_args=(' in signer
    assert 'runtime_two_args=(' in signer
    assert signer.count("--runtime-bundle-url") >= 1
    assert 'diff -u <(cd dist/candidate-one' in signer
    assert "ubuntu-24.04-arm" in capability
    assert "verify-runtime-capability.py" in capability
    assert "verify-runtime-source-upgrade.py" in capability
    assert "channelwatch-runtime-v1.6.0-amd64.zip" in capability
    assert "channelwatch-runtime-v1.6.0-arm64.zip" in capability
    assert "f20578206578901c9a28486c28e2a6f8c2e7974bad4ec314d55a3bcba53e1b39" in capability
    assert "runtime-capability-${NATIVE_ARCHITECTURE}-1.5.3.json" in capability
    assert "runtime-capability-${NATIVE_ARCHITECTURE}-1.6.0.json" in capability
    assert '--architecture "${NATIVE_ARCHITECTURE}"' in capability
    assert "--validate-evidence" in capability
    assert '--architecture "${{ matrix.architecture }}"' in capability
    assert "docker/setup-qemu-action@" not in capability
    assert "{status:\"not_required\"" not in capability
    capability_job = workflow["jobs"]["runtime-capability"]
    conditional_steps = {
        step["name"]: step.get("if")
        for step in capability_job["steps"]
        if step["name"] in {
            "Open signed assets before native qualification starts",
            "Run the native protocol-4 lifecycle verifier",
            "Validate the complete native receipt before retention",
        }
    }
    assert set(conditional_steps.values()) == {
        "needs.candidate-source-gates.outputs.runtime_generation_required == 'true'"
    }
    browser_steps = [step for step in capability_job["steps"] if step["name"] in {
        "Set up Node for headless live Update Center verification",
        "Install the existing headless Chromium harness",
    }]
    assert len(browser_steps) == 2
    assert all(step["if"] == (
        "needs.candidate-source-gates.outputs.runtime_generation_required == 'true' && matrix.architecture == 'amd64'"
    ) for step in browser_steps)
    assert browser_steps[0]["with"]["node-version"] == "24.21.0"
    assert 'test "$(pnpm --version)" = "11.28.5"' in browser_steps[1]["run"]
    assert "pnpm install --frozen-lockfile" in browser_steps[1]["run"]
    assert "pnpm exec playwright install --with-deps chromium" in browser_steps[1]["run"]
    native_step = next(step for step in capability_job["steps"] if step["name"] == "Run the native protocol-4 lifecycle verifier")
    assert native_step["env"]["CI"] == "true"
    assert native_step["env"]["BROWSER"] == "none"
    assert native_step["env"]["PLAYWRIGHT_HTML_OPEN"] == "never"
    step_names = [step["name"] for step in capability_job["steps"]]
    assert step_names.index("Run the native protocol-4 lifecycle verifier") < step_names.index(
        "Validate the complete native receipt before retention"
    ) < step_names.index("Retain native runtime capability evidence")
    retention = next(step for step in capability_job["steps"] if step["name"] == "Retain native runtime capability evidence")
    assert retention["if"] == "needs.candidate-source-gates.outputs.runtime_generation_required == 'true' && always()"
    assert retention["with"]["path"] == "${{ runner.temp }}/runtime-capability/"
    assert retention["with"]["if-no-files-found"] == "error"
    assert "aggregate-runtime-capability.py" in candidate
    assert "aggregate-runtime-source-upgrades.py" in candidate
    for binding in (
        "1.5.3=amd64", "1.5.3=arm64", "1.6.0=amd64", "1.6.0=arm64",
    ):
        assert f'--receipt "{binding}=' in candidate
    assert "channelwatch-runtime-source-upgrades-${tag}.json" in candidate
    assert "scan-runtime-artifacts.py" in candidate
    assert "syft-version: v1.54.1" in candidate
    assert "version: v0.75.0" in candidate
    assert 'install -d -m 0700 "${trivy_cache_dir}"' in candidate
    assert '--trivy-cache-dir "${trivy_cache_dir}"' in candidate
    assert '--syft "${syft_path}"' in candidate
    assert '--trivy "${trivy_path}"' in candidate
    assert "dbf75864e7a7ff9e1fbf00552c31483f693188632a9f68f343cd7653dac513d6" in candidate
    assert "93f9da8e4ba5e0c1c76d8234ed2494cf9afb0a96fd21953e424bb795f3299b8e" in candidate
    assert "channelwatch-runtime-scan-${tag}.tar" in candidate
    assert "Rebuild and compare the complete application payload" in candidate
    assert "--signing-key-env CHANNELWATCH_COMPARISON_SIGNING_KEY" in candidate
    assert candidate.count("build-runtime-bundle.py") == 1
    assert '"dist/candidate-one/channelwatch-runtime-${tag}-${architecture}.zip"' in candidate
    assert '"${comparison_dir}/channelwatch-app-${tag}.zip"' in candidate
    assert (
        'if [ "${updater_protocol}" = "4" ] && '
        '[ "${runtime_generation_required}" = "true" ]; then'
    ) in candidate
    assert '--runtime-capability-evidence "${capability_evidence}"' in candidate
    assert "channelwatch-update-catalog-v3.json" in candidate
    assert "Encrypt qualified candidate for the final sealer" in candidate
    assert "Open qualification and create the final production seal" in final_sealer

    assert "Open the exact qualified candidate before release checks" in release_job
    assert 'sha256sum --check SHA256SUMS.txt' in release_job
    assert 'cp "${approved_dir}/channelwatch-update-catalog-v3.json"' in release_job
    assert 'cp "${approved_dir}/channelwatch-runtime-capability-${TAG}.json"' in release_job
    assert '--runtime-capability-evidence "dist/update/channelwatch-runtime-capability-${TAG}.json"' in release_job
    assert "channelwatch-runtime-${TAG}-amd64.zip" in release_job
    assert "channelwatch-runtime-${TAG}-arm64.zip" in release_job
    assert "channelwatch-runtime-scan-${TAG}.tar" in release_job
    assert "runtime_catalog_url:" in release_job
    assert "runtime_catalog_sha256:" in release_job

    assert 'expected_checksum_count=$((expected_checksum_count + 1))' in public_job
    assert 'expected_checksum_count=$((expected_checksum_count + 3))' in public_job
    assert 18 + 1 + 3 == 22
    assert "channelwatch-update-catalog-v3.json" in public_job
    assert "--runtime-catalog" in public_job
    assert "--expected-runtime-source-commit" in public_job
    assert "runtime_update_catalog_url: $runtime_update_catalog_url" in sync_job
    assert 'updates/v2/stable.json' in live_job
    assert 'updates/v3/stable.json' in live_job
    assert "Verify live runtime update catalog" in live_job
    assert "--runtime-catalog" in live_job


def test_trivy_root_entrypoint_exception_is_narrow_and_expires():
    ignore = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text(encoding="utf-8"))

    assert set(ignore) == {"misconfigurations"}
    assert ignore["misconfigurations"] == [
        {
            "id": "AVD-DS-0002",
            "paths": ["deploy/docker/Dockerfile"],
            "expired_at": date(2027, 2, 1),
            "statement": (
                "The entrypoint must start as root only to repair configurable "
                "/config ownership, then it clears supplemental groups and verifies "
                "the requested effective UID and GID before starting ChannelWatch. "
                "Container integration tests require the final process identity to "
                "be non-root and the root filesystem to be read-only."
            ),
        }
    ]


def test_release_workflow_does_not_interpolate_step_outputs_in_shell():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert 'version="${{ steps.version.outputs.version }}"' not in workflow
    live_step = workflow.split(
        "      - name: Verify live stable update manifest", 1
    )[1]
    assert '--version "${{ steps.release.outputs.version }}"' not in live_step
    assert "RAW_RELEASE_VERSION: ${{ steps.release.outputs.version }}" in live_step


def test_release_candidate_rejects_stale_and_divergent_tags():
    module = _load_script(
        "verify_release_candidate",
        "scripts/release/verify-release-candidate.py",
    )

    assert module.validate_candidate("v0.9.17", ["v0.9.15", "v0.9.17"]) == "v0.9.17"
    try:
        module.validate_candidate("v0.9.15", ["v0.9.15", "v0.9.17"])
    except ValueError as exc:
        assert "newer release tag v0.9.17 already exists" in str(exc)
    else:
        raise AssertionError("stale release candidate was accepted")

    try:
        module.validate_candidate(
            "v0.9.17",
            ["v0.9.15", "v0.9.17"],
            candidate_sha="divergent",
            main_sha="current-main",
        )
    except ValueError as exc:
        assert "is not current main" in str(exc)
    else:
        raise AssertionError("divergent release candidate was accepted")


def test_release_candidate_requires_one_exact_subject_commit_and_lightweight_tag():
    module = _load_script(
        "verify_release_candidate_single_commit",
        "scripts/release/verify-release-candidate.py",
    )

    module.validate_single_commit_release(
        "v0.9.18",
        "v0.9.17",
        commit_count=1,
        commit_message="v0.9.18",
        tag_object_type="commit",
    )

    invalid = (
        {"commit_count": 2},
        {"commit_message": "v0.9.18\n\nextra"},
        {"commit_message": "release v0.9.18"},
        {"tag_object_type": "tag"},
    )
    defaults = {
        "commit_count": 1,
        "commit_message": "v0.9.18",
        "tag_object_type": "commit",
    }
    for changes in invalid:
        with pytest.raises(ValueError):
            module.validate_single_commit_release(
                "v0.9.18",
                "v0.9.17",
                **{**defaults, **changes},
            )


def _signed_update_assets(
    version: str = "0.9.17",
    *,
    image_required: bool = False,
    git_sha: str | None = TEST_RELEASE_GIT_SHA,
    bundle_type: str | None = "channelwatch-app",
    member_contents: dict[str, bytes] | None = None,
    metadata_overrides: dict[str, object] | None = None,
    payload_overrides: dict[str, object] | None = None,
):
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    bundle_io = io.BytesIO()
    metadata = {
        "version": version,
        "version_tag": f"v{version}",
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "created_at": "2026-08-22T00:00:00Z",
    }
    if git_sha is not None:
        metadata["git_sha"] = git_sha
    if bundle_type is not None:
        metadata["bundle_type"] = bundle_type
    metadata.update(metadata_overrides or {})
    members = {
        "core/main.py": b"",
        "ui/backend/main.py": b"",
        **(member_contents or {}),
    }
    with zipfile.ZipFile(bundle_io, "w") as bundle:
        bundle.writestr(
            "channelwatch-bundle.json",
            json.dumps(metadata),
        )
        for member, content in members.items():
            bundle.writestr(member, content)
    bundle_bytes = bundle_io.getvalue()
    digest = hashlib.sha256(bundle_bytes).digest()
    key_id = "test-key"
    payload = {
        "version": version,
        "version_tag": f"v{version}",
        "channel": "stable",
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "image_required": image_required,
        "release_url": (
            f"https://github.com/CoderLuii/ChannelWatch/releases/tag/v{version}"
        ),
        "bundle_url": (
            "https://github.com/CoderLuii/ChannelWatch/releases/download/"
            f"v{version}/channelwatch-app-v{version}.zip"
        ),
        "bundle_sha256": digest.hex(),
        "bundle_signature": base64.b64encode(private.sign(digest)).decode("ascii"),
        "key_id": key_id,
        "highlights": [],
    }
    payload.update(payload_overrides or {})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    manifest = {
        "schema": 1,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": key_id,
            "value": base64.b64encode(private.sign(canonical)).decode("ascii"),
        },
    }
    return json.dumps(manifest).encode(), bundle_bytes, {key_id: public}


def test_update_asset_verifier_uses_runtime_signature_and_archive_validation():
    module = _load_script(
        "verify_update_assets",
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets()

    verified = module.verify_update_assets(
        manifest_bytes,
        bundle_bytes,
        public_keys=public_keys,
        expected_version="0.9.17",
        expected_image_required=False,
        expected_git_sha=TEST_RELEASE_GIT_SHA,
        expected_release_url=TEST_RELEASE_URL,
        expected_bundle_url=TEST_BUNDLE_URL,
    )

    assert verified["payload"]["version"] == "0.9.17"


def test_update_asset_verifier_binds_image_requirement_and_exact_git_sha():
    module = _load_script(
        "verify_update_assets_release_contract",
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets()

    with pytest.raises(Exception, match="image_required does not match"):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=True,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    missing_image_manifest, missing_image_bundle, missing_image_keys = (
        _signed_update_assets(payload_overrides={"image_required": None})
    )
    with pytest.raises(Exception, match="image_required must be an explicit boolean"):
        module.verify_update_assets(
            missing_image_manifest,
            missing_image_bundle,
            public_keys=missing_image_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    with pytest.raises(Exception, match="git_sha does not match"):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha="f" * 40,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    missing_sha_manifest, missing_sha_bundle, missing_sha_keys = (
        _signed_update_assets(git_sha=None)
    )
    with pytest.raises(Exception, match="git_sha does not match"):
        module.verify_update_assets(
            missing_sha_manifest,
            missing_sha_bundle,
            public_keys=missing_sha_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    missing_type_manifest, missing_type_bundle, missing_type_keys = (
        _signed_update_assets(bundle_type=None)
    )
    with pytest.raises(Exception, match="bundle_type does not match"):
        module.verify_update_assets(
            missing_type_manifest,
            missing_type_bundle,
            public_keys=missing_type_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    wrong_type_manifest, wrong_type_bundle, wrong_type_keys = _signed_update_assets(
        bundle_type="not-channelwatch"
    )
    with pytest.raises(Exception, match="bundle_type does not match"):
        module.verify_update_assets(
            wrong_type_manifest,
            wrong_type_bundle,
            public_keys=wrong_type_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    wrong_metadata_tag_manifest, wrong_metadata_tag_bundle, wrong_metadata_tag_keys = (
        _signed_update_assets(metadata_overrides={"version_tag": "v0.9.99"})
    )
    with pytest.raises(Exception, match="metadata version_tag does not match"):
        module.verify_update_assets(
            wrong_metadata_tag_manifest,
            wrong_metadata_tag_bundle,
            public_keys=wrong_metadata_tag_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


@pytest.mark.parametrize(
    "metadata_overrides,payload_overrides,error_fragment",
    [
        ({}, {"channel": None}, "channel must be a string"),
        ({}, {"channel": "beta"}, "channel does not match"),
        (
            {"runtime_abi": None},
            {"runtime_abi": None},
            "runtime_abi must be a string",
        ),
        (
            {"runtime_abi": "channelwatch-runtime-v999"},
            {"runtime_abi": "channelwatch-runtime-v999"},
            "runtime_abi does not match",
        ),
        (
            {"settings_schema_version": None},
            {"settings_schema_version": None},
            "settings_schema_version must be an integer",
        ),
        (
            {"settings_schema_version": 999},
            {"settings_schema_version": 999},
            "settings_schema_version does not match",
        ),
        (
            {"settings_schema_version": "7"},
            {"settings_schema_version": "7"},
            "settings_schema_version must be an integer",
        ),
    ],
)
def test_update_asset_verifier_binds_canonical_runtime_contract(
    metadata_overrides, payload_overrides, error_fragment
):
    module = _load_script(
        "verify_update_assets_runtime_contract_" + error_fragment.split()[0],
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        metadata_overrides=metadata_overrides,
        payload_overrides=payload_overrides,
    )

    with pytest.raises(Exception, match=error_fragment):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_channel="stable",
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


@pytest.mark.parametrize(
    "metadata_overrides,error_fragment",
    [
        ({"runtime_abi": None}, "runtime ABI is not compatible"),
        ({"runtime_abi": 1}, "runtime ABI is not compatible"),
        (
            {"settings_schema_version": None},
            "schema version does not match",
        ),
        (
            {"settings_schema_version": "7"},
            "metadata settings_schema_version must be an integer",
        ),
    ],
)
def test_update_asset_verifier_rejects_wrong_bundle_metadata_types(
    metadata_overrides, error_fragment
):
    module = _load_script(
        "verify_update_assets_bundle_metadata_types_" + error_fragment.split()[0],
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        metadata_overrides=metadata_overrides,
    )

    with pytest.raises(Exception, match=error_fragment):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_channel="stable",
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


@pytest.mark.parametrize(
    "payload_overrides,error_fragment",
    [
        ({"version_tag": None}, "version_tag does not match"),
        ({"version_tag": "v0.9.99"}, "version_tag does not match"),
        ({"release_url": None}, "release_url does not match"),
        (
            {"release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.15"},
            "release_url does not match",
        ),
        ({"bundle_url": None}, "bundle_url does not match"),
        (
            {
                "bundle_url": (
                    "https://github.com/CoderLuii/ChannelWatch/releases/download/"
                    "v0.9.15/channelwatch-app-v0.9.15.zip"
                )
            },
            "bundle_url does not match",
        ),
    ],
)
def test_update_asset_verifier_rejects_wrong_tag_and_urls(
    payload_overrides, error_fragment
):
    module = _load_script(
        "verify_update_assets_manifest_contract_" + error_fragment.split()[0],
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        payload_overrides=payload_overrides
    )

    with pytest.raises(Exception, match=error_fragment):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


def test_update_asset_verifier_matches_critical_members_to_exact_source(tmp_path):
    module = _load_script(
        "verify_update_assets_exact_source",
        "scripts/release/verify-update-assets.py",
    )
    member_contents = {
        member: f"exact bytes for {member}\n".encode()
        for member in module.CRITICAL_SOURCE_MEMBERS
    }
    for member, relative_source in module.CRITICAL_SOURCE_MEMBERS.items():
        source_path = tmp_path / relative_source
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(member_contents[member])
    git_env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        git_env.pop(name, None)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, env=git_env, check=True)
    subprocess.run(
        ["git", "config", "user.email", "review@example.invalid"],
        cwd=tmp_path,
        env=git_env,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Review"],
        cwd=tmp_path,
        env=git_env,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, env=git_env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "exact source"], cwd=tmp_path, env=git_env, check=True
    )
    exact_git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        env=git_env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        member_contents=member_contents,
        git_sha=exact_git_sha,
    )

    module.verify_update_assets(
        manifest_bytes,
        bundle_bytes,
        public_keys=public_keys,
        expected_version="0.9.17",
        expected_channel="stable",
        expected_runtime_abi="channelwatch-runtime-v1",
        expected_settings_schema_version=7,
        expected_image_required=False,
        expected_git_sha=exact_git_sha,
        expected_release_url=TEST_RELEASE_URL,
        expected_bundle_url=TEST_BUNDLE_URL,
        expected_source_root=tmp_path,
    )

    for metadata_overrides, payload_overrides, error_fragment in (
        (
            {"runtime_abi": "channelwatch-runtime-v999"},
            {"runtime_abi": "channelwatch-runtime-v999"},
            "runtime_abi does not match",
        ),
        (
            {"settings_schema_version": 999},
            {"settings_schema_version": 999},
            "settings_schema_version does not match",
        ),
    ):
        wrong_manifest, wrong_bundle, wrong_keys = _signed_update_assets(
            member_contents=member_contents,
            git_sha=exact_git_sha,
            metadata_overrides=metadata_overrides,
            payload_overrides=payload_overrides,
        )
        with pytest.raises(Exception, match=error_fragment):
            module.verify_update_assets(
                wrong_manifest,
                wrong_bundle,
                public_keys=wrong_keys,
                expected_version="0.9.17",
                expected_channel="stable",
                expected_runtime_abi="channelwatch-runtime-v1",
                expected_settings_schema_version=7,
                expected_image_required=False,
                expected_git_sha=exact_git_sha,
                expected_release_url=TEST_RELEASE_URL,
                expected_bundle_url=TEST_BUNDLE_URL,
                expected_source_root=tmp_path,
            )

    changed_source = tmp_path / module.CRITICAL_SOURCE_MEMBERS["core/update_center.py"]
    changed_source.write_bytes(b"changed after packaging\n")
    with pytest.raises(Exception, match="does not match exact Git SHA"):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=exact_git_sha,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
            expected_source_root=tmp_path,
        )

    changed_source.write_bytes(member_contents["core/update_center.py"])
    mismatched_members = {
        **member_contents,
        "core/update_center.py": b"bundle bytes from a different source\n",
    }
    mismatched_manifest, mismatched_bundle, mismatched_keys = _signed_update_assets(
        member_contents=mismatched_members,
        git_sha=exact_git_sha,
    )
    with pytest.raises(Exception, match="does not match exact release source"):
        module.verify_update_assets(
            mismatched_manifest,
            mismatched_bundle,
            public_keys=mismatched_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=exact_git_sha,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
            expected_source_root=tmp_path,
        )


def test_update_asset_verifier_rejects_tampered_bundle_and_empty_trust_store():
    module = _load_script(
        "verify_update_assets_tamper",
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets()

    try:
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes + b"tampered",
            public_keys=public_keys,
            expected_version="0.9.17",
        )
    except Exception as exc:
        assert "hash did not match" in str(exc)
    else:
        raise AssertionError("tampered update bundle was accepted")

    try:
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys={},
            expected_version="0.9.17",
        )
    except Exception as exc:
        assert "Unknown update signing key" in str(exc)
    else:
        raise AssertionError("an empty trust store unexpectedly used production keys")


@pytest.mark.parametrize("version", ["1.1.0", "1.1.1"])
def test_update_asset_verifier_checks_generated_legal_identity_and_body(tmp_path, version):
    module = _load_script("verify_generated_legal_" + version, "scripts/release/verify-update-assets.py")
    members = {}
    for member, relative in {**module.CRITICAL_SOURCE_MEMBERS, **module.GENERATED_LEGAL_SOURCE_MEMBERS}.items():
        source = b"# Legal title\n\nExact package and source evidence.\n" if member in module.GENERATED_LEGAL_SOURCE_MEMBERS else f"source {relative}\n".encode()
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        members[member] = module.render_document(source.decode(), version).encode() if member in module.GENERATED_LEGAL_SOURCE_MEMBERS else source
    manifest, bundle, keys = _signed_update_assets(version, member_contents=members)
    module.verify_update_assets(manifest, bundle, public_keys=keys, expected_version=version, expected_source_root=tmp_path)
    for replacement in (b"# ChannelWatch v0.9.18 - Legal title\n\nExact package and source evidence.\n", b"# ChannelWatch v" + version.encode() + b" - Legal title\n\nAltered evidence.\n"):
        altered = dict(members)
        altered["core/release_legal/THIRD_PARTY_LICENSES.md"] = replacement
        manifest, bundle, keys = _signed_update_assets(version, member_contents=altered)
        with pytest.raises(Exception, match="does not match exact release source"):
            module.verify_update_assets(manifest, bundle, public_keys=keys, expected_version=version, expected_source_root=tmp_path)


def test_catalog_predecessor_allows_only_verified_unpublished_ancestor(tmp_path):
    module = _load_script(
        "build_update_bundle_unpublished_predecessor",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ChannelWatch Test",
                "-c",
                "user.email=channelwatch-test@example.invalid",
                *args,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--initial-branch=main")
    tracked = repo / "tracked.txt"
    for version in ("1.4.1", "1.4.2", "1.4.3"):
        tracked.write_text(version, encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", version)
        if version != "1.4.3":
            git("tag", f"v{version}")

    requests = []

    def release_not_found(request, *, timeout):
        requests.append((request, timeout))
        if "/releases/tags/" in request.full_url:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"message":"Not Found"}'),
            )
        return SimpleNamespace(
            status=200,
            read=lambda _limit: b"[]",
            close=lambda: None,
        )

    module.require_previous_catalog_release(
        "1.4.3",
        [{"version": "1.4.1"}],
        ["v1.4.1", "v1.4.2"],
        unpublished_predecessor_tags=["v1.4.2"],
        repository="CoderLuii/ChannelWatch",
        github_token="authenticated-test-token",
        repo_root=repo,
        urlopen=release_not_found,
    )

    assert len(requests) == 2
    tag_request, tag_timeout = requests[0]
    assert tag_request.full_url.endswith(
        "/repos/CoderLuii/ChannelWatch/releases/tags/v1.4.2"
    )
    list_request, list_timeout = requests[1]
    assert list_request.full_url.endswith(
        "/repos/CoderLuii/ChannelWatch/releases?per_page=100"
    )
    for request in (tag_request, list_request):
        assert request.get_header("Authorization") == "Bearer authenticated-test-token"
    assert tag_timeout == list_timeout == 15


def test_catalog_predecessor_unpublished_exception_rejects_draft_release(tmp_path):
    module = _load_script(
        "build_update_bundle_unpublished_draft_predecessor",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ChannelWatch Test",
                "-c",
                "user.email=channelwatch-test@example.invalid",
                *args,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--initial-branch=main")
    tracked = repo / "tracked.txt"
    for version in ("1.4.1", "1.4.2", "1.4.3"):
        tracked.write_text(version, encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", version)
        if version != "1.4.3":
            git("tag", f"v{version}")

    def release_lookup(request, *, timeout):
        assert timeout == 15
        if "/releases/tags/" in request.full_url:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"message":"Not Found"}'),
            )
        return SimpleNamespace(
            status=200,
            read=lambda _limit: json.dumps(
                [{"tag_name": "v1.4.2", "draft": True}]
            ).encode("utf-8"),
            close=lambda: None,
        )

    with pytest.raises(ValueError, match="because a release exists"):
        module.require_previous_catalog_release(
            "1.4.3",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2"],
            unpublished_predecessor_tags=["v1.4.2"],
            repository="CoderLuii/ChannelWatch",
            github_token="authenticated-test-token",
            repo_root=repo,
            urlopen=release_lookup,
        )


@pytest.mark.parametrize("malformed_release", [None, {}, {"tag_name": ""}])
def test_catalog_predecessor_unpublished_exception_rejects_malformed_release_list(
    tmp_path, malformed_release
):
    module = _load_script(
        "build_update_bundle_unpublished_malformed_release_list",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ChannelWatch Test",
                "-c",
                "user.email=channelwatch-test@example.invalid",
                *args,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--initial-branch=main")
    tracked = repo / "tracked.txt"
    for version in ("1.4.1", "1.4.2", "1.4.3"):
        tracked.write_text(version, encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", version)
        if version != "1.4.3":
            git("tag", f"v{version}")

    def release_lookup(request, *, timeout):
        assert timeout == 15
        if "/releases/tags/" in request.full_url:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"message":"Not Found"}'),
            )
        return SimpleNamespace(
            status=200,
            read=lambda _limit: json.dumps([malformed_release]).encode("utf-8"),
            close=lambda: None,
        )

    with pytest.raises(ValueError, match="release list was malformed"):
        module.require_previous_catalog_release(
            "1.4.3",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2"],
            unpublished_predecessor_tags=["v1.4.2"],
            repository="CoderLuii/ChannelWatch",
            github_token="authenticated-test-token",
            repo_root=repo,
            urlopen=release_lookup,
        )


@pytest.mark.parametrize("status", [200, 403])
def test_catalog_predecessor_unpublished_exception_fails_closed(
    tmp_path, status
):
    module = _load_script(
        f"build_update_bundle_unpublished_predecessor_{status}",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("1.4.2", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ChannelWatch Test",
            "-c",
            "user.email=channelwatch-test@example.invalid",
            "commit",
            "-m",
            "1.4.2",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "tag", "v1.4.2"], cwd=repo, check=True)

    def release_response(request, *, timeout):
        if status == 403:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "Forbidden",
                {},
                io.BytesIO(b'{"message":"Forbidden"}'),
            )
        return SimpleNamespace(status=status, close=lambda: None)

    with pytest.raises(ValueError, match="GitHub release absence"):
        module.require_previous_catalog_release(
            "1.4.3",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2"],
            unpublished_predecessor_tags=["v1.4.2"],
            repository="CoderLuii/ChannelWatch",
            github_token="authenticated-test-token",
            repo_root=repo,
            urlopen=release_response,
        )


def test_catalog_predecessor_unpublished_exception_rejects_network_failure(
    tmp_path,
):
    module = _load_script(
        "build_update_bundle_unpublished_network_failure",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("1.4.2", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ChannelWatch Test",
            "-c",
            "user.email=channelwatch-test@example.invalid",
            "commit",
            "-m",
            "1.4.2",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "tag", "v1.4.2"], cwd=repo, check=True)

    with pytest.raises(ValueError, match="GitHub release absence"):
        module.require_previous_catalog_release(
            "1.4.3",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2"],
            unpublished_predecessor_tags=["v1.4.2"],
            repository="CoderLuii/ChannelWatch",
            github_token="authenticated-test-token",
            repo_root=repo,
            urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                urllib.error.URLError("network unavailable")
            ),
        )


def test_catalog_predecessor_exception_requires_tag_ancestor(tmp_path):
    module = _load_script(
        "build_update_bundle_unpublished_nonancestor",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    commit = [
        "git",
        "-c",
        "user.name=ChannelWatch Test",
        "-c",
        "user.email=channelwatch-test@example.invalid",
        "commit",
        "-m",
        "commit",
    ]
    subprocess.run(commit, cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "switch", "-c", "unpublished"], cwd=repo, check=True)
    tracked.write_text("unpublished", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(commit, cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "tag", "v1.4.2"], cwd=repo, check=True)
    subprocess.run(["git", "switch", "main"], cwd=repo, check=True)

    with pytest.raises(ValueError, match="ancestor"):
        module.require_previous_catalog_release(
            "1.4.3",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2"],
            unpublished_predecessor_tags=["v1.4.2"],
            repository="CoderLuii/ChannelWatch",
            github_token="authenticated-test-token",
            repo_root=repo,
            urlopen=lambda *_args, **_kwargs: pytest.fail(
                "GitHub must not be queried for a non-ancestor tag"
            ),
        )


def test_catalog_predecessor_allows_verified_contiguous_unpublished_tags(tmp_path):
    module = _load_script(
        "build_update_bundle_contiguous_unpublished_predecessors",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ChannelWatch Test",
                "-c",
                "user.email=channelwatch-test@example.invalid",
                *args,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--initial-branch=main")
    tracked = repo / "tracked.txt"
    for version in ("1.4.1", "1.4.2", "1.4.3", "1.4.4"):
        tracked.write_text(version, encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", version)
        if version != "1.4.4":
            git("tag", f"v{version}")

    requests = []

    def release_not_found(request, *, timeout):
        requests.append((request, timeout))
        if "/releases/tags/" in request.full_url:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"message":"Not Found"}'),
            )
        return SimpleNamespace(
            status=200,
            read=lambda _limit: b'[{"tag_name":"v1.4.1"}]',
            close=lambda: None,
        )

    module.require_previous_catalog_release(
        "1.4.4",
        [{"version": "1.4.1"}],
        ["v1.4.1", "v1.4.2", "v1.4.3"],
        unpublished_predecessor_tags=["v1.4.3", "v1.4.2"],
        repository="CoderLuii/ChannelWatch",
        github_token="authenticated-test-token",
        repo_root=repo,
        urlopen=release_not_found,
    )

    assert [
        request.full_url.rsplit("/", 1)[-1]
        for request, _timeout in requests
        if "/releases/tags/" in request.full_url
    ] == ["v1.4.3", "v1.4.2"]
    assert len(requests) == 4
    assert all(timeout == 15 for _request, timeout in requests)
    assert all(
        request.get_header("Authorization") == "Bearer authenticated-test-token"
        for request, _timeout in requests
    )


def test_catalog_predecessor_rejects_noncontiguous_or_unretained_history(tmp_path):
    module = _load_script(
        "build_update_bundle_noncontiguous_unpublished_predecessors",
        "scripts/release/build-update-bundle.py",
    )
    failure_urlopen = lambda *_args, **_kwargs: pytest.fail(
        "Invalid exception lists must fail before GitHub is queried."
    )

    with pytest.raises(ValueError, match="retain the previous release 1.4.3"):
        module.require_previous_catalog_release(
            "1.4.4",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2", "v1.4.3"],
            unpublished_predecessor_tags=["v1.4.3", "v1.4.1"],
            urlopen=failure_urlopen,
        )
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ChannelWatch Test",
                "-c",
                "user.email=channelwatch-test@example.invalid",
                *args,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--initial-branch=main")
    tracked = repo / "tracked.txt"
    for version in ("1.4.1", "1.4.2", "1.4.3", "1.4.4"):
        tracked.write_text(version, encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", version)
        if version != "1.4.4":
            git("tag", f"v{version}")

    def release_not_found(request, *, timeout):
        assert timeout == 15
        if "/releases/tags/" in request.full_url:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"message":"Not Found"}'),
            )
        return SimpleNamespace(
            status=200,
            read=lambda _limit: b'[{"tag_name":"v1.4.1"}]',
            close=lambda: None,
        )

    with pytest.raises(ValueError, match="nearest published predecessor 1.4.1"):
        module.require_previous_catalog_release(
            "1.4.4",
            [],
            ["v1.4.1", "v1.4.2", "v1.4.3"],
            unpublished_predecessor_tags=["v1.4.3", "v1.4.2"],
            repository="CoderLuii/ChannelWatch",
            github_token="authenticated-test-token",
            repo_root=repo,
            urlopen=release_not_found,
        )


def test_catalog_predecessor_accepts_retained_144_for_150_with_durable_exclusions():
    module = _load_script(
        "build_update_bundle_retained_144_predecessor",
        "scripts/release/build-update-bundle.py",
    )

    module.require_previous_catalog_release(
        "1.5.0",
        [{"version": "1.4.4"}, {"version": "1.4.1"}],
        ["v1.4.1", "v1.4.2", "v1.4.3", "v1.4.4"],
    )


def test_catalog_predecessor_rejects_missing_144_for_150():
    module = _load_script(
        "build_update_bundle_missing_144_predecessor",
        "scripts/release/build-update-bundle.py",
    )

    with pytest.raises(ValueError, match="retain the previous release 1.4.4"):
        module.require_previous_catalog_release(
            "1.5.0",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2", "v1.4.3", "v1.4.4"],
        )


def test_catalog_predecessor_cannot_use_failed_143_142_exceptions_for_150():
    module = _load_script(
        "build_update_bundle_wrong_143_142_predecessor_exceptions",
        "scripts/release/build-update-bundle.py",
    )

    with pytest.raises(ValueError, match="retain the previous release 1.4.4"):
        module.require_previous_catalog_release(
            "1.5.0",
            [{"version": "1.4.1"}],
            ["v1.4.1", "v1.4.2", "v1.4.3", "v1.4.4"],
            unpublished_predecessor_tags=["v1.4.3", "v1.4.2"],
        )


def test_catalog_predecessor_accepts_151_then_150_as_failed_predecessors_for_152(
    tmp_path,
):
    module = _load_script(
        "build_update_bundle_152_recovery_predecessors",
        "scripts/release/build-update-bundle.py",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ChannelWatch Test",
                "-c",
                "user.email=channelwatch-test@example.invalid",
                *args,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--initial-branch=main")
    tracked = repo / "tracked.txt"
    for version in ("1.4.5", "1.5.0", "1.5.1", "1.5.2"):
        tracked.write_text(version, encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", version)
        if version != "1.5.2":
            git("tag", f"v{version}")

    requests = []

    def release_not_found(request, *, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith(("/releases/tags/v1.5.1", "/releases/tags/v1.5.0")):
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"message":"Not Found"}'),
            )
        return SimpleNamespace(
            status=200,
            read=lambda _limit: b'[{"tag_name":"v1.4.5"}]',
            close=lambda: None,
        )

    module.require_previous_catalog_release(
        "1.5.2",
        [{"version": "1.4.5"}],
        ["v1.4.5", "v1.5.0", "v1.5.1"],
        unpublished_predecessor_tags=["v1.5.1", "v1.5.0"],
        repository="CoderLuii/ChannelWatch",
        github_token="authenticated-test-token",
        repo_root=repo,
        urlopen=release_not_found,
    )

    assert [
        request.full_url.rsplit("/", 1)[-1]
        for request, _timeout in requests
        if "/releases/tags/" in request.full_url
    ] == ["v1.5.1", "v1.5.0"]


def test_current_metadata_keeps_failed_142_through_151_excluded(
    monkeypatch,
):
    module = _load_script(
        "export_release_metadata_future_history",
        "scripts/release/export-site-release-metadata.py",
    )
    changelog = """\
## [1.5.2] - 2026-10-20

### Added
- Image update.

## [1.5.1] - 2026-10-11

### Important
- Publishing failed.

## [1.5.0] - 2026-10-11

### Important
- Publishing failed.

## [1.4.5] - 2026-10-11

### Fixed
- App update.

## [1.4.4] - 2026-10-11

### Important
- Publishing failed.

## [1.4.3] - 2026-10-11

### Important
- Publishing failed.

## [1.4.2] - 2026-10-10

### Important
- Publishing failed.

## [1.4.1] - 2026-10-10

### Fixed
- Earlier app update.

## [0.9.17] - 2026-01-01

### Fixed
- Pre-catalog release.
"""
    files = {
        "docs/releases/CHANGELOG.md": changelog,
        "scripts/release/release-config.json": json.dumps(
            {
                "unpublished_release_tags": [
                    "v1.4.2",
                    "v1.4.3",
                    "v1.4.4",
                    "v1.5.0",
                    "v1.5.1",
                ]
            }
        ),
    }
    monkeypatch.setattr(
        module,
        "read_text",
        lambda relative, tree_ref=None: files[relative],
    )

    parsed = module.parse_changelog("1.5.2")

    assert [release["version"] for release in parsed["previousReleases"]] == [
        "1.4.5",
        "1.4.1",
        "0.9.17",
    ]


def test_candidate_signer_checkout_fetches_complete_release_history():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
            encoding="utf-8"
        )
    )
    checkout = next(
        step
        for step in workflow["jobs"]["candidate-signing"]["steps"]
        if step.get("name") == "Checkout exact audited signer"
    )

    assert checkout["with"] == {
        "ref": "${{ github.sha }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    signing_step = next(
        step
        for step in workflow["jobs"]["candidate-signing"]["steps"]
        if step.get("name") == "Sign, verify, and seal immutable candidate inputs"
    )
    assert signing_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    comparison_step = next(
        step
        for step in workflow["jobs"]["candidate-qualification"]["steps"]
        if step.get("name") == "Rebuild and compare the complete application payload"
    )
    assert comparison_step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert workflow["jobs"]["candidate-qualification"]["permissions"] == {
        "contents": "read"
    }


def test_dependency_docs_exclude_failed_142_through_144_from_published_images():
    update_model = (ROOT / "docs/explanation/update-model.md").read_text(
        encoding="utf-8"
    )
    update_how_to = (ROOT / "docs/how-to/update-channelwatch.md").read_text(
        encoding="utf-8"
    )
    changelog = (ROOT / "docs/releases/CHANGELOG.md").read_text(encoding="utf-8")

    for false_image_list in (
        "v1.4.0, v1.4.1, and v1.4.2 images",
        "v1.4.0, v1.4.1, and v1.4.3 images",
        "v1.4.0, v1.4.1, and v1.4.4 images",
    ):
        assert false_image_list not in update_model
        assert false_image_list not in update_how_to
    assert "Published v1.4.x images" in update_model
    assert "Published\nv1.4.x images" in update_how_to
    for failed_version in ("v1.4.2", "v1.4.3"):
        assert (
            f"Publishing failed before any {failed_version} app bundle, catalog, "
            "container image, or GitHub release was published."
        ) in changelog
    assert "## [1.4.4] - 2026-10-11" in changelog
    assert (
        "Publishing failed during OpenVEX validation before any v1.4.4 "
        "app bundle, catalog, container image, or GitHub release was published."
    ) in changelog
    assert "## [1.4.5] - 2026-10-11" in changelog


def test_release_policy_requires_current_verified_versioned_openvex():
    result = subprocess.run(
        [sys.executable, "scripts/release/release_version_policy.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_release_policy_rejects_missing_current_openvex(tmp_path: Path):
    module = _load_script(
        "release_version_policy_missing_vex",
        "scripts/release/release_version_policy.py",
    )
    source = tmp_path / "app/core/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text('__version__ = "1.5.0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Release VEX document is missing"):
        module.verify_release_source(tmp_path, "1.5.0")
