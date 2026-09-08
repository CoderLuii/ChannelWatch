"""Exercise label maintenance with stub gh calls, never a real repository."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

ROOT=Path(__file__).resolve().parents[3]

def run_helper(tmp_path, *, without_yaml=False, labels=None, fail_name=''):
    scripts=tmp_path/'scripts';scripts.mkdir()
    shutil.copy2(ROOT/'.github/create-labels.sh',scripts/'create-labels.sh')
    (scripts/'labels.yml').write_text(yaml.safe_dump(labels if labels is not None else [{'name':'sample','color':'abcdef','description':'Example'}]))
    binaries=tmp_path/'bin';binaries.mkdir()
    (binaries/'python3').write_text(f'#!/bin/sh\nexec "{sys.executable}" '+('-S ' if without_yaml else '')+'"$@"\n')
    calls=tmp_path/'calls.jsonl'
    (binaries/'gh').write_text(f'''#!{sys.executable}
import json,sys
with open({str(calls)!r},'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
if {fail_name!r} and {fail_name!r} in sys.argv:
 print('already exists; update failed',file=sys.stderr)
 sys.exit(1)
''')
    for path in binaries.iterdir():path.chmod(0o700)
    result=subprocess.run(['bash',str(scripts/'create-labels.sh'),'fixture/project'],env={**os.environ,'PATH':str(binaries)+':'+os.environ['PATH']},capture_output=True,text=True)
    operations=[json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    return result,operations

def test_missing_yaml_fails_explicitly_without_claiming_zero_work_success(tmp_path):
    result,calls=run_helper(tmp_path,without_yaml=True)
    assert result.returncode!=0
    assert 'PyYAML' in result.stderr
    assert not calls

def test_empty_taxonomy_is_an_error_before_any_gh_call(tmp_path):
    result,calls=run_helper(tmp_path,labels=[])
    assert result.returncode!=0
    assert not calls

def test_valid_taxonomy_applies_every_label_with_force(tmp_path):
    labels=yaml.safe_load((ROOT/'.github/labels.yml').read_text())
    result,calls=run_helper(tmp_path,labels=labels)
    assert result.returncode==0,result.stderr
    assert [call[2] for call in calls]==[label['name'] for label in labels]
    assert all(call[:2]==['label','create'] and '--force' in call for call in calls)
    assert f'{len(labels)} applied' in result.stdout

def test_failed_update_returns_nonzero_instead_of_ignoring_edit_failure(tmp_path):
    result,calls=run_helper(tmp_path,labels=[{'name':'broken','color':'abcdef'}],fail_name='broken')
    assert result.returncode!=0
    assert len(calls)==1  # --force already performs an upsert; no unchecked fallback.
    assert '1 errors' in result.stdout

def test_forms_use_canonical_labels_and_security_questions_have_no_priority():
    names={label['name'] for label in yaml.safe_load((ROOT/'.github/labels.yml').read_text())}
    for path in (ROOT/'.github/ISSUE_TEMPLATE').iterdir():
        source=path.read_text()
        if path.suffix=='.md':source=source.split('---',2)[1]
        form=yaml.safe_load(source)
        labels=form.get('labels',[])
        if isinstance(labels,str):labels=[item.strip() for item in labels.split(',')]
        assert set(labels)<=names,(path.name,labels)
    security=yaml.safe_load((ROOT/'.github/ISSUE_TEMPLATE/security.yml').read_text())
    assert security['name']=='Security Question'
    assert security['labels']==['type/security']
    assert 'security/advisories/new' in str(security)
