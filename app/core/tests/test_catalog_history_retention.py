"""Catalog publication keeps previous compatible choices available."""
import copy
import importlib.util
import json
from pathlib import Path
import pytest
from core.update_catalog import select_catalog_release

ROOT = Path(__file__).resolve().parents[3]

def builder():
    spec = importlib.util.spec_from_file_location('retention_builder', ROOT / 'scripts/release/build-update-bundle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_predecessor_is_required_and_missing_tag_inventory_fails_closed():
    module = builder()
    history = [{'version':'0.9.18'}, {'version':'1.0.8'}]
    with pytest.raises(ValueError, match='previous release 1.0.9'):
        module.require_previous_catalog_release('1.1.0', history, ['v1.0.8','v1.0.9','v1.1.0'])
    with pytest.raises(ValueError, match='complete release tags'):
        module.require_previous_catalog_release('1.1.0', history, [])
    module.require_previous_catalog_release('1.1.0', history + [{'version':'1.0.9'}], ['v1.0.9','v1.1.0'])

@pytest.mark.parametrize('unavailable', ['revoked', 'incompatible'])
def test_retained_catalog_falls_back_to_highest_compatible_release(unavailable):
    current=json.loads((ROOT/'scripts/release/release-config.json').read_text())['version']
    history=builder().load_catalog_history(current)
    assert {e['version'] for e in history} >= {'0.9.18', *[f'1.0.{i}' for i in range(10)]}
    history=copy.deepcopy(history)
    if unavailable=='revoked':
        history[0]['revocation_state']='revoked'
    else:
        history[0]['compatible_launcher_protocols']=[]
    selection=select_catalog_release({'payload':{'releases':history}},current_version='1.0.0',runtime_abi='channelwatch-runtime-v1',settings_schema_version=7,launcher_protocol=3)
    assert selection.release['version']=='1.1.0'
