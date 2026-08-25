import base64
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
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

    module.copy_release_legal_files(tmp_path)

    legal_root = tmp_path / "core" / "release_legal"
    assert (legal_root / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert (legal_root / "NOTICE").read_bytes() == (
        ROOT / "docs/legal/NOTICE"
    ).read_bytes()
    assert (legal_root / "THIRD_PARTY_LICENSES.md").read_bytes() == (
        ROOT / "docs/legal/THIRD_PARTY_LICENSES.md"
    ).read_bytes()


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
        module.load_catalog_history("0.9.19", path=history)
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
        "LGPL-2.1-only.txt",
        "GCC-exception-3.1.txt",
    }
    assert all(
        len(artifact.sha256) == 64
        and artifact.url.startswith(
            "https://raw.githubusercontent.com/spdx/license-list-data/"
            f"{module.SPDX_LICENSE_LIST_COMMIT}/text/"
        )
        for artifact in module.COPYLEFT_LICENSES
    )


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


def test_corresponding_source_map_pins_exact_release_sources():
    source_map = (ROOT / "docs/legal/CORRESPONDING_SOURCE.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "8190a1652f4534ad3feebd3b48066514f0f4375f",
        "gdbm 1.26-r5",
        "glibc-2.43 2.43-r15",
        "libgcc 16.2.0-r0",
        "libuuid 2.42.2-r3",
        "libzstd1 1.5.7-r8",
        "readline 8.3-r2",
        "xz 5.8.3-r2",
        "zeroconf 0.150.0",
        "a5fe7feab1de6ef5e541e0a3d07e534fd91629b813fc27281593584100f63164",
        "5bf6d9610255540bfbee6890765a616042bf1e11",
    ):
        assert required in source_map


def test_release_config_declares_0918_legacy_update_bridge():
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )

    assert config == {
        "version": "0.9.18",
        "image_required": False,
        "delivery_mode": "app_update_with_image_refresh",
        "minimum_image_version": "0.9.11",
        "updater_protocol": 2,
        "recommended_image_version": "0.9.18",
        "publication_time": "2026-08-25T17:10:00Z",
        "automatic_install_after": "2026-09-03T12:00:00Z",
        "compatible_source_application_versions": ["0.9.18"],
        "compatible_launcher_protocols": [1, 2, 3],
        "runtime_compatibility_evidence": {
            **{
                path: {
                    "kind": "legacy_update_bridge_v1",
                    "candidate_version": "0.9.18",
                    "source_tags": [f"v0.9.{patch}" for patch in range(11, 18)],
                    "minimum_image_version": "0.9.11",
                    "expected_activations": {
                        **{
                            f"v0.9.{patch}": "protocol_1_adoption"
                            for patch in range(11, 16)
                        },
                        "v0.9.16": "protocol_2_quorum",
                        "v0.9.17": "protocol_2_quorum",
                    },
                    "image_pull_only_sources": {
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
                    },
                }
                for path in (
                    "app/core/docker-entrypoint.py",
                    "app/core/runtime_launcher.py",
                    "deploy/docker/Dockerfile",
                )
            }
        },
        "release_heading": (
            "# ChannelWatch v0.9.18 - Simpler setup and automatic updates"
        ),
        "verification_assets": True,
    }
    publication = datetime.fromisoformat(config["publication_time"].replace("Z", "+00:00"))
    automatic = datetime.fromisoformat(
        config["automatic_install_after"].replace("Z", "+00:00")
    )
    assert automatic >= publication + timedelta(hours=24)


def test_release_version_surfaces_accept_multi_digit_patch():
    module = _load_script(
        "export_release_metadata",
        "scripts/release/export-site-release-metadata.py",
    )

    metadata = module.collect_metadata(
        source_ref=None,
        release_url=None,
    )

    assert metadata["version"] == "0.9.18"
    assert metadata["versionTag"] == "v0.9.18"
    assert metadata["dockerTag"] == "0.9.18"
    assert metadata["helmChartVersion"] == "0.9.18"
    assert metadata["helmAppVersion"] == "0.9.18"


def test_release_body_for_0918_links_license_and_sbom_assets(monkeypatch, capsys):
    module = _load_script(
        "render_release_body_0918_legal_assets",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v0.9.18",
        "releaseDate": "2026-08-24",
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
        "dockerTag": "0.9.18",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["render-release-body.py", "--version", "0.9.18"],
    )

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith(
        "# ChannelWatch v0.9.18 - Simpler setup and automatic updates\n"
    )
    assert "## Important" in output
    assert "v0.9.9 needs one image pull while preserving /config." in output
    assert output.index("## Important") < output.index("## Security")
    assert "## License and verification" in output
    assert "channelwatch-v0.9.18-THIRD-PARTY-LICENSES.md" in output
    assert "channelwatch-v0.9.18-CORRESPONDING-SOURCE.md" in output
    assert "channelwatch-v0.9.18-COPYLEFT-LICENSES.zip" in output
    assert "channelwatch-v0.9.18-SHA256SUMS.txt" in output
    assert "Exact amd64 and arm64 SPDX and CycloneDX SBOMs" in output
    assert "every other attached asset is covered" in output
    assert "`coderluii/channelwatch:0.9`" in output
    assert "`ghcr.io/coderluii/channelwatch:0.9`" in output


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
    parse_env = {
        **os.environ,
        "RELEASE_TAG": "v0.9.17",
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
    assert output_values == {"version": "0.9.17"}
    assert "VERSION: ${{ steps.version.outputs.version }}" in verify_block
    assert 'version_re="${VERSION//' in verify_shell
    assert 'version_re="${version//' not in verify_shell

    verified = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", verify_shell],
        cwd=ROOT,
        env={
            **os.environ,
            "RELEASE_TAG": "v0.9.17",
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
        "app/core/update_center.py",
        "app/ui/pnpm-lock.yaml",
    )


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
        json.dumps({"image_required": False, "delivery_mode": "app_update"}),
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
    candidate = (ROOT / ".github/workflows/release-candidate.yml").read_text(
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
        "Seal production-signed candidate for private retention"
    )
    assert "--historical-compatibility-verified" not in workflow + candidate
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


def test_candidate_workflow_uploads_only_sealed_production_assets():
    candidate = (ROOT / ".github/workflows/release-candidate.yml").read_text(
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
    assert "actions/setup-python@e797f83bcb11b83ae66e0230d6156d7c80228e7c" in workflow
    assert "python-version: '3.12'" in workflow
    assert "actions/setup-node@395ad3262231945c25e8478fd5baf05154b1d79f" in workflow
    assert "node-version: '24'" in workflow
    assert "package-manager-cache: false" in workflow

    publish_job = workflow.split("  publish-github-release:", 1)[1].split(
        "\n  verify-public-release-assets:", 1
    )[0]
    assert "missing or already published; published releases are immutable" in publish_job
    assert ".draft == true" in publish_job
    assert "'.target_commitish'" in publish_job


def test_release_workflow_recovery_is_bound_to_one_immutable_reviewed_target():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:",
        1,
    )[0]

    assert "workflow_dispatch:" in workflow
    assert "release_tag:" in workflow
    assert "release_sha:" in workflow
    assert "expected_commit_count:" in workflow
    assert "recovery_confirmation:" in workflow
    assert "RELEASE_TAG: ${{ inputs.release_tag || github.ref_name }}" in workflow
    assert "RELEASE_SHA: ${{ inputs.release_sha || github.sha }}" in workflow
    assert workflow.count("ref: ${{ env.RELEASE_SHA }}") == 7
    assert "if: github.event_name == 'push'" in release_job
    assert "Verify immutable recovery publication target" in release_job
    assert 'expected_confirmation="publish-${RELEASE_TAG}-at-${RELEASE_SHA}"' in release_job
    assert 'tag_commit="$(git rev-parse --verify "${RELEASE_TAG}^{commit}")"' in release_job
    assert 'git merge-base --is-ancestor "${RELEASE_SHA}" "${main_commit}"' in release_job
    assert 'latest_tag="$(git tag --list' in release_job
    assert 'git rev-list --reverse "${previous_tag}..${RELEASE_SHA}"' in release_job
    assert 'if [ "${#release_commits[@]}" -ne "${EXPECTED_COMMIT_COUNT}" ]' in release_job
    assert 'if [ "${subject}" != "${RELEASE_TAG}" ]' in release_job
    assert 'elif [[ ! "${subject}" =~ ^Fix\\ ${RELEASE_TAG}\\  ]]' in release_job
    assert "No successful, unexpired signed candidate artifact exists" in release_job
    assert "head_sha=${RELEASE_SHA}" in release_job


def test_release_workflow_publishes_only_the_scanned_multiarch_archive():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    candidate = (ROOT / ".github/workflows/release-candidate.yml").read_text(
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
    assert candidate.count("docker/build-push-action@") == 3
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
    assert "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514" in image_job
    assert "--scanners vuln,secret,misconfig" in image_job
    assert "--severity CRITICAL,HIGH" in image_job
    assert "--exit-code 1" in image_job
    assert '--input "${OCI_LAYOUT}"' in image_job
    assert '--platform "${platform}"' in image_job

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
        "e22c389904149dbc22b58101806040fa8d37a610"
    ) in image_job
    assert "syft-version: v1.51.0" in image_job
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
    assert "Release checksum manifest must cover all 13 non-checksum assets" in image_job
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
    assert "quay.io/skopeo/stable@sha256:64ac45c5a1c01230896fbae960b2213e32a5040e4009b83b5f5cbf31a35f61c3" in publish_job
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
    candidate = (ROOT / ".github/workflows/release-candidate.yml").read_text(
        encoding="utf-8"
    )
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]

    assert "actions: read" in release_job
    assert "Require byte-identical approved candidate assets" in release_job
    assert "head_sha=${RELEASE_SHA}" in release_job
    assert 'artifact_name="channelwatch-release-candidate-${RELEASE_SHA}"' in release_job
    assert "No successful, unexpired signed candidate artifact exists" in release_job
    assert release_job.count("cmp --silent") == 1
    assert "seal-candidate-artifact.py open" in release_job
    assert '"channelwatch-app-${TAG}.zip"' in release_job
    assert '"channelwatch-update-${TAG}.json"' in release_job
    assert '"channelwatch-catalog-${TAG}.json"' in release_job
    assert 'image_descriptor="channelwatch-image-${TAG}.json"' in release_job
    assert 'image_digest="channelwatch-image-${TAG}.digest"' in release_job
    assert 'image_archive="channelwatch-image-${TAG}.oci.tar"' in release_job
    assert "approved_candidate_artifact_id:" in release_job
    assert "approved_image_index_digest:" in release_job
    assert "approved_image_descriptor_sha256:" in release_job
    assert "approved_image_archive_sha256:" in release_job
    assert "sha256sum --check SHA256SUMS.txt" in release_job
    assert (
        "name: channelwatch-release-candidate-${{ inputs.candidate_sha }}"
        in candidate
    )
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
    candidate = (ROOT / ".github/workflows/release-candidate.yml").read_text(
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
    ) < candidate.index("Seal production-signed candidate for private retention")
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
    assert 'compatible_tag="${version%.*}"' in alias_job
    assert 'for alias_tag in "${compatible_tag}" latest' in alias_job
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
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    security_job = workflow.split("  security:", 1)[1]

    assert "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514" in security_job
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


def test_ci_python_cache_tracks_repository_requirement_files():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    python_job = workflow.split("  python:", 1)[1].split("\n  frontend:", 1)[0]

    assert "fetch-depth: 0" in python_job
    assert "cache: pip" in python_job
    assert "cache-dependency-path:" in python_job
    assert "deploy/requirements/runtime.txt" in python_job
    assert "deploy/requirements/dev.txt" in python_job
    assert "deploy/requirements/dev.constraints.txt" in python_job


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
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "review@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Review"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "exact source"], cwd=tmp_path, check=True
    )
    exact_git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
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
