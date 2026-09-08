"""Targeted deletion must preserve an unreadable recovery journal."""
import json
from unittest.mock import patch
import pytest
from core.storage import activity_store as store

@pytest.mark.parametrize('raw', ['{"partial":', '{}', '[null]', '[{"dvr_id":"other"},42]'])
def test_malformed_targeted_delete_preserves_bytes_before_database_changes(tmp_path, raw):
    journal = tmp_path / 'activity_history.json'
    journal.write_text(raw)
    (tmp_path / 'channelwatch.db').touch()
    with patch.object(store, '_open_engine') as open_engine:
        with pytest.raises(ValueError):
            store.delete_dvr_activity('target', config_dir=tmp_path)
    assert journal.read_text() == raw
    open_engine.assert_not_called()


def test_unreadable_journal_fails_before_database_changes(tmp_path):
    with patch.object(store, '_read_journal', side_effect=PermissionError('read denied')):
        with pytest.raises(PermissionError):
            store.delete_dvr_activity('target', config_dir=tmp_path)
    assert not (tmp_path / 'activity_history.json').exists()


def test_valid_targeted_delete_preserves_other_dvr(tmp_path):
    journal = tmp_path / 'activity_history.json'
    journal.write_text(json.dumps([{'dvr_id': 'target'}, {'dvr_id': 'other'}]))
    assert store.delete_dvr_activity('target', config_dir=tmp_path) == 1
    assert json.loads(journal.read_text()) == [{'dvr_id': 'other'}]
