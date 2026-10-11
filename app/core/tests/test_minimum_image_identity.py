from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[3] / "scripts/release/verify-minimum-image-app-update.py"
    spec = importlib.util.spec_from_file_location("minimum_image_identity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_minimum_image_uses_platform_digest_from_exact_index(monkeypatch):
    module = _module()
    commands = []
    index = "sha256:" + "a" * 64
    platform = "sha256:" + "b" * 64

    def run(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({"manifests": [
            {"digest": platform, "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": "sha256:" + "c" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
        ]}), "")

    monkeypatch.setattr(module, "run", run)
    assert module.pinned_image("coderluii/channelwatch", index) == (
        "coderluii/channelwatch@" + platform, platform,
    )
    assert commands == [["docker", "buildx", "imagetools", "inspect", "--raw",
                         "coderluii/channelwatch@" + index]]


@pytest.mark.parametrize("digest", ["1.4.0", "latest", "sha256:" + "a" * 63])
def test_minimum_image_rejects_mutable_or_invalid_identity(digest):
    module = _module()
    with pytest.raises(module.CanaryError, match="immutable index digest"):
        module.pinned_image("coderluii/channelwatch", digest)


@pytest.mark.parametrize("changes", [
    {"Architecture": "arm64"}, {"Os": "windows"}, {"RepoDigests": []}, {"Id": "latest"},
])
def test_minimum_image_checks_actual_pulled_platform_and_identity(monkeypatch, changes):
    module = _module()
    image = "coderluii/channelwatch@sha256:" + "b" * 64
    value = {"Os": "linux", "Architecture": "amd64", "RepoDigests": [image],
             "Id": "sha256:" + "c" * 64, **changes}
    monkeypatch.setattr(module, "run", lambda command: subprocess.CompletedProcess(
        command, 0, json.dumps([value]), "",
    ))
    with pytest.raises(module.CanaryError, match="pinned platform"):
        module.verify_pulled_image(image)


def test_minimum_image_accepts_exact_pulled_platform(monkeypatch):
    module = _module()
    image = "coderluii/channelwatch@sha256:" + "b" * 64
    identity = "sha256:" + "c" * 64
    monkeypatch.setattr(module, "run", lambda command: subprocess.CompletedProcess(
        command, 0, json.dumps([{"Os": "linux", "Architecture": "amd64",
                                 "RepoDigests": [image], "Id": identity}]), "",
    ))
    assert module.verify_pulled_image(image) == identity
