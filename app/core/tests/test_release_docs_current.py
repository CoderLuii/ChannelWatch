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


def test_current_guide_discloses_legacy_image_certifi_update_path():
    version = json.loads((ROOT / "scripts/release/release-config.json").read_text())["version"]
    guide = (ROOT / "docs/how-to/update-channelwatch.md").read_text()
    current = guide.split("## Upgrade and recovery notes", 1)[0]
    assert f"install ChannelWatch v{version} directly" in current
    assert "v1.4.x route updates the application and interface while keeping the image's existing Python packages" in current
    assert "receive the certifi security update on v1.4.x" in current
    assert f"replace the container with the v{version} image" in current


def test_vitest_mocker_advisories_are_patched_in_the_locked_toolchain():
    manifest = json.loads((ROOT / "app/ui/package.json").read_text())
    assert manifest["devDependencies"]["vitest"] == "^4.1.12"

    lock = yaml.safe_load((ROOT / "app/ui/pnpm-lock.yaml").read_text())
    assert "@vitest/mocker@4.1.12" in lock["packages"]
    assert "vitest@4.1.12" in lock["packages"]
    assert any(
        key.startswith("@vitest/mocker@4.1.12(")
        for key in lock["snapshots"]
    )
