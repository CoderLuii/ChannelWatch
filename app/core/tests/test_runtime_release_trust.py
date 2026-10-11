"""Signed catalog retention must not turn mutable markers into authority."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from core.runtime_release_trust import (
    delete_retained_runtime_catalog,
    persist_runtime_catalog,
    verify_retained_runtime_catalog,
)
from core.tests.test_runtime_catalog import _resign, _runtime_catalog
from core.tests.test_update_manager import _key_pair
from core.update_center import UpdateBundleError, UpdateManifestError


@pytest.fixture
def trust(tmp_path):
    private, public = _key_pair()
    raw = _runtime_catalog(private)
    descriptor = raw["payload"]["releases"][0]["runtime_variants"][0]["runtime_descriptor"]
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    return runtime, private, public, raw, descriptor


def _bytes(raw):
    return json.dumps(raw).encode()


def test_retains_exact_signed_bytes_and_private_mode(trust):
    runtime, _, public, raw, descriptor = trust
    data = _bytes(raw)
    digest = persist_runtime_catalog(runtime, descriptor, data, public)
    path = runtime / "trust" / f"{descriptor['generation_id']}.json"
    assert path.read_bytes() == data
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_nlink == 1
    assert digest == hashlib.sha256(data).hexdigest()
    assert verify_retained_runtime_catalog(runtime, descriptor, public) == digest


def test_entrypoint_permission_pass_preserves_existing_runtime_trust(trust):
    from core.tests.test_supervisor_creds import _load_entrypoint

    runtime, _, public, raw, descriptor = trust
    data = _bytes(raw)
    digest = persist_runtime_catalog(runtime, descriptor, data, public)
    entrypoint = _load_entrypoint()

    entrypoint.chmod_config_tree(
        runtime,
        trusted_runtime_uid=os.getuid(),
        trusted_runtime_root=runtime,
    )

    assert persist_runtime_catalog(runtime, descriptor, data, public) == digest
    assert verify_retained_runtime_catalog(runtime, descriptor, public) == digest


def test_retains_signed_runtime_catalog_above_legacy_limit(trust):
    from core.update_center import MAX_MANIFEST_BYTES

    runtime, _, public, raw, descriptor = trust
    data = _bytes(raw).ljust(MAX_MANIFEST_BYTES + 128, b" ")
    digest = persist_runtime_catalog(runtime, descriptor, data, public)
    assert verify_retained_runtime_catalog(runtime, descriptor, public) == digest


def test_deletes_only_authenticated_retained_record(trust):
    runtime, _, public, raw, descriptor = trust
    persist_runtime_catalog(runtime, descriptor, _bytes(raw), public)
    unrelated = runtime / "trust/unrelated.json"
    unrelated.write_bytes(b"preserved")
    delete_retained_runtime_catalog(runtime, descriptor, public)
    assert not (runtime / "trust" / f"{descriptor['generation_id']}.json").exists()
    assert unrelated.read_bytes() == b"preserved"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode", "signature"])
def test_unsafe_retained_record_is_never_deleted(trust, tmp_path, kind):
    runtime, _, public, raw, descriptor = trust
    persist_runtime_catalog(runtime, descriptor, _bytes(raw), public)
    path = runtime / "trust" / f"{descriptor['generation_id']}.json"
    if kind == "symlink":
        other = tmp_path / "outside.json"
        path.rename(other)
        path.symlink_to(other)
    elif kind == "hardlink":
        os.link(path, tmp_path / "alias.json")
    elif kind == "mode":
        path.chmod(0o644)
    else:
        raw["payload"]["releases"][0]["highlights"] = ["tampered"]
        path.write_bytes(_bytes(raw))
    with pytest.raises((UpdateBundleError, UpdateManifestError)):
        delete_retained_runtime_catalog(runtime, descriptor, public)
    assert path.exists()


def test_changed_record_is_not_unlinked_after_verification(trust, monkeypatch):
    from core import runtime_release_trust

    runtime, _, public, raw, descriptor = trust
    persist_runtime_catalog(runtime, descriptor, _bytes(raw), public)
    path = runtime / "trust" / f"{descriptor['generation_id']}.json"
    verify = runtime_release_trust._variant

    def replace_after_verification(*args):
        result = verify(*args)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(path)
        return result

    monkeypatch.setattr(runtime_release_trust, "_variant", replace_after_verification)
    with pytest.raises(UpdateBundleError, match="changed before deletion"):
        delete_retained_runtime_catalog(runtime, descriptor, public)
    assert path.exists()


def test_retry_retains_original_signed_envelope_when_history_advances(trust):
    runtime, private, public, raw, descriptor = trust
    original = persist_runtime_catalog(runtime, descriptor, _bytes(raw), public)
    raw["payload"]["publication_time"] = "2026-10-13T00:00:00Z"
    _resign(raw, private)
    assert persist_runtime_catalog(runtime, descriptor, _bytes(raw), public) == original


@pytest.mark.parametrize("mutation", ["signature", "key", "descriptor", "duplicate"])
def test_untrusted_or_ambiguous_envelope_never_creates_record(trust, mutation):
    runtime, private, public, raw, descriptor = trust
    if mutation == "signature":
        raw["payload"]["releases"][0]["highlights"] = ["tampered"]
    elif mutation == "key":
        public = {}
    elif mutation == "descriptor":
        descriptor = dict(descriptor, source_commit="f" * 40)
    else:
        variants = raw["payload"]["releases"][0]["runtime_variants"]
        variants.append(variants[0])
        _resign(raw, private)
    with pytest.raises(UpdateManifestError):
        persist_runtime_catalog(runtime, descriptor, _bytes(raw), public)
    assert not (runtime / "trust").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode", "tamper"])
def test_changed_retained_record_is_rejected(trust, tmp_path, kind):
    runtime, _, public, raw, descriptor = trust
    data = _bytes(raw)
    persist_runtime_catalog(runtime, descriptor, data, public)
    path = runtime / "trust" / f"{descriptor['generation_id']}.json"
    if kind == "symlink":
        other = tmp_path / "outside.json"
        path.rename(other)
        path.symlink_to(other)
    elif kind == "hardlink":
        os.link(path, tmp_path / "alias.json")
    elif kind == "mode":
        path.chmod(0o644)
    else:
        raw["payload"]["releases"][0]["highlights"] = ["tampered"]
        path.write_bytes(_bytes(raw))
    with pytest.raises((UpdateBundleError, UpdateManifestError)):
        verify_retained_runtime_catalog(runtime, descriptor, public)
    with pytest.raises((UpdateBundleError, UpdateManifestError)):
        persist_runtime_catalog(runtime, descriptor, data, public)


def test_rejects_symlink_runtime_root(trust, tmp_path):
    runtime, _, public, raw, descriptor = trust
    link = tmp_path / "runtime-link"
    link.symlink_to(runtime, target_is_directory=True)
    with pytest.raises(UpdateBundleError, match="Runtime directory"):
        persist_runtime_catalog(link, descriptor, _bytes(raw), public)
    assert not (runtime / "trust").exists()


def test_failed_atomic_publication_leaves_no_record_or_temporary_file(trust, monkeypatch):
    runtime, _, public, raw, descriptor = trust

    def fail(*args, **kwargs):
        raise OSError("disposable storage full")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError, match="full"):
        persist_runtime_catalog(runtime, descriptor, _bytes(raw), public)
    assert list((runtime / "trust").iterdir()) == []


def test_bounded_record_rejected_before_publication(trust):
    from core.update_center import MAX_RUNTIME_CATALOG_BYTES

    runtime, _, public, _, descriptor = trust
    with pytest.raises(UpdateManifestError, match="size limit"):
        persist_runtime_catalog(runtime, descriptor, b" " * (MAX_RUNTIME_CATALOG_BYTES + 1), public)
    assert not (runtime / "trust").exists()
