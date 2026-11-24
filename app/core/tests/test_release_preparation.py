"""Exercise release preparation against real Git and generated catalog inputs."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts/release" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def history(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
           "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "--initial-branch=main")
    git("commit", "--allow-empty", "-m", "v1.6.5")
    git("tag", "v1.6.5")
    return tmp_path, env, git


def candidate_cli(history, messages):
    root, env, git = history
    for message in messages:
        git("commit", "--allow-empty", "-m", message)
    git("commit", "--allow-empty", "-m", "v1.6.6")
    git("tag", "v1.6.6")
    sha = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", sha)
    return subprocess.run([sys.executable, str(ROOT / "scripts/release/verify-release-candidate.py"),
                           "--tag", "v1.6.6", "--sha", sha], cwd=root, env=env,
                          capture_output=True, text=True)


def test_release_accepts_main_documentation_and_ci_commits(history):
    result = candidate_cli(history, ["docs: supporters", "ci: prepare release"])
    assert result.returncode == 0, result.stderr


def test_release_rejects_an_untagged_release_in_the_range(history):
    result = candidate_cli(history, ["v1.6.5"])
    assert result.returncode == 1
    assert "untagged release" in result.stderr


def test_release_rejects_a_divergent_predecessor(history):
    _, _, git = history
    git("checkout", "--orphan", "unrelated")
    git("commit", "--allow-empty", "-m", "unrelated history")
    git("tag", "-f", "v1.6.5")
    git("checkout", "main")
    result = candidate_cli(history, ["docs: main"])
    assert result.returncode == 1


def test_release_commit_cannot_be_a_merge(history):
    root, env, git = history
    git("checkout", "-b", "side")
    git("commit", "--allow-empty", "-m", "docs: side")
    git("checkout", "main")
    git("merge", "--no-ff", "side", "-m", "v1.6.6")
    git("tag", "v1.6.6")
    sha = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", sha)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/release/verify-release-candidate.py"),
                           "--tag", "v1.6.6", "--sha", sha], cwd=root, env=env,
                          capture_output=True, text=True)
    assert result.returncode == 1
    assert "exactly one parent" in result.stderr


def test_qualification_failure_names_the_missing_input(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/release/verify-local-qualification.py"),
                             "--output", str(tmp_path / "accepted.json")],
                            env={**os.environ, "CHANNELWATCH_LOCAL_QUALIFICATION": ""},
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "Missing or oversized local record" in result.stderr
    assert not (tmp_path / "accepted.json").exists()


def test_static_build_and_native_dependencies_can_start_together():
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())["jobs"]
    assert jobs["candidate-source-gates"]["needs"] == "release-preflight"
    assert set(jobs["prepare-runtime-archives"]["needs"]) == {"candidate-source-gates", "runtime-dependencies"}
    assert "prepare-runtime-archives" in jobs["candidate-signing"]["needs"]


def test_static_build_does_not_install_backend_packages_it_does_not_execute():
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())["jobs"]
    assert not any("pip install" in step.get("run", "") for step in jobs["candidate-source-gates"]["steps"])
    for name in ("release-preflight", "prepare-runtime-archives"):
        installs = [step["run"] for step in jobs[name]["steps"] if "pip install" in step.get("run", "") and step.get("if") != "github.event_name == 'workflow_dispatch'"]
        assert len(installs) == 1
        assert "-r scripts/release/verifier-requirements.txt" in installs[0]
        assert "-r deploy/requirements/runtime.txt" not in installs[0]


def test_early_and_late_gates_execute_one_shared_candidate_policy():
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())["jobs"]
    for name, step_name in (("release-preflight", "Verify release identity and local qualification"),
                            ("candidate-qualification", "Verify exact candidate identity")):
        step = next(step for step in jobs[name]["steps"] if step.get("name") == step_name)
        assert "--config scripts/release/release-config.json" in step["run"]
        assert '--event-ref "${GITHUB_REF}"' in step["run"]
        assert '--event-sha "${GITHUB_SHA}"' in step["run"]
        assert "actual_subject=" not in step["run"]


@pytest.fixture
def catalog_preflight(monkeypatch):
    module = load_script("verify-release-inputs.py")
    builder = module.load_builder(ROOT)
    checked = []
    monkeypatch.setenv("GITHUB_REPOSITORY", "CoderLuii/ChannelWatch")
    monkeypatch.setenv("GH_TOKEN", "fixture-token")

    def verify_absence(tag, *, repository, github_token, **_kwargs):
        checked.append((tag, repository, github_token))

    monkeypatch.setattr(builder, "_verify_github_release_absence", verify_absence)
    return module, builder, checked


def test_current_catalog_metadata_passes_the_real_client_validator(catalog_preflight):
    module, builder, checked = catalog_preflight
    result = module.validate_inputs(ROOT, builder=builder)
    assert result["catalog_schema"] == 2
    assert result["release_count"] > 1
    assert 0 < result["highlights"] <= 32
    assert checked == []
    assert result["published_predecessor"] == "v1.6.8"


def test_preflight_rejects_highlights_the_client_cannot_accept(monkeypatch, catalog_preflight):
    module, builder, _ = catalog_preflight
    monkeypatch.setattr(builder, "release_highlights", lambda version: ["entry"] * 33)
    with pytest.raises(ValueError, match="highlights"):
        module.validate_inputs(ROOT, builder=builder)


def test_preflight_rejects_missing_catalog_predecessor(monkeypatch, catalog_preflight):
    module, builder, _ = catalog_preflight
    monkeypatch.setattr(builder, "load_catalog_history", lambda version: [])
    with pytest.raises(ValueError, match=r"previous release 1\.6\.8"):
        module.validate_inputs(ROOT, builder=builder)


def test_preflight_rejects_refresh_without_image_runtime_changes(monkeypatch, catalog_preflight):
    module, builder, _ = catalog_preflight
    monkeypatch.setattr(builder, "require_previous_catalog_release", lambda *args, **kwargs: "HEAD")
    config_path = ROOT / "scripts/release/release-config.json"
    read_text = Path.read_text

    def read_refresh_config(path, *args, **kwargs):
        raw = read_text(path, *args, **kwargs)
        if path != config_path:
            return raw
        config = json.loads(raw)
        config["delivery_mode"] = "app_update_with_image_refresh"
        return json.dumps(config)

    monkeypatch.setattr(Path, "read_text", read_refresh_config)
    with pytest.raises(ValueError, match="no image-runtime paths changed"):
        module.validate_inputs(ROOT, builder=builder)


def test_manual_validation_runs_real_preparation_without_publication():
    workflow = yaml.load((ROOT / ".github/workflows/docker-publish.yml").read_text(), Loader=yaml.BaseLoader)
    assert "workflow_dispatch" in workflow["on"]
    jobs = workflow["jobs"]
    for name in ("release-preflight", "runtime-dependencies", "candidate-source-gates", "prepare-runtime-archives"):
        job = jobs[name]
        assert "github.event_name == 'workflow_dispatch'" in job["if"]
        assert "github.ref == 'refs/heads/main'" in job["if"]
        assert "github.actor == 'CoderLuii'" in job["if"]
        assert job["permissions"] == {"contents": "read"}
        assert "secrets." not in str(job)
    preflight = jobs["release-preflight"]["steps"]
    for step in preflight:
        if "verify-local-qualification.py" in step.get("run", "") or step.get("name") == "Retain accepted local qualification":
            assert step["if"] == "github.event_name == 'push'"
    native = next(s for s in jobs["runtime-dependencies"]["steps"] if "docker run" in s.get("run", ""))
    assert "diff -ru" in native["run"]
    assert "--is-ancestor" not in str(native)
    for name in ("candidate-signing", "candidate-qualification", "signed-candidate", "native-image-qualification", "build-update-bundle-and-release", "publish-github-release", "sync-site", "advance-compatible-aliases"):
        assert jobs[name]["if"] == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
    assert "prepare-runtime-archives" in jobs["release-validation"]["needs"]
    assert "secrets." not in str(jobs["release-validation"])


@pytest.mark.parametrize("event,ref,actor,prepare,publish", [
    ("workflow_dispatch", "refs/heads/main", "CoderLuii", True, False),
    ("workflow_dispatch", "refs/tags/v1.6.5", "CoderLuii", False, False),
    ("workflow_dispatch", "refs/heads/main", "another-user", False, False),
    ("workflow_dispatch", "refs/heads/another-branch", "CoderLuii", False, False),
    ("push", "refs/tags/v1.6.6", "CoderLuii", True, True),
    ("pull_request", "refs/pull/33/merge", "CoderLuii", False, False),
])
def test_manual_event_cannot_enter_the_production_release_path(event, ref, actor, prepare, publish):
    from types import SimpleNamespace
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())["jobs"]
    context = SimpleNamespace(event_name=event, ref=ref, actor=actor)
    for name, expected in (("release-preflight", prepare), ("runtime-dependencies", prepare),
                           ("candidate-source-gates", prepare), ("prepare-runtime-archives", prepare),
                           ("candidate-signing", publish), ("signed-candidate", publish),
                           ("native-image-qualification", publish), ("publish-github-release", publish),
                           ("sync-site", publish), ("advance-compatible-aliases", publish)):
        expression = jobs[name]["if"].replace("&&", " and ").replace("||", " or ")
        result = eval(expression, {"__builtins__": {}}, {"github": context, "startsWith": lambda value, prefix: value.startswith(prefix)})
        assert result is expected, (name, event, ref)


def test_hosted_measurements_fetch_the_release_history_the_verifier_requires():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())
    job = workflow["jobs"]["release-validation"]
    checkout = next(step for step in job["steps"] if step.get("name") == "Checkout measured source")
    assert checkout["with"]["fetch-depth"] == 0


def test_hosted_measurements_retain_reports_without_uploading_virtualenvs():
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())["jobs"]
    upload = next(step for step in jobs["release-validation"]["steps"] if step.get("name") == "Retain hosted measurements and install logs")
    assert upload["with"]["path"].splitlines() == [
        "${{ runner.temp }}/verifier-benchmark/*.json",
        "${{ runner.temp }}/verifier-benchmark/*.log",
    ]
