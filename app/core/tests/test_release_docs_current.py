"""Current release guidance and historical references must remain navigable."""
import json
from pathlib import Path
import re
import yaml

ROOT=Path(__file__).resolve().parents[3]

def test_every_changelog_release_has_one_reference_and_unreleased_starts_at_current():
    source=(ROOT/'docs/releases/CHANGELOG.md').read_text()
    headings=re.findall(r'^## \[([^\]]+)\]',source,re.M)
    references=re.findall(r'^\[([^\]]+)\]: (\S+)$',source,re.M)
    assert len(references)==len(dict(references))
    assert set(headings)==set(dict(references))
    version=json.loads((ROOT/'scripts/release/release-config.json').read_text())['version']
    assert dict(references)['Unreleased']==f'https://github.com/CoderLuii/ChannelWatch/compare/v{version}...HEAD'
    for current,previous in zip(headings[1:],headings[2:]):
        if tuple(map(int,current.split('.'))) >= (1,0,0):
            assert dict(references)[current]==f'https://github.com/CoderLuii/ChannelWatch/compare/v{previous}...v{current}'

def test_update_guide_has_no_heading_level_jump():
    source=(ROOT/'docs/how-to/update-channelwatch.md').read_text()
    source=re.sub(r'```.*?```','',source,flags=re.S)
    levels=[len(m) for m in re.findall(r'^(#+) ',source,re.M)]
    assert levels[0]==1
    assert all(next_level<=level+1 for level,next_level in zip(levels,levels[1:]))

def test_current_guidance_distinguishes_application_version_from_image_version():
    config=json.loads((ROOT/'scripts/release/release-config.json').read_text())
    guide=(ROOT/'docs/how-to/update-channelwatch.md').read_text()
    assert f"v{config['version']}" in guide.split('## ',1)[0]
    assert f"v{config['minimum_image_version']}" in guide.split('## ',1)[0]
    for name in ('bug_report.yml','question.yml'):
        form=yaml.safe_load((ROOT/'.github/ISSUE_TEMPLATE'/name).read_text())
        version=next(field for field in form['body'] if field.get('id')=='cw_version')['attributes']
        assert config['version'] in version['placeholder']
        assert 'Application version' in version['description']
        assert 'docker inspect' not in version['description']
    support=(ROOT/'.github/SUPPORT.md').read_text()
    assert 'Application version' in support
    assert 'Confirm you\'re on the latest version (`docker pull' not in support
