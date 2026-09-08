"""Public intake must not request private diagnostics or advertise stale support."""
import json
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[3]

def test_public_forms_explain_metadata_risk_and_keep_config_optional():
    for name,field in [('bug_report.yml','compose_snippet'),('question.yml','config_snippet')]:
        form=yaml.safe_load((ROOT/'.github/ISSUE_TEMPLATE'/name).read_text())
        text=' '.join(item.get('attributes',{}).get('value','') for item in form['body'])
        assert 'public issue' in text
        for term in ('DVR','client','content','hostnames','IP addresses','backups','encryption keys','Report a Problem','private'):
            assert term in text
        config=next(item for item in form['body'] if item.get('id')==field)
        assert config['validations']['required'] is False
    support=(ROOT/'.github/SUPPORT.md').read_text()
    assert 'even sanitized debug bundles' in support
    assert 'private support path' in support

def test_security_support_line_matches_release_configuration():
    config=json.loads((ROOT/'scripts/release/release-config.json').read_text())
    line='.'.join(config['version'].split('.')[:2])+'.x'
    policy=(ROOT/'.github/SECURITY.md').read_text()
    supported=[row for row in policy.splitlines() if row.endswith('| Yes |')]
    assert supported==[f'| {line} | Yes |']
    assert 'https://github.com/CoderLuii/ChannelWatch/security/advisories/new' in policy
