"""The tag publication dependency graph must enforce nonpublishing checks."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_tag_candidate_requires_existing_deployment_docs_and_security_gates():
    workflow = yaml.load((ROOT / '.github/workflows/docker-publish.yml').read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    assert set(jobs['candidate-qualification']['needs']) >= {'docs-config', 'helm', 'security'}
    assert set(jobs['signed-candidate']['needs']) >= {'candidate-qualification', 'candidate-signing'}
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

def test_browser_diagnostics_and_local_release_evidence_precede_signing():
    workflow = yaml.load((ROOT / '.github/workflows/docker-publish.yml').read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    steps = jobs['browser']['steps']
    uploads = [step for step in steps if 'browser diagnostics' in step.get('name', '')]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload['if'] == 'always()'
    assert upload['with']['retention-days'] == '7'
    assert upload['with']['path'].splitlines() == ['app/ui/playwright-report/', 'app/ui/test-results/']
    browser = next(i for i, step in enumerate(steps) if 'pnpm exec playwright test' in step.get('run', ''))
    assert steps.index(upload) > browser

    preflight = jobs['release-preflight']['steps']
    verification = next(i for i, step in enumerate(preflight)
                        if 'verify-local-qualification.py' in step.get('run', ''))
    retained = next(i for i, step in enumerate(preflight)
                    if step.get('name') == 'Retain accepted local qualification')
    assert retained > verification
    assert preflight[retained]['with']['path'].splitlines() == [
        '${{ runner.temp }}/local-qualification.json',
        '${{ runner.temp }}/local-qualification.json.sha256',
    ]
    assert jobs['runtime-dependencies']['needs'] == 'release-preflight'
    assert jobs['candidate-source-gates']['needs'] == 'runtime-dependencies'
    assert not any('pnpm exec playwright test' in step.get('run', '')
                   for step in jobs['candidate-source-gates']['steps'])

    assert 'candidate-source-gates' in jobs['candidate-signing']['needs']
    assert 'candidate-source-gates' in jobs['candidate-qualification']['needs']
    assert jobs['candidate-source-gates']['permissions'] == {'contents': 'read'}
    assert 'secrets.' not in str(jobs['candidate-source-gates'])
    assert 'CHANNELWATCH_UPDATE_SIGNING_KEY' not in str(jobs['candidate-source-gates'])
    assert any('CHANNELWATCH_UPDATE_SIGNING_KEY' in str(step.get('env', {}))
               for step in jobs['candidate-signing']['steps'])
    assert not any('pnpm exec playwright test' in step.get('run', '')
                   for step in jobs['candidate-qualification']['steps'])
