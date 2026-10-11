import hashlib
import importlib.util
import json
import marshal
import stat
import subprocess
from pathlib import Path

import pytest

from core.tests.test_runtime_release_assets import SOURCE_COMMIT, _catalog


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/scan-runtime-artifacts.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("runtime_artifact_scanner", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool(path: Path, name: str) -> Path:
    value = path / name
    value.write_bytes(name.encode())
    value.chmod(0o755)
    return value


def _runtime_inputs(tmp_path: Path, monkeypatch):
    _builder, catalog, bundles, _private, public = _catalog(tmp_path, monkeypatch)
    catalog_path = tmp_path / "runtime-catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return (
        catalog_path,
        {arch: path for arch, (path, _url) in bundles.items()},
        {arch: url for arch, (_path, url) in bundles.items()},
        public,
    )


def _fake_runner(
    *,
    secret: bool = False,
    malformed: str | None = None,
    missing_self_test: str | None = None,
):
    targets: list[Path] = []

    def run(command, **_kwargs):
        command = [str(value) for value in command]
        if "dir:" in command[1]:
            target = Path(command[1].removeprefix("dir:"))
            targets.append(target)
            outputs = [
                command[index + 1]
                for index, value in enumerate(command)
                if value == "--output"
            ]
            for output in outputs:
                kind, filename = output.split("=", 1)
                if malformed == kind:
                    Path(filename).write_text("{not-json", encoding="utf-8")
                elif kind == "spdx-json":
                    Path(filename).write_text(
                        json.dumps(
                            {
                                "spdxVersion": "SPDX-2.3",
                                "packages": [
                                    {"name": "demo", "versionInfo": "1.0"}
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                else:
                    Path(filename).write_text(
                        json.dumps(
                            {
                                "bomFormat": "CycloneDX",
                                "components": [{"name": "demo", "version": "1.0"}],
                            }
                        ),
                        encoding="utf-8",
                    )
        else:
            target = Path(command[-1])
            targets.append(target)
            output = Path(command[command.index("--output") + 1])
            scanners = command[command.index("--scanners") + 1]
            if malformed == scanners:
                output.write_text("", encoding="utf-8")
            elif "scanner-self-test" in str(target):
                results = []
                if missing_self_test != "secret":
                    results.append(
                        {
                            "Target": "probe.txt",
                            "Secrets": [{"RuleID": "github-pat"}],
                        }
                    )
                if missing_self_test != "misconfig":
                    results.append(
                        {
                            "Target": "pod.yaml",
                            "Misconfigurations": [
                                {"ID": "KSV-001", "Status": "FAIL"}
                            ],
                        }
                    )
                output.write_text(
                    json.dumps(
                        {
                            "SchemaVersion": 2,
                            "ArtifactName": str(target),
                            "ArtifactType": "filesystem",
                            "Results": results,
                        }
                    ),
                    encoding="utf-8",
                )
            elif scanners == "vuln":
                output.write_text(
                    json.dumps(
                        {
                            "SchemaVersion": 2,
                            "ArtifactName": str(target),
                            "ArtifactType": "cyclonedx",
                            "Results": [
                                {
                                    "Target": "Python",
                                    "Class": "lang-pkgs",
                                    "Type": "python-pkg",
                                    "Vulnerabilities": [
                                        {"VulnerabilityID": "CVE-2099-0001"}
                                    ],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                findings = []
                if secret:
                    findings = [
                        {
                            "Target": "core/main.py",
                            "Secrets": [
                                {
                                    "RuleID": "private-key",
                                    "Category": "key",
                                    "Severity": "HIGH",
                                    "Match": "DO-NOT-PERSIST-SECRET",
                                    "Code": {"Lines": [{"Content": "secret"}]},
                                }
                            ],
                        }
                    ]
                output.write_text(
                    json.dumps(
                        {
                            "SchemaVersion": 2,
                            "ArtifactName": str(target),
                            "ArtifactType": "filesystem",
                            "Results": findings or None,
                        }
                    ),
                    encoding="utf-8",
                )
        return subprocess.CompletedProcess(command, 0, "", "")

    run.targets = targets
    return run


def _scan(module, tmp_path, monkeypatch, runner):
    catalog, bundles, urls, public = _runtime_inputs(tmp_path, monkeypatch)
    tools = tmp_path / "tools"
    tools.mkdir()
    cache = tmp_path / "trivy-cache"
    cache.mkdir(mode=0o700)
    return module.scan_runtime_artifacts(
        catalog_path=catalog,
        runtime_bundles=bundles,
        expected_bundle_urls=urls,
        expected_version="1.4.4",
        expected_source_commit=SOURCE_COMMIT,
        syft_path=_tool(tools, "syft-pinned"),
        trivy_path=_tool(tools, "trivy-pinned"),
        trivy_cache_dir=cache,
        output_dir=tmp_path / "reports",
        public_keys=public,
        timeout_seconds=30,
        runner=runner,
    )


def test_scans_both_verified_runtime_archives_and_binds_canonical_evidence(
    tmp_path, monkeypatch
):
    module = _load_scanner()
    runner = _fake_runner()

    def forbidden_extractall(*_args, **_kwargs):
        raise AssertionError("extractall must never be used")

    monkeypatch.setattr(module.zipfile.ZipFile, "extractall", forbidden_extractall)
    evidence_path = _scan(module, tmp_path, monkeypatch, runner)
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes)

    assert evidence_bytes == (
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    assert evidence["status"] == "passed"
    assert evidence["source_commit"] == SOURCE_COMMIT
    assert set(evidence["architectures"]) == {"amd64", "arm64"}
    assert evidence["catalog_sha256"] == hashlib.sha256(
        (tmp_path / "runtime-catalog.json").read_bytes()
    ).hexdigest()
    for architecture, result in evidence["architectures"].items():
        assert result["generation_id"]
        assert len(result["descriptor_sha256"]) == 64
        assert result["reports"]["vulnerabilities"]["policy"] == (
            "advisory_accepted_unfixed"
        )
        assert result["reports"]["vulnerabilities"]["finding_count"] == 1
        assert result["reports"]["vulnerabilities"]["input_sha256"] == result[
            "reports"
        ]["cyclonedx"]["sha256"]
        assert result["reports"]["secret_misconfig"]["finding_count"] == 0
        assert result["reports"]["own_code_manifest"]["file_count"] > 0
        for report in result["reports"].values():
            report_path = evidence_path.parent / report["path"]
            assert report["sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert all(not target.exists() for target in runner.targets)


def test_bundle_tamper_is_rejected_before_any_tool_runs(tmp_path, monkeypatch):
    module = _load_scanner()
    catalog, bundles, urls, public = _runtime_inputs(tmp_path, monkeypatch)
    bundles["amd64"].write_bytes(bundles["amd64"].read_bytes() + b"tamper")
    runner = _fake_runner()
    tools = tmp_path / "tools"
    tools.mkdir()
    cache = tmp_path / "trivy-cache"
    cache.mkdir(mode=0o700)

    with pytest.raises(Exception, match="hash"):
        module.scan_runtime_artifacts(
            catalog_path=catalog,
            runtime_bundles=bundles,
            expected_bundle_urls=urls,
            expected_version="1.4.4",
            expected_source_commit=SOURCE_COMMIT,
            syft_path=_tool(tools, "syft-pinned"),
            trivy_path=_tool(tools, "trivy-pinned"),
            trivy_cache_dir=cache,
            output_dir=tmp_path / "reports",
            public_keys=public,
            runner=runner,
        )

    assert runner.targets == []


@pytest.mark.parametrize("outcome", ["nonzero", "timeout"])
def test_tool_failures_and_timeouts_are_blocking(tmp_path, outcome):
    module = _load_scanner()

    def runner(command, **_kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 2, "sensitive stdout", "sensitive stderr")

    with pytest.raises(module.RuntimeArtifactScanError, match="failed|timed out"):
        module.run_tool(["pinned-tool", "scan"], timeout_seconds=1, runner=runner)


@pytest.mark.parametrize("state", ["missing", "empty", "malformed"])
def test_missing_empty_and_malformed_reports_are_rejected(tmp_path, state):
    module = _load_scanner()
    report = tmp_path / "report.json"
    if state == "empty":
        report.write_bytes(b"")
    elif state == "malformed":
        report.write_text("{bad", encoding="utf-8")

    with pytest.raises(module.RuntimeArtifactScanError, match="missing|empty|JSON"):
        module.read_json_report(report)


def test_sbom_without_signed_dependency_coverage_is_rejected():
    module = _load_scanner()

    with pytest.raises(module.RuntimeArtifactScanError, match="coverage"):
        module.validate_spdx_report(
            {"spdxVersion": "SPDX-2.3", "packages": []},
            {("demo", "1.0")},
        )
    with pytest.raises(module.RuntimeArtifactScanError, match="coverage"):
        module.validate_cyclonedx_report(
            {"bomFormat": "CycloneDX", "components": []},
            {("demo", "1.0")},
        )


def test_trivy_report_without_artifact_or_target_coverage_is_rejected(tmp_path):
    module = _load_scanner()

    with pytest.raises(module.RuntimeArtifactScanError, match="coverage"):
        module.validate_vulnerability_report(
            {
                "SchemaVersion": 2,
                "ArtifactName": "",
                "ArtifactType": "cyclonedx",
                "Results": [],
            },
            tmp_path,
        )


@pytest.mark.parametrize(
    "report_update",
    [
        {"SchemaVersion": 1},
        {"ArtifactType": "filesystem"},
        {"Results": ["not-an-object"]},
        {
            "Results": [
                {
                    "Target": "dependencies/site-packages",
                    "Vulnerabilities": {"VulnerabilityID": "CVE-2099-1"},
                }
            ]
        },
        {
            "Results": [
                {
                    "Target": "dependencies/site-packages",
                    "Vulnerabilities": ["not-an-object"],
                }
            ]
        },
    ],
)
def test_malformed_trivy_vulnerability_schema_is_rejected(tmp_path, report_update):
    module = _load_scanner()
    report = {
        "SchemaVersion": 2,
        "ArtifactName": str(tmp_path),
        "ArtifactType": "cyclonedx",
        "Results": [{"Target": "dependencies/site-packages", "Vulnerabilities": []}],
    }
    report.update(report_update)

    with pytest.raises(module.RuntimeArtifactScanError, match="Trivy|objects"):
        module.validate_vulnerability_report(report, tmp_path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("Secrets", {"RuleID": "private-key"}),
        ("Secrets", ["not-an-object"]),
        ("Misconfigurations", {"ID": "CFG-1"}),
        ("Misconfigurations", ["not-an-object"]),
    ],
)
def test_malformed_trivy_safety_finding_collections_are_rejected(
    tmp_path, key, value
):
    module = _load_scanner()
    report = {
        "SchemaVersion": 2,
        "ArtifactName": str(tmp_path),
        "ArtifactType": "filesystem",
        "Results": [{"Target": "core/main.py", key: value}],
    }

    with pytest.raises(module.RuntimeArtifactScanError, match="objects"):
        module._sanitized_safety_report(
            report,
            tmp_path,
            scanner_self_test={"secret": True, "misconfiguration": True},
            own_code_manifest={"sha256": "a" * 64, "file_count": 1, "bytes": 1},
        )


@pytest.mark.parametrize("missing", ["secret", "misconfig"])
def test_clean_own_code_scan_requires_both_real_scanner_self_tests(
    tmp_path, monkeypatch, missing
):
    module = _load_scanner()

    with pytest.raises(module.RuntimeArtifactScanError, match="self-test"):
        _scan(
            module,
            tmp_path,
            monkeypatch,
            _fake_runner(missing_self_test=missing),
        )


def test_scanner_self_test_builds_detector_probe_only_at_runtime(tmp_path):
    module = _load_scanner()
    expected_probe = "token=" + "".join(
        ("gh", "p_", "0123456789", "abcdefghijklmnopqrstuvwxyz")
    ) + "\n"
    observed: list[str] = []

    def runner(command, **_kwargs):
        command = [str(value) for value in command]
        target = Path(command[-1])
        probe = (target / "probe.txt").read_text(encoding="utf-8")
        observed.append(probe)
        output = Path(command[command.index("--output") + 1])
        output.write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "ArtifactName": str(target),
                    "ArtifactType": "filesystem",
                    "Results": [
                        {
                            "Target": "probe.txt",
                            "Secrets": (
                                [{"RuleID": "github-pat"}]
                                if probe == expected_probe
                                else []
                            ),
                        },
                        {
                            "Target": "pod.yaml",
                            "Misconfigurations": [
                                {"ID": "KSV-001", "Status": "FAIL"}
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0)

    result = module._run_trivy_self_test(
        _tool(tmp_path, "trivy"),
        tmp_path,
        timeout_seconds=30,
        runner=runner,
    )

    assert result == {"secret": True, "misconfiguration": True}
    assert observed == [expected_probe]
    detector_signature = expected_probe.removeprefix("token=").strip().encode()
    assert detector_signature not in SCRIPT.read_bytes()
    for source_path in (SCRIPT, Path(__file__)):
        compiled = compile(source_path.read_bytes(), str(source_path), "exec")
        assert detector_signature not in marshal.dumps(compiled)


@pytest.mark.parametrize("results", [None, "omitted"])
def test_clean_finding_collections_are_accepted_with_coverage_proof(
    tmp_path, results
):
    module = _load_scanner()
    report = {
        "SchemaVersion": 2,
        "ArtifactName": str(tmp_path),
        "ArtifactType": "filesystem",
    }
    if results != "omitted":
        report["Results"] = results

    sanitized, count = module._sanitized_safety_report(
        report,
        tmp_path,
        scanner_self_test={"secret": True, "misconfiguration": True},
        own_code_manifest={"sha256": "b" * 64, "file_count": 2, "bytes": 3},
    )

    assert count == 0
    assert sanitized["finding_count"] == 0
    assert sanitized["scanner_self_test"] == {
        "secret": True,
        "misconfiguration": True,
    }


def test_incomplete_signed_own_code_manifest_is_rejected(tmp_path):
    module = _load_scanner()
    root = tmp_path / "runtime"
    (root / "core").mkdir(parents=True)
    payload = root / "core" / "main.py"
    payload.write_bytes(b"print('ok')\n")
    payload.chmod(0o644)
    descriptor = {"files": []}

    with pytest.raises(module.RuntimeArtifactScanError, match="manifest|member set"):
        module._own_code_manifest(root, descriptor)


def test_signed_own_code_manifest_accepts_empty_files(tmp_path):
    module = _load_scanner()
    root = tmp_path / "runtime"
    (root / "core").mkdir(parents=True)
    payload = root / "core" / "__init__.py"
    payload.write_bytes(b"")
    payload.chmod(0o644)
    descriptor = {
        "files": [
            {
                "path": "core/__init__.py",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "size": 0,
                "mode": "0644",
            }
        ]
    }

    manifest = module._own_code_manifest(root, descriptor)

    assert manifest["counts"] == {"files": 1, "bytes": 0}


def test_commands_use_exact_cyclonedx_input_and_explicit_trivy_cache(
    tmp_path, monkeypatch
):
    module = _load_scanner()
    delegate = _fake_runner()
    commands: list[list[str]] = []
    sbom_hashes: dict[str, str] = {}

    def runner(command, **kwargs):
        command = [str(value) for value in command]
        commands.append(command)
        if len(command) > 1 and command[1] == "sbom":
            architecture = "amd64" if "amd64" in command[-1] else "arm64"
            sbom_hashes[architecture] = hashlib.sha256(
                Path(command[-1]).read_bytes()
            ).hexdigest()
        return delegate(command, **kwargs)

    evidence_path = _scan(module, tmp_path, monkeypatch, runner)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    trivy_commands = [command for command in commands if "--scanners" in command]
    vuln_commands = [command for command in trivy_commands if command[1] == "sbom"]

    assert len(vuln_commands) == 2
    for command in trivy_commands:
        assert "--cache-dir" in command
        assert Path(command[command.index("--cache-dir") + 1]) == (
            tmp_path / "trivy-cache"
        )
    for command in vuln_commands:
        architecture = "amd64" if "amd64" in command[-1] else "arm64"
        assert sbom_hashes[architecture] == evidence[
            "architectures"
        ][architecture]["reports"]["vulnerabilities"]["input_sha256"]


def test_secret_findings_block_and_persist_only_sanitized_metadata(
    tmp_path, monkeypatch
):
    module = _load_scanner()

    with pytest.raises(module.RuntimeArtifactScanError, match="secret or misconfiguration"):
        _scan(module, tmp_path, monkeypatch, _fake_runner(secret=True))

    reports = tmp_path / "reports"
    saved = list(reports.glob("*.secret-misconfig.json"))
    assert saved
    assert all("DO-NOT-PERSIST-SECRET" not in path.read_text(encoding="utf-8") for path in saved)


def test_tool_paths_must_be_pinned_regular_executables(tmp_path):
    module = _load_scanner()
    relative = Path("syft")
    regular = tmp_path / "regular"
    regular.write_text("tool", encoding="utf-8")
    regular.chmod(stat.S_IRUSR | stat.S_IWUSR)

    with pytest.raises(module.RuntimeArtifactScanError, match="absolute"):
        module.validate_tool_path(relative, label="Syft")
    with pytest.raises(module.RuntimeArtifactScanError, match="executable"):
        module.validate_tool_path(regular, label="Syft")


def test_hardlinked_tool_is_rejected(tmp_path):
    module = _load_scanner()
    tool = _tool(tmp_path, "syft")
    (tmp_path / "syft-second-link").hardlink_to(tool)

    with pytest.raises(module.RuntimeArtifactScanError, match="hard link"):
        module.validate_tool_path(tool, label="Syft")


def test_scanner_executes_private_tool_snapshot_after_source_path_replacement(
    tmp_path, monkeypatch
):
    module = _load_scanner()
    catalog, bundles, urls, public = _runtime_inputs(tmp_path, monkeypatch)
    tools = tmp_path / "tools"
    tools.mkdir()
    syft = _tool(tools, "syft-pinned")
    trivy = _tool(tools, "trivy-pinned")
    cache = tmp_path / "trivy-cache"
    cache.mkdir(mode=0o700)
    delegate = _fake_runner()
    observed: list[tuple[Path, bytes]] = []

    def runner(command, **kwargs):
        executable = Path(command[0])
        observed.append((executable, executable.read_bytes()))
        if len(observed) == 1:
            replacement = tools / "replacement"
            replacement.write_bytes(b"replacement-tool")
            replacement.chmod(0o755)
            syft.unlink()
            replacement.rename(syft)
        return delegate(command, **kwargs)

    evidence_path = module.scan_runtime_artifacts(
        catalog_path=catalog,
        runtime_bundles=bundles,
        expected_bundle_urls=urls,
        expected_version="1.4.4",
        expected_source_commit=SOURCE_COMMIT,
        syft_path=syft,
        trivy_path=trivy,
        trivy_cache_dir=cache,
        output_dir=tmp_path / "reports",
        public_keys=public,
        runner=runner,
    )

    assert evidence_path.is_file()
    assert observed
    assert all(path.parent != tools for path, _content in observed)
    assert all(content in {b"syft-pinned", b"trivy-pinned"} for _path, content in observed)
