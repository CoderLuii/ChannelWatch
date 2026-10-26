"""Release preparation rejects stale windows and inconsistent public dates."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/verify-release-timing.py"


class ReleaseTimingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {
            "version": "1.6.1",
            "publication_time": "2026-10-26T04:00:00Z",
            "automatic_install_after": "2026-11-02T04:00:00Z",
            "automatic_install_allowed": False,
            "release_heading": "## 10/26/2026",
        }
        self.vex = {"timestamp": "2026-10-26T04:00:00Z"}
        self.changelog = "# Changelog\n\n## [1.6.1] - 2026-10-26\n"

    def run_gate(self, *arguments):
        for relative, value in (
            ("scripts/release/release-config.json", self.config),
            ("deploy/security/channelwatch-v1.6.1.openvex.json", self.vex),
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        changelog = self.root / "docs/releases/CHANGELOG.md"
        changelog.parent.mkdir(parents=True, exist_ok=True)
        changelog.write_text(self.changelog, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *arguments],
            capture_output=True, text=True, check=False,
        )

    def test_consistent_upcoming_window_passes(self):
        result = self.run_gate("--candidate-created-at", "2026-10-25T12:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_window_cannot_precede_actual_candidate_creation(self):
        result = self.run_gate("--candidate-created-at", "2026-10-26T04:00:01Z")
        self.assertEqual(result.returncode, 1)
        self.assertIn("candidate creation", result.stderr)

    def test_public_dates_and_manual_install_delay_cannot_drift(self):
        mutations = (
            ("heading", lambda: self.config.update(release_heading="## 10/25/2026")),
            ("VEX", lambda: self.vex.update(timestamp="2026-10-25T04:00:00Z")),
            ("seven-day", lambda: self.config.update(automatic_install_after="2026-11-01T04:00:00Z")),
            ("disabled", lambda: self.config.update(automatic_install_allowed=True)),
        )
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                original_config, original_vex = self.config.copy(), self.vex.copy()
                mutate()
                result = self.run_gate("--candidate-created-at", "2026-10-25T12:00:00Z")
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.config, self.vex = original_config, original_vex

    def test_changelog_must_contain_one_matching_publication_date(self):
        self.changelog = "## [1.6.1] - 2026-10-25\n"
        result = self.run_gate("--candidate-created-at", "2026-10-25T12:00:00Z")
        self.assertEqual(result.returncode, 1)
        self.assertIn("changelog", result.stderr)

    def test_timezone_is_required_for_candidate_creation(self):
        result = self.run_gate("--candidate-created-at", "2026-10-25T12:00:00")
        self.assertEqual(result.returncode, 1)
        self.assertIn("timezone", result.stderr)

    def test_preparation_previews_synchronized_metadata_without_writing(self):
        result = self.run_gate(
            "--candidate-created-at", "2026-10-25T12:00:00Z",
            "--prepare-publication-time", "2026-10-25T12:00:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["publication_time"], "2026-10-25T12:00:00Z")
        self.assertEqual(proposal["automatic_install_after"], "2026-11-01T12:00:00Z")
        self.assertFalse(proposal["written"])
        actual = json.loads((self.root / "scripts/release/release-config.json").read_text())
        self.assertEqual(actual, self.config)

    def test_deliberate_preparation_updates_all_dates_then_passes_the_gate(self):
        result = self.run_gate(
            "--candidate-created-at", "2026-10-25T12:00:00Z",
            "--prepare-publication-time", "2026-10-25T12:00:00Z", "--write",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["written"])
        config = json.loads((self.root / "scripts/release/release-config.json").read_text())
        vex = json.loads((self.root / "deploy/security/channelwatch-v1.6.1.openvex.json").read_text())
        changelog = (self.root / "docs/releases/CHANGELOG.md").read_text()
        self.assertEqual(config["publication_time"], "2026-10-25T12:00:00Z")
        self.assertEqual(config["automatic_install_after"], "2026-11-01T12:00:00Z")
        self.assertEqual(config["release_heading"], "## 10/25/2026")
        self.assertFalse(config["automatic_install_allowed"])
        self.assertEqual(vex["timestamp"], config["publication_time"])
        self.assertIn("## [1.6.1] - 2026-10-25", changelog)

    def test_invalid_preparation_cannot_partially_write_metadata(self):
        result = self.run_gate(
            "--candidate-created-at", "2026-10-25T12:00:01Z",
            "--prepare-publication-time", "2026-10-25T12:00:00Z", "--write",
        )
        self.assertEqual(result.returncode, 1)
        actual = json.loads((self.root / "scripts/release/release-config.json").read_text())
        self.assertEqual(actual, self.config)

    def test_write_requires_an_explicit_publication_anchor(self):
        result = self.run_gate("--write")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--prepare-publication-time", result.stderr)

    def test_duplicate_changelog_headings_cannot_be_prepared(self):
        self.changelog += self.changelog
        result = self.run_gate(
            "--candidate-created-at", "2026-10-25T12:00:00Z",
            "--prepare-publication-time", "2026-10-25T12:00:00Z", "--write",
        )
        self.assertEqual(result.returncode, 1)
        actual = json.loads((self.root / "scripts/release/release-config.json").read_text())
        self.assertEqual(actual, self.config)

    def test_default_current_time_rejects_an_old_publication_window(self):
        self.config.update(
            publication_time="2000-01-01T04:00:00Z",
            automatic_install_after="2000-01-08T04:00:00Z",
            release_heading="## 01/01/2000",
        )
        self.vex["timestamp"] = self.config["publication_time"]
        self.changelog = "## [1.6.1] - 2000-01-01\n"
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("candidate creation", result.stderr)


def test_workflow_checks_exact_source_timing_before_native_dependency_build():
    import yaml

    workflow = yaml.load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    steps = workflow["jobs"]["runtime-dependencies"]["steps"]
    build = next(step["run"] for step in steps if "docker pull" in step.get("run", ""))
    gate = 'python3 scripts/release/verify-release-timing.py --source-ref "${GITHUB_SHA}"'
    assert build.index(gate) < build.index("docker pull")
    assert "--candidate-created-at" not in build
    assert "--prepare-publication-time" not in build


if __name__ == "__main__":
    unittest.main()
