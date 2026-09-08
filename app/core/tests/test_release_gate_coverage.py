"""The tag publication dependency graph must enforce nonpublishing checks."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_tag_candidate_requires_existing_deployment_docs_and_security_gates():
    workflow = yaml.load((ROOT / '.github/workflows/docker-publish.yml').read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    assert set(jobs['signed-candidate']['needs']) >= {'docs-config', 'helm', 'security'}
    for name in ('docs-config', 'helm', 'security'):
        job = jobs[name]
        assert 'if' not in job  # Both supported events use the same check.
        assert job['permissions'] == {'contents': 'read'}
        checkout = job['steps'][0]
        assert checkout['with']['ref'] == '${{ github.sha }}'
        assert 'secrets.' not in str(job)
    for name in ('signed-candidate', 'build-update-bundle-and-release'):
        assert "startsWith(github.ref, 'refs/tags/v')" in jobs[name]['if']

    assert jobs["build-and-push"]["needs"] == "build-update-bundle-and-release"
    assert "if" not in jobs["build-and-push"]

def test_both_browser_paths_retain_first_attempt_diagnostics_before_signing():
    workflow = yaml.load((ROOT / '.github/workflows/docker-publish.yml').read_text(), Loader=yaml.BaseLoader)
    for name in ('browser', 'signed-candidate'):
        steps = workflow['jobs'][name]['steps']
        uploads = [step for step in steps if 'browser diagnostics' in step.get('name', '')]
        assert len(uploads) == 1
        upload = uploads[0]
        assert upload['if'] == 'always()'
        assert upload['with']['retention-days'] == '7'
        assert upload['with']['path'].splitlines() == ['app/ui/playwright-report/', 'app/ui/test-results/']
        browser = next(i for i, step in enumerate(steps) if 'pnpm exec playwright test' in step.get('run', ''))
        assert steps.index(upload) > browser
        if name == 'signed-candidate':
            signing = next(i for i, step in enumerate(steps) if 'CHANNELWATCH_UPDATE_SIGNING_KEY' in str(step.get('env', {})))
            assert steps.index(upload) < signing
