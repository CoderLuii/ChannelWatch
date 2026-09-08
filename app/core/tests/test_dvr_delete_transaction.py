"""Exercise permanent deletion at the real locked settings-save boundary."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from core.tests.test_dvr_soft_delete import _api_settings_file, _dvr, _history_file


def _configured(tmp_path):
    config = tmp_path / 'config'
    settings = _api_settings_file(config, [_dvr()])
    state = config / 'session_state_dvr_aaa11111.json'
    state.write_text('{"sessions": []}')
    history = _history_file(config, [{'id': 'event', 'dvr_id': 'dvr_aaa11111'}])
    return config, settings, state, history

@pytest.mark.asyncio
async def test_settings_save_failure_preserves_state_and_history(tmp_path):
    from ui.backend import main
    config, settings, state, history = _configured(tmp_path)
    before = (settings.read_bytes(), state.read_bytes(), history.read_bytes())
    with (
        patch('ui.backend.config.CONFIG_FILE', settings),
        patch('ui.backend.config.CONFIG_DIR', config),
        patch.object(main, '_CORE_CONFIG_DIR', config),
        patch('ui.backend.config.save_settings', side_effect=OSError('save failed')),
    ):
        with pytest.raises(OSError):
            await main.hard_delete_dvr_endpoint('dvr_aaa11111')
    assert state.exists() and history.exists()
    assert (settings.read_bytes(), state.read_bytes(), history.read_bytes()) == before

@pytest.mark.asyncio
async def test_purge_failure_is_reported_and_restart_can_finish(tmp_path):
    from ui.backend import main
    from core.helpers import soft_delete_manager as deletion
    config, settings, state, history = _configured(tmp_path)
    with (
        patch('ui.backend.config.CONFIG_FILE', settings),
        patch('ui.backend.config.CONFIG_DIR', config),
        patch.object(main, '_CORE_CONFIG_DIR', config),
        patch.object(main, '_signal_core_hot_reload', return_value=True),
    ):
        with patch.object(deletion, 'delete_dvr_activity', side_effect=OSError('purge failed')):
            with pytest.raises(OSError):
                await main.hard_delete_dvr_endpoint('dvr_aaa11111')
        assert (config / 'pending-dvr-deletions.json').exists()
        # Simulate startup replay using the durable settings generation.
        deletion.complete_pending_dvr_deletions(config, json.loads(settings.read_text())['dvr_servers'])
    assert not state.exists()
    assert json.loads(history.read_text()) == []
    assert not (config / 'pending-dvr-deletions.json').exists()

@pytest.mark.asyncio
@pytest.mark.parametrize('failure_point', ['intent_write', 'state_unlink', 'journal_write'])
async def test_deletion_failures_preserve_or_replay_intent(tmp_path, failure_point):
    from ui.backend import main
    from core.helpers import soft_delete_manager as deletion
    from core.storage import activity_store
    config, settings, state, history = _configured(tmp_path)
    target, method = {
        'intent_write': (deletion, 'atomic_write_private_json'),
        'state_unlink': (deletion, '_remove_dvr_state_files'),
        'journal_write': (activity_store, '_write_journal'),
    }[failure_point]
    with (
        patch('ui.backend.config.CONFIG_FILE', settings),
        patch('ui.backend.config.CONFIG_DIR', config),
        patch.object(main, '_CORE_CONFIG_DIR', config),
        patch.object(main, '_signal_core_hot_reload', return_value=True),
    ):
        with patch.object(target, method, side_effect=OSError('injected I/O failure')):
            with pytest.raises(OSError):
                await main.hard_delete_dvr_endpoint('dvr_aaa11111')
        servers = json.loads(settings.read_text())['dvr_servers']
        if failure_point == 'intent_write':
            assert servers and state.exists()
            assert json.loads(history.read_text())
        else:
            assert servers == []
            assert (config / 'pending-dvr-deletions.json').exists()
            deletion.complete_pending_dvr_deletions(config, servers)
            assert not state.exists()
            assert json.loads(history.read_text()) == []


def test_uncommitted_intent_is_cancelled_without_data_loss(tmp_path):
    from core.helpers import soft_delete_manager as deletion
    config, settings, state, history = _configured(tmp_path)
    before = (state.read_bytes(), history.read_bytes())
    deletion.prepare_dvr_deletions(config, ['dvr_aaa11111'])
    deletion.complete_pending_dvr_deletions(config, json.loads(settings.read_text())['dvr_servers'])
    assert (state.read_bytes(), history.read_bytes()) == before
    assert not (config / 'pending-dvr-deletions.json').exists()


def test_committed_intent_survives_process_loss_before_purge(tmp_path):
    from core.helpers import soft_delete_manager as deletion
    config, settings, state, history = _configured(tmp_path)
    deletion.prepare_dvr_deletions(config, ['dvr_aaa11111'])
    payload = json.loads(settings.read_text())
    payload['dvr_servers'] = []
    settings.write_text(json.dumps(payload))
    deletion.complete_pending_dvr_deletions(config, json.loads(settings.read_text())['dvr_servers'])
    assert not state.exists()
    assert json.loads(history.read_text()) == []
