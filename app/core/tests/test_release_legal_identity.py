"""Release identity and documented rebuild arguments must follow the source tag."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('render_release_legal', ROOT / 'scripts/release/render_release_legal.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


@pytest.mark.parametrize('version', ['1.1.0', '1.1.1'])
def test_legal_identity_and_rebuild_use_the_checked_out_release(tmp_path, version):
    output = tmp_path / 'legal'
    renderer.render_files(ROOT / 'docs/legal', output, version)
    for name in renderer.DOCUMENTS:
        document = (output / name).read_text()
        assert document.startswith(f'# ChannelWatch v{version} - ')
        assert 'v0.9.18' not in document
        assert document.split('\n', 1)[1] == (ROOT / 'docs/legal' / name).read_text().split('\n', 1)[1]
    source = (output / 'CORRESPONDING_SOURCE.md').read_text()
    command = re.search(r'```sh\n(.*?)```', source, re.S)[1]
    config_dir = tmp_path / 'scripts/release'
    config_dir.mkdir(parents=True)
    (config_dir / 'release-config.json').write_text(json.dumps({'version': version}))
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    capture = tmp_path / 'docker-args.json'
    (binaries / 'docker').write_text(f'#!{sys.executable}\nimport json,sys\nopen({str(capture)!r},"w").write(json.dumps(sys.argv[1:]))\n')
    (binaries / 'docker').chmod(0o700)
    (binaries / 'git').write_text('#!/bin/sh\nprintf "%s\\n" 0000000000000000000000000000000000000000\n')
    (binaries / 'git').chmod(0o700)
    subprocess.run(['bash', '-eu', '-c', command], cwd=tmp_path, env={**os.environ, 'PATH': str(binaries) + ':' + os.environ['PATH']}, check=True)
    arguments = json.loads(capture.read_text())
    assert f'VERSION={version}' in arguments
    assert f'type=oci,dest=channelwatch-v{version}.oci' in arguments
