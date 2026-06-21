"""Tests for multi-DVR dedup isolation.

Activity history is isolated per DVR, but duplicate suppression still needs
to work within a single DVR stream. These tests pass a shared in-memory
history dictionary into multiple DVR calls and assert that cross-DVR events
remain distinct while rapid same-DVR duplicates are still suppressed.

Fixture usage
-------------
mock_dvr_cluster(count=2) supplies two MockDVR instances with distinct
dvr_id values ("mock_dvr_1", "mock_dvr_2").  The HTTP servers are not
invoked; the fixture is used to obtain realistic dvr_id / dvr_name values
and to keep the test setup mirroring what the real multi-DVR runtime
produces.
"""

import json
from unittest.mock import patch


from core.helpers.activity_recorder import (
    record_activity,
    record_vod_watching,
    record_recording_event,
)


pytest_plugins = ["core.tests.fixtures.mock_dvr_cluster"]


class TestMultiDvrDedupIsolation:
    """Cross-DVR dedup isolation with same-DVR cooldown preserved."""

    def test_same_channel_different_dvr_no_suppression(
        self, mock_dvr_cluster, tmp_path
    ):
        """Channel-5 viewed on DVR-A and DVR-B should each produce a history entry.

        The tracking key must include dvr_id so a shared history dict cannot
        suppress another DVR's event.
        """
        history_file = str(tmp_path / "activity_history.json")
        cluster = mock_dvr_cluster(count=2)
        dvr_a = cluster[0]
        dvr_b = cluster[1]

        shared_history: dict = {}
        with patch("core.helpers.activity_recorder.HISTORY_FILE", history_file):
            record_activity(
                activity_type="watching_channel",
                title="Watching Channel",
                message="Fire Stick watching Channel 5",
                channel_name="Channel 5",
                device_name="Fire Stick",
                dvr_id=dvr_a.dvr_id,
                dvr_name=dvr_a.name,
                notification_history=shared_history,
            )
            record_activity(
                activity_type="watching_channel",
                title="Watching Channel",
                message="Fire Stick watching Channel 5",
                channel_name="Channel 5",
                device_name="Fire Stick",
                dvr_id=dvr_b.dvr_id,
                dvr_name=dvr_b.name,
                notification_history=shared_history,
            )

        history = json.loads((tmp_path / "activity_history.json").read_text())
        assert len(history) == 2, (
            f"Expected 2 history entries (one per DVR), got {len(history)}. "
            f"DVR-B's channel watch was suppressed because the tracking key "
            f"'watching_channel-Channel 5-Fire Stick' does not include dvr_id "
            f"."
        )
        recorded_dvr_ids = {entry["dvr_id"] for entry in history}
        assert dvr_a.dvr_id in recorded_dvr_ids, (
            f"DVR-A ({dvr_a.dvr_id}) entry missing from history"
        )
        assert dvr_b.dvr_id in recorded_dvr_ids, (
            f"DVR-B ({dvr_b.dvr_id}) entry missing from history"
        )

    def test_same_vod_different_dvr_no_suppression(self, mock_dvr_cluster, tmp_path):
        """The same VOD playing on DVR-A and DVR-B should each produce a history entry.

        The tracking key must include dvr_id so a shared history dict cannot
        suppress another DVR's event.
        """
        history_file = str(tmp_path / "activity_history.json")
        cluster = mock_dvr_cluster(count=2)
        dvr_a = cluster[0]
        dvr_b = cluster[1]

        shared_history: dict = {}
        with patch("core.helpers.activity_recorder.HISTORY_FILE", history_file):
            record_vod_watching(
                content_name="The Matrix",
                device_name="AppleTV",
                dvr_id=dvr_a.dvr_id,
                dvr_name=dvr_a.name,
                notification_history=shared_history,
            )
            record_vod_watching(
                content_name="The Matrix",
                device_name="AppleTV",
                dvr_id=dvr_b.dvr_id,
                dvr_name=dvr_b.name,
                notification_history=shared_history,
            )

        history = json.loads((tmp_path / "activity_history.json").read_text())
        assert len(history) == 2, (
            f"Expected 2 history entries (one per DVR), got {len(history)}. "
            f"DVR-B's VOD activity was suppressed because the tracking key "
            f"'watching_vod-The Matrix-AppleTV' does not include dvr_id "
            f"."
        )
        recorded_dvr_ids = {entry["dvr_id"] for entry in history}
        assert dvr_a.dvr_id in recorded_dvr_ids
        assert dvr_b.dvr_id in recorded_dvr_ids

    def test_same_recording_different_dvr_no_suppression(
        self, mock_dvr_cluster, tmp_path
    ):
        """The same recording event on DVR-A and DVR-B should each produce a history entry.

        The tracking key must include dvr_id so a shared history dict cannot
        suppress another DVR's event.
        """
        history_file = str(tmp_path / "activity_history.json")
        cluster = mock_dvr_cluster(count=2)
        dvr_a = cluster[0]
        dvr_b = cluster[1]

        shared_history: dict = {}
        with patch("core.helpers.activity_recorder.HISTORY_FILE", history_file):
            record_recording_event(
                event_type="Recording",
                program_name="Jeopardy!",
                channel_name="Channel 11",
                dvr_id=dvr_a.dvr_id,
                dvr_name=dvr_a.name,
                notification_history=shared_history,
            )
            record_recording_event(
                event_type="Recording",
                program_name="Jeopardy!",
                channel_name="Channel 11",
                dvr_id=dvr_b.dvr_id,
                dvr_name=dvr_b.name,
                notification_history=shared_history,
            )

        history = json.loads((tmp_path / "activity_history.json").read_text())
        assert len(history) == 2, (
            f"Expected 2 history entries (one per DVR), got {len(history)}. "
            f"DVR-B's recording event was suppressed because the tracking key "
            f"'recording_event-Recording-Jeopardy!-Channel 11' does not include "
            f"dvr_id."
        )
        recorded_dvr_ids = {entry["dvr_id"] for entry in history}
        assert dvr_a.dvr_id in recorded_dvr_ids
        assert dvr_b.dvr_id in recorded_dvr_ids

    def test_rapid_duplicate_on_single_dvr_still_suppressed(
        self, mock_dvr_cluster, tmp_path
    ):
        """Rapid duplicate on one DVR must still be suppressed (regression guard).

        This test PASSES before and after: the cooldown window inside a
        single DVR's activity stream must remain active regardless of whether
        the key includes dvr_id.
        """
        history_file = str(tmp_path / "activity_history.json")
        cluster = mock_dvr_cluster(count=1)
        dvr_a = cluster[0]

        shared_history: dict = {}
        with patch("core.helpers.activity_recorder.HISTORY_FILE", history_file):
            record_activity(
                activity_type="watching_channel",
                title="Watching Channel",
                message="Fire Stick watching Channel 5",
                channel_name="Channel 5",
                device_name="Fire Stick",
                dvr_id=dvr_a.dvr_id,
                dvr_name=dvr_a.name,
                notification_history=shared_history,
            )
            record_activity(
                activity_type="watching_channel",
                title="Watching Channel",
                message="Fire Stick watching Channel 5",
                channel_name="Channel 5",
                device_name="Fire Stick",
                dvr_id=dvr_a.dvr_id,
                dvr_name=dvr_a.name,
                notification_history=shared_history,
            )

        history = json.loads((tmp_path / "activity_history.json").read_text())
        assert len(history) == 1, (
            f"Expected 1 history entry (duplicate suppressed), got {len(history)}. "
            f"Same-DVR dedup cooldown must remain active."
        )
        assert history[0]["dvr_id"] == dvr_a.dvr_id
