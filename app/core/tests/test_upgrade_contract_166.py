"""Exact v1.6.6 upgrade surface; historical contracts stay bounded."""
import importlib.util
import json
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[3]
def load(name):
    spec=importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts/release" / name)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
LEGACY=["1.4.0","1.4.1","1.4.5","1.6.0","1.6.1","1.6.2","1.6.3","1.6.4","1.6.5"]
CANONICAL=["1.5.3",*LEGACY[3:]]
@pytest.mark.parametrize("script",["build-update-bundle.py","verify-update-assets.py"])
def test_catalog_schema_contracts_166(script):
    m=load(script)
    assert list(m.LEGACY_PROTOCOL3_APP_UPDATE_CONTRACTS["1.6.6"])==LEGACY
    assert list(m.CANONICAL_PROTOCOL4_APP_UPDATE_CONTRACTS["1.6.6"])==CANONICAL
    assert list(m.LEGACY_PROTOCOL3_APP_UPDATE_CONTRACTS["1.6.5"])==LEGACY[:-1]
def test_real_legacy_config_validator_166():
    m=load("verify-legacy-app-upgrade.py")
    config=json.loads((ROOT/"scripts/release/release-config.json").read_text())
    config.update(version="1.6.6",legacy_app_update_sources=LEGACY)
    assert m.validate_release_config(config,target_version="1.6.6")==LEGACY
    config["legacy_app_update_sources"]=LEGACY[:-1]
    with pytest.raises(m.LegacyAppUpgradeError): m.validate_release_config(config,target_version="1.6.6")
def test_aggregate_and_verifier_contracts_166():
    assert load("aggregate-legacy-app-upgrades.py").LEGACY_SOURCE_CONTRACTS["1.6.6"]==LEGACY
    assert load("aggregate-runtime-source-upgrades.py").SOURCE_CONTRACTS["1.6.6"]==CANONICAL
    m=load("verify-runtime-source-upgrade.py")
    assert list(m.TARGET_SOURCES["1.6.6"])==LEGACY[3:]
    assert "1.6.5" in m.SOURCE_RELEASES

@pytest.mark.parametrize("mode", ["app_update", "app_update_with_image_refresh"])
def test_v166_real_catalog_projection_preserves_canonical_runtime_contract(mode):
    m=load("build-update-bundle.py")
    release={"version":"1.6.6","delivery_mode":mode,"image_required":False,"updater_protocol":4,
             "minimum_image_version":"1.5.3","compatible_source_application_versions":CANONICAL,
             "compatible_launcher_protocols":[4]}
    projected=m.project_current_release_v2(release,LEGACY)
    assert projected["compatible_source_application_versions"]==LEGACY
    assert projected["compatible_launcher_protocols"]==[3]
    assert projected["minimum_image_version"]=="1.4.0"
    assert release["compatible_launcher_protocols"]==[4]
    assert release["compatible_source_application_versions"]==CANONICAL
    with pytest.raises(ValueError,match="exact target contract"):
        m.project_current_release_v2(release,LEGACY[:-1])
    release["compatible_source_application_versions"]=CANONICAL[:-1]
    with pytest.raises(ValueError,match="verified-dependency"):
        m.project_current_release_v2(release,LEGACY)

def test_v166_workflow_retains_both_native_source_receipts_and_exact_downloads():
    import yaml
    jobs=yaml.safe_load((ROOT/".github/workflows/docker-publish.yml").read_text())["jobs"]
    native=jobs["runtime-capability"]
    assert {row["architecture"] for row in native["strategy"]["matrix"]["include"]}=={"amd64","arm64"}
    commands="\n".join(step.get("run", "") for step in native["steps"])
    assert "--source-version 1.6.5" in commands
    assert "7299eb70a90d6059159d91f355517915814d97c4d766b6ed84ad0086f2f8fe6a" in commands
    assert "f360e8c083eec1dc64be7b1c00bb39e8ccac0cc768bd429bc98bfcd3c287696d" in commands
    qualification="\n".join(step.get("run", "") for step in jobs["candidate-qualification"]["steps"])
    for arch in ("amd64","arm64"):
        assert f"1.6.5={arch}=" in qualification

def test_native_closure_shell_keeps_fail_closed_checks_without_argument_xtrace():
    import re
    import subprocess
    import yaml
    jobs=yaml.safe_load((ROOT/".github/workflows/docker-publish.yml").read_text())["jobs"]
    commands="\n".join(step.get("run", "") for step in jobs["runtime-dependencies"]["steps"])
    flags=re.search(r"/bin/sh (-[a-z]+) '",commands).group(1)
    # Execute the production shell flags against representative argv accumulation.
    fixture='set --; i=0; while [ "$i" -lt 100 ]; do set -- "$@" --compatible-wheel-tag "tag-$i"; i=$((i+1)); done; printf "tags=%s\\n" "$#"'
    result=subprocess.run(["/bin/sh",flags,fixture],capture_output=True,text=True)
    assert result.returncode==0, result.stderr
    assert result.stdout.strip()=="tags=200"
    assert result.stderr=="", "Native producer traces the growing argument vector."
    for failure in ("false; echo incorrectly-continued", "echo ${CHANNELWATCH_TEST_UNSET_7FCED?missing}"):
        result=subprocess.run(["/bin/sh",flags,failure],capture_output=True,text=True)
        assert result.returncode!=0
        assert "incorrectly-continued" not in result.stdout
    assert "for build in one two" in commands
    assert "diff -ru" in commands
