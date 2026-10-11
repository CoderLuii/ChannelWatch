#!/usr/bin/env python3
"""Verify and scan signed runtime update ZIPs without publishing artifacts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
VERIFY_SCRIPT = SCRIPT_DIR / "verify-update-assets.py"
_VERIFY_SPEC = importlib.util.spec_from_file_location(
    "channelwatch_verify_update_assets", VERIFY_SCRIPT
)
if _VERIFY_SPEC is None or _VERIFY_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("Unable to load the runtime update verifier.")
_VERIFIER = importlib.util.module_from_spec(_VERIFY_SPEC)
_VERIFY_SPEC.loader.exec_module(_VERIFIER)

MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_TOOL_BYTES = 512 * 1024 * 1024
ARCHITECTURES = ("amd64", "arm64")
Runner = Callable[..., subprocess.CompletedProcess[str]]


class RuntimeArtifactScanError(RuntimeError):
    """Raised when verified runtime artifact scanning cannot be trusted."""


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_bounded(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeArtifactScanError(f"{label} is missing or unreadable.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeArtifactScanError(f"{label} must be a regular file.")
    if metadata.st_size <= 0:
        raise RuntimeArtifactScanError(f"{label} is empty.")
    if metadata.st_size > maximum:
        raise RuntimeArtifactScanError(f"{label} exceeds its bounded size.")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeArtifactScanError(f"{label} is unreadable.") from exc


def _open_tool(path: Path, *, label: str) -> int:
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeArtifactScanError(f"{label} path must be absolute.")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:  # pragma: no cover - release scans run on Linux
        raise RuntimeArtifactScanError(
            f"{label} cannot be pinned safely on this platform."
        )
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | nofollow,
        )
    except OSError as exc:
        raise RuntimeArtifactScanError(f"{label} executable is missing.") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeArtifactScanError(f"{label} must be a regular executable.")
        if metadata.st_nlink != 1:
            raise RuntimeArtifactScanError(f"{label} cannot have a hard link.")
        if metadata.st_uid not in {0, os.geteuid()}:
            raise RuntimeArtifactScanError(f"{label} has an untrusted owner.")
        if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise RuntimeArtifactScanError(f"{label} has an unsafe executable mode.")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeArtifactScanError(
                f"{label} cannot be group- or world-writable."
            )
        if not metadata.st_mode & (
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        ):
            raise RuntimeArtifactScanError(f"{label} path is not executable.")
        if metadata.st_size <= 0 or metadata.st_size > MAX_TOOL_BYTES:
            raise RuntimeArtifactScanError(f"{label} executable size is invalid.")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def validate_tool_path(path: Path, *, label: str) -> Path:
    """Validate a caller-supplied tool using a no-follow file descriptor."""

    descriptor = _open_tool(path, label=label)
    os.close(descriptor)
    return path


def _copy_open_tool(
    descriptor: int, destination: Path, *, label: str
) -> tuple[Path, str]:
    source_before = os.fstat(descriptor)
    digest = hashlib.sha256()
    copied = 0
    output_descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o500,
    )
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > MAX_TOOL_BYTES:
                raise RuntimeArtifactScanError(f"{label} executable size changed.")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(output_descriptor, view)
                if written <= 0:  # pragma: no cover - defensive OS boundary
                    raise RuntimeArtifactScanError(f"{label} snapshot write failed.")
                view = view[written:]
        source_after = os.fstat(descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if copied != source_before.st_size or any(
            getattr(source_before, field) != getattr(source_after, field)
            for field in stable_fields
        ):
            raise RuntimeArtifactScanError(
                f"{label} executable changed while snapshotting."
            )
        os.fchmod(output_descriptor, 0o500)
        os.fsync(output_descriptor)
    finally:
        os.close(output_descriptor)
    return destination, digest.hexdigest()


@contextlib.contextmanager
def _pinned_tool_snapshots(syft_path: Path, trivy_path: Path):
    descriptors: list[int] = []
    try:
        syft_descriptor = _open_tool(syft_path, label="Syft")
        descriptors.append(syft_descriptor)
        trivy_descriptor = _open_tool(trivy_path, label="Trivy")
        descriptors.append(trivy_descriptor)
        with tempfile.TemporaryDirectory(
            prefix="channelwatch-runtime-scan-tools-"
        ) as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            syft, syft_hash = _copy_open_tool(
                syft_descriptor, root / "syft", label="Syft"
            )
            trivy, trivy_hash = _copy_open_tool(
                trivy_descriptor, root / "trivy", label="Trivy"
            )
            yield syft, trivy, {
                "syft_sha256": syft_hash,
                "trivy_sha256": trivy_hash,
            }
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def run_tool(
    command: Sequence[str | Path],
    *,
    timeout_seconds: int,
    runner: Runner = subprocess.run,
) -> None:
    """Run one pinned scanner while keeping its output out of error messages."""

    if timeout_seconds <= 0:
        raise RuntimeArtifactScanError("Scanner timeout must be positive.")
    argv = [str(value) for value in command]
    try:
        result = runner(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeArtifactScanError(f"{Path(argv[0]).name} timed out.") from exc
    except OSError as exc:
        raise RuntimeArtifactScanError(
            f"{Path(argv[0]).name} failed to execute."
        ) from exc
    if result.returncode != 0:
        raise RuntimeArtifactScanError(
            f"{Path(argv[0]).name} failed with exit code {result.returncode}."
        )


def read_json_report(path: Path) -> dict[str, Any]:
    """Read a nonempty, bounded JSON object produced by a scanner."""

    raw = _read_bounded(path, maximum=MAX_REPORT_BYTES, label="Scanner report")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeArtifactScanError("Scanner report is not valid JSON.") from exc
    if not isinstance(value, dict) or not value:
        raise RuntimeArtifactScanError("Scanner report JSON must be a nonempty object.")
    return value


def _normalized_name(value: object) -> str:
    return re.sub(r"[-_.]+", "-", str(value).strip().lower())


def validate_spdx_report(
    report: dict[str, Any], expected_dependencies: set[tuple[str, str]]
) -> int:
    packages = report.get("packages")
    if not str(report.get("spdxVersion") or "").startswith("SPDX-") or not isinstance(
        packages, list
    ):
        raise RuntimeArtifactScanError("SPDX report format is invalid.")
    covered = {
        (_normalized_name(item.get("name")), str(item.get("versionInfo") or ""))
        for item in packages
        if isinstance(item, dict)
    }
    if not expected_dependencies or not expected_dependencies.issubset(covered):
        raise RuntimeArtifactScanError("SPDX report lacks signed dependency coverage.")
    return len(packages)


def validate_cyclonedx_report(
    report: dict[str, Any], expected_dependencies: set[tuple[str, str]]
) -> int:
    components = report.get("components")
    if report.get("bomFormat") != "CycloneDX" or not isinstance(components, list):
        raise RuntimeArtifactScanError("CycloneDX report format is invalid.")
    covered = {
        (_normalized_name(item.get("name")), str(item.get("version") or ""))
        for item in components
        if isinstance(item, dict)
    }
    if not expected_dependencies or not expected_dependencies.issubset(covered):
        raise RuntimeArtifactScanError(
            "CycloneDX report lacks signed dependency coverage."
        )
    return len(components)


def _validate_trivy_envelope(
    report: dict[str, Any],
    target_path: Path,
    *,
    artifact_type: str,
    require_result_coverage: bool,
    allow_null_results: bool = False,
) -> list[dict[str, Any]]:
    if "Results" not in report and not allow_null_results:
        raise RuntimeArtifactScanError("Trivy report lacks Results coverage.")
    results = report.get("Results")
    artifact_name = str(report.get("ArtifactName") or "")
    if (
        report.get("SchemaVersion") != 2
        or not artifact_name
        or report.get("ArtifactType") != artifact_type
    ):
        raise RuntimeArtifactScanError(
            f"Trivy schema 2 {artifact_type} report is malformed or lacks coverage."
        )
    if results is None:
        if not allow_null_results:
            raise RuntimeArtifactScanError("Trivy report lacks Results coverage.")
        results = []
    if not isinstance(results, list) or any(
        not isinstance(item, dict) for item in results
    ):
        raise RuntimeArtifactScanError("Trivy Results must be a list of objects.")
    try:
        covered_artifact = Path(artifact_name).resolve() == target_path.resolve()
    except OSError:
        covered_artifact = False
    covered_results = [
        item
        for item in results
        if isinstance(item, dict)
        and any(str(item.get(key) or "") for key in ("Target", "Class", "Type"))
    ]
    if not covered_artifact or (require_result_coverage and not covered_results):
        raise RuntimeArtifactScanError("Trivy report lacks target coverage.")
    return results


def _finding_objects(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if key not in result:
        return []
    findings = result[key]
    if findings is None:
        return []
    if not isinstance(findings, list) or any(
        not isinstance(item, dict) for item in findings
    ):
        raise RuntimeArtifactScanError(
            f"Trivy {key} must be a list containing only objects."
        )
    return findings


def validate_vulnerability_report(
    report: dict[str, Any], target_path: Path
) -> int:
    results = _validate_trivy_envelope(
        report,
        target_path,
        artifact_type="cyclonedx",
        require_result_coverage=True,
    )
    return sum(len(_finding_objects(item, "Vulnerabilities")) for item in results)


def _sanitized_safety_report(
    report: dict[str, Any],
    target_path: Path,
    *,
    scanner_self_test: dict[str, bool],
    own_code_manifest: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    if scanner_self_test != {"secret": True, "misconfiguration": True}:
        raise RuntimeArtifactScanError("Trivy scanner self-test proof is incomplete.")
    if (
        not isinstance(own_code_manifest.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", own_code_manifest["sha256"])
        or not isinstance(own_code_manifest.get("file_count"), int)
        or own_code_manifest["file_count"] <= 0
        or not isinstance(own_code_manifest.get("bytes"), int)
        or own_code_manifest["bytes"] < 0
    ):
        raise RuntimeArtifactScanError("Signed own-code manifest proof is incomplete.")
    results = _validate_trivy_envelope(
        report,
        target_path,
        artifact_type="filesystem",
        require_result_coverage=False,
        allow_null_results=True,
    )
    findings: list[dict[str, str]] = []
    for result in results:
        target = str(result.get("Target") or "")
        for item in _finding_objects(result, "Secrets"):
            findings.append(
                {
                    "kind": "secret",
                    "target": target,
                    "id": str(item.get("RuleID") or ""),
                    "category": str(item.get("Category") or ""),
                    "severity": str(item.get("Severity") or ""),
                }
            )
        for item in _finding_objects(result, "Misconfigurations"):
            status_value = str(item.get("Status") or "FAIL").upper()
            if status_value == "PASS":
                continue
            findings.append(
                {
                    "kind": "misconfiguration",
                    "target": target,
                    "id": str(item.get("ID") or item.get("AVDID") or ""),
                    "severity": str(item.get("Severity") or ""),
                    "status": status_value,
                }
            )
    sanitized = {
        "schema": 1,
        "scanner": "trivy",
        "artifact_type": str(report.get("ArtifactType") or ""),
        "scanner_self_test": scanner_self_test,
        "own_code_manifest": own_code_manifest,
        "finding_count": len(findings),
        "findings": findings,
    }
    return sanitized, len(findings)


def _run_trivy_self_test(
    trivy: Path,
    cache_dir: Path,
    *,
    timeout_seconds: int,
    runner: Runner,
) -> dict[str, bool]:
    with tempfile.TemporaryDirectory(
        prefix="channelwatch-scanner-self-test-"
    ) as temporary:
        root = Path(temporary) / "scanner-self-test"
        root.mkdir(mode=0o700)
        secret_path = root / "probe.txt"
        detector_probe = "token=" + "".join(
            ("gh", "p_", "0123456789", "abcdefghijklmnopqrstuvwxyz")
        ) + "\n"
        secret_path.write_text(
            detector_probe,
            encoding="utf-8",
        )
        secret_path.chmod(0o600)
        config_path = root / "pod.yaml"
        config_path.write_text(
            "apiVersion: v1\nkind: Pod\nmetadata:\n  name: scanner-probe\n"
            "spec:\n  containers:\n    - name: probe\n      image: busybox:1.36\n"
            "      securityContext:\n        privileged: true\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        report_path = Path(temporary) / "report.json"
        run_tool(
            [
                trivy,
                "fs",
                "--cache-dir",
                cache_dir,
                "--config",
                "",
                "--ignorefile",
                "",
                "--secret-config",
                "",
                "--scanners",
                "secret,misconfig",
                "--format",
                "json",
                "--output",
                report_path,
                root,
            ],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        report = read_json_report(report_path)
        results = _validate_trivy_envelope(
            report,
            root,
            artifact_type="filesystem",
            require_result_coverage=True,
        )
        secret_count = sum(len(_finding_objects(item, "Secrets")) for item in results)
        misconfiguration_count = sum(
            1
            for item in results
            for finding in _finding_objects(item, "Misconfigurations")
            if str(finding.get("Status") or "FAIL").upper() != "PASS"
        )
        if not secret_count or not misconfiguration_count:
            raise RuntimeArtifactScanError(
                "Trivy scanner self-test did not detect both fixture types."
            )
        return {"secret": True, "misconfiguration": True}


def _write_report(output_dir: Path, filename: str, report: Any) -> tuple[Path, str]:
    destination = output_dir / filename
    raw = _canonical_bytes(report)
    destination.write_bytes(raw)
    return destination, _sha256(raw)


def _target_release(catalog: dict[str, Any], version: str) -> dict[str, Any]:
    normalized = version.strip().lstrip("v")
    releases = catalog["payload"]["releases"]
    matches = [
        release
        for release in releases
        if str(release.get("version") or "").strip().lstrip("v") == normalized
    ]
    if len(matches) != 1:
        raise RuntimeArtifactScanError("Verified runtime release is not unique.")
    return matches[0]


def _extract_verified_members(
    bundle: bytes, descriptor: dict[str, Any], destination: Path
) -> None:
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as archive:
        selected = _VERIFIER._runtime_archive_files(archive, descriptor)
        signed_modes = {
            _VERIFIER.DESCRIPTOR_FILENAME: 0o644,
            _VERIFIER.DEPENDENCY_INVENTORY_PATH: 0o644,
            **{
                str(record["path"]): int(str(record["mode"]), 8)
                for record in descriptor["files"]
            },
        }
        if set(selected) != set(signed_modes):
            raise RuntimeArtifactScanError("Verified runtime member set changed.")
        for name in sorted(selected):
            pure = PurePosixPath(name)
            if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise RuntimeArtifactScanError("Runtime member path is unsafe.")
            target = destination.joinpath(*pure.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if target.exists() or target.is_symlink():
                raise RuntimeArtifactScanError("Runtime member extraction collided.")
            content = archive.read(selected[name])
            with target.open("xb") as stream:
                stream.write(content)
            target.chmod(signed_modes[name])


def _inventory_dependencies(root: Path) -> set[tuple[str, str]]:
    inventory_path = root / _VERIFIER.DEPENDENCY_INVENTORY_PATH
    inventory = read_json_report(inventory_path)
    dependencies = inventory.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise RuntimeArtifactScanError("Signed dependency inventory is empty.")
    result = {
        (_normalized_name(item.get("name")), str(item.get("version") or ""))
        for item in dependencies
        if isinstance(item, dict)
        and _normalized_name(item.get("name"))
        and str(item.get("version") or "")
    }
    if len(result) != len(dependencies):
        raise RuntimeArtifactScanError("Signed dependency inventory is malformed.")
    return result


def _validate_cache_dir(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeArtifactScanError("Trivy cache path must be absolute.")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeArtifactScanError("Trivy cache directory is missing.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeArtifactScanError("Trivy cache must be a real directory.")
    if metadata.st_uid not in {0, os.geteuid()}:
        raise RuntimeArtifactScanError("Trivy cache has an untrusted owner.")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeArtifactScanError(
            "Trivy cache cannot be group- or world-writable."
        )
    return path


def _own_code_manifest(root: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    signed_records = [
        record
        for record in descriptor.get("files", [])
        if isinstance(record, dict)
        and not str(record.get("path") or "").startswith("dependencies/")
    ]
    expected_paths = {str(record.get("path") or "") for record in signed_records}
    if (
        not expected_paths
        or "" in expected_paths
        or len(expected_paths) != len(signed_records)
    ):
        raise RuntimeArtifactScanError("Signed own-code manifest is malformed.")

    metadata_paths = {
        _VERIFIER.DESCRIPTOR_FILENAME,
        _VERIFIER.DEPENDENCY_INVENTORY_PATH,
    }
    actual_paths: set[str] = set()
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        if relative == "dependencies" or relative.startswith("dependencies/"):
            continue
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeArtifactScanError("Own-code manifest contains a symlink.")
        if stat.S_ISREG(metadata.st_mode) and relative not in metadata_paths:
            actual_paths.add(relative)
        elif not stat.S_ISDIR(metadata.st_mode) and relative not in metadata_paths:
            raise RuntimeArtifactScanError("Own-code manifest contains an unsafe member.")
    if actual_paths != expected_paths:
        raise RuntimeArtifactScanError("Signed own-code member set is incomplete.")

    files: list[dict[str, Any]] = []
    for record in sorted(signed_records, key=lambda item: str(item["path"])):
        path = root.joinpath(*PurePosixPath(str(record["path"])).parts)
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RuntimeArtifactScanError(
                    "Signed own-code file must be a regular file."
                )
            if metadata.st_size > _VERIFIER.MAX_RUNTIME_MEMBER_BYTES:
                raise RuntimeArtifactScanError(
                    "Signed own-code file exceeds its bounded size."
                )
            raw = path.read_bytes()
        except OSError as exc:
            raise RuntimeArtifactScanError(
                "Signed own-code file is missing or unreadable."
            ) from exc
        mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
        if (
            len(raw) != metadata.st_size
            or len(raw) != record.get("size")
            or _sha256(raw) != record.get("sha256")
            or mode != record.get("mode")
        ):
            raise RuntimeArtifactScanError("Signed own-code manifest does not match disk.")
        files.append(
            {
                "path": str(record["path"]),
                "sha256": str(record["sha256"]),
                "size": len(raw),
                "mode": mode,
            }
        )
    manifest = {
        "schema": 1,
        "files": files,
        "counts": {
            "files": len(files),
            "bytes": sum(item["size"] for item in files),
        },
    }
    manifest["sha256"] = _sha256(_canonical_bytes(manifest))
    return manifest


def _scan_runtime_artifacts_with_tools(
    *,
    catalog_path: Path,
    runtime_bundles: dict[str, Path],
    expected_bundle_urls: dict[str, str],
    expected_version: str,
    expected_source_commit: str,
    syft_path: Path,
    trivy_path: Path,
    trivy_cache_dir: Path,
    tool_hashes: dict[str, str],
    output_dir: Path,
    public_keys: dict[str, str] | None = None,
    timeout_seconds: int = 300,
    runner: Runner = subprocess.run,
) -> Path:
    syft = syft_path
    trivy = trivy_path
    trivy_cache = _validate_cache_dir(trivy_cache_dir)
    if set(runtime_bundles) != set(ARCHITECTURES):
        raise RuntimeArtifactScanError("Exactly amd64 and arm64 bundles are required.")
    catalog_bytes = _read_bounded(
        Path(catalog_path),
        maximum=_VERIFIER.MAX_RUNTIME_CATALOG_BYTES,
        label="Runtime catalog",
    )
    bundle_bytes = {
        architecture: _read_bounded(
            Path(runtime_bundles[architecture]),
            maximum=_VERIFIER.MAX_BUNDLE_BYTES,
            label=f"{architecture} runtime bundle",
        )
        for architecture in ARCHITECTURES
    }
    catalog = _VERIFIER.verify_runtime_update_catalog(
        catalog_bytes,
        bundle_bytes,
        public_keys=public_keys,
        expected_version=expected_version,
        expected_source_commit=expected_source_commit,
        expected_bundle_urls=expected_bundle_urls,
    )
    release = _target_release(catalog, expected_version)
    variants = {
        variant["runtime_descriptor"]["architecture"]: variant
        for variant in release["runtime_variants"]
    }

    output_dir = Path(output_dir)
    if output_dir.is_symlink():
        raise RuntimeArtifactScanError("Scan output directory cannot be a symlink.")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise RuntimeArtifactScanError("Scan output directory must be empty.")
    output_dir.mkdir(parents=True, exist_ok=True)
    architecture_evidence: dict[str, Any] = {}
    report_hashes: dict[str, str] = {}
    scanner_self_test = _run_trivy_self_test(
        trivy,
        trivy_cache,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )

    for architecture in ARCHITECTURES:
        descriptor = variants[architecture]["runtime_descriptor"]
        with tempfile.TemporaryDirectory(
            prefix=f"channelwatch-runtime-scan-{architecture}-"
        ) as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "runtime"
            root.mkdir(mode=0o700)
            _extract_verified_members(bundle_bytes[architecture], descriptor, root)
            expected_dependencies = _inventory_dependencies(root)
            own_code_manifest = _own_code_manifest(root, descriptor)
            own_code_proof = {
                "sha256": own_code_manifest["sha256"],
                "file_count": own_code_manifest["counts"]["files"],
                "bytes": own_code_manifest["counts"]["bytes"],
            }
            temporary_reports = temporary_root / "scanner-output"
            temporary_reports.mkdir(mode=0o700)
            spdx_temp = temporary_reports / "spdx.json"
            cyclonedx_temp = temporary_reports / "cyclonedx.json"
            vulnerability_temp = temporary_reports / "vulnerability.json"
            safety_temp = temporary_reports / "safety.json"

            run_tool(
                [
                    syft,
                    f"dir:{root}",
                    "--output",
                    f"spdx-json={spdx_temp}",
                    "--output",
                    f"cyclonedx-json={cyclonedx_temp}",
                ],
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            spdx = read_json_report(spdx_temp)
            cyclonedx = read_json_report(cyclonedx_temp)
            spdx_count = validate_spdx_report(spdx, expected_dependencies)
            cyclonedx_count = validate_cyclonedx_report(
                cyclonedx, expected_dependencies
            )

            run_tool(
                [
                    trivy,
                    "sbom",
                    "--cache-dir",
                    trivy_cache,
                    "--scanners",
                    "vuln",
                    "--format",
                    "json",
                    "--output",
                    vulnerability_temp,
                    cyclonedx_temp,
                ],
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            vulnerabilities = read_json_report(vulnerability_temp)
            vulnerability_count = validate_vulnerability_report(
                vulnerabilities, cyclonedx_temp
            )

            run_tool(
                [
                    trivy,
                    "fs",
                    "--cache-dir",
                    trivy_cache,
                    "--config",
                    "",
                    "--ignorefile",
                    "",
                    "--secret-config",
                    "",
                    "--scanners",
                    "secret,misconfig",
                    "--skip-dirs",
                    root / "dependencies",
                    "--format",
                    "json",
                    "--output",
                    safety_temp,
                    root,
                ],
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            raw_safety = read_json_report(safety_temp)
            safety, safety_count = _sanitized_safety_report(
                raw_safety,
                root,
                scanner_self_test=scanner_self_test,
                own_code_manifest=own_code_proof,
            )

            reports: dict[str, Any] = {}
            report_specs = (
                ("spdx", "spdx.json", spdx, {"format": "spdx-json", "component_count": spdx_count}),
                ("cyclonedx", "cyclonedx.json", cyclonedx, {"format": "cyclonedx-json", "component_count": cyclonedx_count}),
                (
                    "vulnerabilities",
                    "vulnerabilities.json",
                    vulnerabilities,
                    {
                        "policy": "advisory_accepted_unfixed",
                        "finding_count": vulnerability_count,
                        "input_path": "cyclonedx.json",
                        "input_sha256": _sha256(cyclonedx_temp.read_bytes()),
                    },
                ),
                (
                    "own_code_manifest",
                    "own-code-manifest.json",
                    own_code_manifest,
                    {
                        "file_count": own_code_proof["file_count"],
                        "bytes": own_code_proof["bytes"],
                    },
                ),
                (
                    "secret_misconfig",
                    "secret-misconfig.json",
                    safety,
                    {"policy": "blocking", "finding_count": safety_count},
                ),
            )
            for name, suffix, report, metadata in report_specs:
                filename = f"runtime-{architecture}.{suffix}"
                if name == "cyclonedx":
                    destination = output_dir / filename
                    exact_cyclonedx = cyclonedx_temp.read_bytes()
                    destination.write_bytes(exact_cyclonedx)
                    digest = _sha256(exact_cyclonedx)
                else:
                    _path, digest = _write_report(output_dir, filename, report)
                reports[name] = {"path": filename, "sha256": digest, **metadata}
                report_hashes[f"{architecture}:{name}"] = digest

            if safety_count:
                raise RuntimeArtifactScanError(
                    "Runtime archive contains a blocking secret or misconfiguration finding."
                )

            architecture_evidence[architecture] = {
                "bundle_sha256": _sha256(bundle_bytes[architecture]),
                "generation_id": descriptor["generation_id"],
                "descriptor_sha256": _sha256(
                    _VERIFIER.canonical_payload_bytes(descriptor)
                ),
                "reports": reports,
            }

    evidence = {
        "schema": 1,
        "status": "passed",
        "source_commit": expected_source_commit,
        "catalog_sha256": _sha256(catalog_bytes),
        "tools": tool_hashes,
        "architectures": architecture_evidence,
        "reports_sha256": _sha256(_canonical_bytes(report_hashes)),
    }
    evidence_path = output_dir / "runtime-scan-evidence.json"
    evidence_path.write_bytes(_canonical_bytes(evidence))
    return evidence_path


def scan_runtime_artifacts(
    *,
    catalog_path: Path,
    runtime_bundles: dict[str, Path],
    expected_bundle_urls: dict[str, str],
    expected_version: str,
    expected_source_commit: str,
    syft_path: Path,
    trivy_path: Path,
    trivy_cache_dir: Path,
    output_dir: Path,
    public_keys: dict[str, str] | None = None,
    timeout_seconds: int = 300,
    runner: Runner = subprocess.run,
) -> Path:
    """Verify, safely expand, and scan both signed native runtime archives."""

    with _pinned_tool_snapshots(syft_path, trivy_path) as (
        syft,
        trivy,
        tool_hashes,
    ):
        return _scan_runtime_artifacts_with_tools(
            catalog_path=catalog_path,
            runtime_bundles=runtime_bundles,
            expected_bundle_urls=expected_bundle_urls,
            expected_version=expected_version,
            expected_source_commit=expected_source_commit,
            syft_path=syft,
            trivy_path=trivy,
            trivy_cache_dir=trivy_cache_dir,
            tool_hashes=tool_hashes,
            output_dir=output_dir,
            public_keys=public_keys,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )


def _pairs(values: list[str] | None, *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in result:
            raise RuntimeArtifactScanError(f"{label} requires unique key=value pairs.")
        result[key] = item
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--runtime-bundle", action="append", required=True)
    parser.add_argument(
        "--expected-runtime-bundle-url", action="append", required=True
    )
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--syft", type=Path, required=True)
    parser.add_argument("--trivy", type=Path, required=True)
    parser.add_argument("--trivy-cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-key", action="append")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        bundles = {
            key: Path(value)
            for key, value in _pairs(
                args.runtime_bundle, label="Runtime bundles"
            ).items()
        }
        urls = _pairs(
            args.expected_runtime_bundle_url, label="Runtime bundle URLs"
        )
        public_keys = (
            _pairs(args.public_key, label="Public keys")
            if args.public_key is not None
            else None
        )
        scan_runtime_artifacts(
            catalog_path=args.catalog,
            runtime_bundles=bundles,
            expected_bundle_urls=urls,
            expected_version=args.expected_version,
            expected_source_commit=args.expected_source_commit,
            syft_path=args.syft,
            trivy_path=args.trivy,
            trivy_cache_dir=args.trivy_cache_dir,
            output_dir=args.output,
            public_keys=public_keys,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(f"Runtime artifact scan failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
