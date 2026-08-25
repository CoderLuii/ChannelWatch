"""Container-lifetime single-writer protection for a shared /config mount."""

from __future__ import annotations

import errno
import fcntl
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_DIR = Path(__file__).resolve().parents[3]
_ENTRYPOINT = _REPO_DIR / "app" / "core" / "docker-entrypoint.py"


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location(
        "channelwatch_instance_lock_entrypoint",
        _ENTRYPOINT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exec_lock_holder(config_dir: Path) -> subprocess.Popen[str]:
    outer = f"""
import importlib.util
import os
import sys
spec = importlib.util.spec_from_file_location("entrypoint", {str(_ENTRYPOINT)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fd = module.acquire_container_instance_lock(module.Path({str(config_dir)!r}))
os.environ["CHANNELWATCH_TEST_INSTANCE_LOCK_FD"] = str(fd)
inner = '''
import os
import sys
fd = int(os.environ["CHANNELWATCH_TEST_INSTANCE_LOCK_FD"])
metadata = os.fstat(fd)
assert metadata.st_nlink == 1
assert os.get_inheritable(fd)
print("LOCKED_AFTER_EXEC", flush=True)
sys.stdin.buffer.read()
'''
os.execv(sys.executable, [sys.executable, "-u", "-c", inner])
"""
    return subprocess.Popen(
        [sys.executable, "-u", "-c", outer],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
    )


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock and exec")
def test_instance_lock_survives_exec_rejects_second_container_and_releases_cleanly(
    tmp_path,
):
    entrypoint = _load_entrypoint()
    holder = _exec_lock_holder(tmp_path)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED_AFTER_EXEC"

        with pytest.raises(RuntimeError, match="Another ChannelWatch container"):
            entrypoint.acquire_container_instance_lock(tmp_path)
    finally:
        if holder.stdin is not None:
            holder.stdin.close()
        holder.wait(timeout=5)

    assert holder.returncode == 0, holder.stderr.read() if holder.stderr else ""
    lock_fd = entrypoint.acquire_container_instance_lock(tmp_path)
    try:
        metadata = os.fstat(lock_fd)
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert os.get_inheritable(lock_fd)
        assert fcntl.fcntl(lock_fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC == 0
    finally:
        entrypoint.release_container_instance_lock(lock_fd)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock")
@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo", "directory"])
def test_instance_lock_rejects_unsafe_files_without_following_them(
    tmp_path,
    unsafe_kind,
):
    entrypoint = _load_entrypoint()
    lock_path = tmp_path / entrypoint.CONTAINER_INSTANCE_LOCK_FILE
    external = tmp_path / "outside"
    external.write_text("outside", encoding="utf-8")

    if unsafe_kind == "symlink":
        lock_path.symlink_to(external)
    elif unsafe_kind == "hardlink":
        os.link(external, lock_path)
    elif unsafe_kind == "fifo":
        os.mkfifo(lock_path)
    else:
        lock_path.mkdir()

    with pytest.raises(RuntimeError, match="single-link regular file"):
        entrypoint.acquire_container_instance_lock(tmp_path)

    assert external.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock")
def test_existing_safe_lock_supports_read_only_open_without_chmod(
    tmp_path,
    monkeypatch,
):
    entrypoint = _load_entrypoint()
    lock_path = tmp_path / entrypoint.CONTAINER_INSTANCE_LOCK_FILE
    lock_path.write_bytes(b"")
    lock_path.chmod(0o600)
    real_open = entrypoint.os.open
    fchmod_calls: list[tuple] = []

    def read_only_open(path, flags, *args, **kwargs):
        if (
            path == entrypoint.CONTAINER_INSTANCE_LOCK_FILE
            and flags & os.O_RDWR
        ):
            raise OSError(errno.EROFS, "simulated read-only remount")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(entrypoint.os, "open", read_only_open)
    monkeypatch.setattr(
        entrypoint.os,
        "fchmod",
        lambda *args: fchmod_calls.append(args),
    )

    lock_fd = entrypoint.acquire_container_instance_lock(tmp_path)
    try:
        assert os.get_inheritable(lock_fd)
        assert stat.S_IMODE(os.fstat(lock_fd).st_mode) == 0o600
        assert fchmod_calls == []
    finally:
        entrypoint.release_container_instance_lock(lock_fd)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock")
def test_read_only_lock_with_wrong_mode_fails_closed(tmp_path, monkeypatch):
    entrypoint = _load_entrypoint()
    lock_path = tmp_path / entrypoint.CONTAINER_INSTANCE_LOCK_FILE
    lock_path.write_bytes(b"")
    lock_path.chmod(0o640)
    real_open = entrypoint.os.open

    def read_only_open(path, flags, *args, **kwargs):
        if (
            path == entrypoint.CONTAINER_INSTANCE_LOCK_FILE
            and flags & os.O_RDWR
        ):
            raise OSError(errno.EROFS, "simulated read-only remount")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(entrypoint.os, "open", read_only_open)

    with pytest.raises(RuntimeError, match="open the container instance lock safely"):
        entrypoint.acquire_container_instance_lock(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock")
def test_read_only_storage_without_existing_lock_fails_closed(tmp_path, monkeypatch):
    entrypoint = _load_entrypoint()
    real_open = entrypoint.os.open

    def read_only_open(path, flags, *args, **kwargs):
        if (
            path == entrypoint.CONTAINER_INSTANCE_LOCK_FILE
            and flags & os.O_RDWR
        ):
            raise OSError(errno.EROFS, "simulated read-only remount")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(entrypoint.os, "open", read_only_open)

    with pytest.raises(RuntimeError, match="open the container instance lock safely"):
        entrypoint.acquire_container_instance_lock(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock")
def test_pre_exec_revalidation_rejects_replaced_lock_inode(tmp_path):
    entrypoint = _load_entrypoint()
    lock_fd = entrypoint.acquire_container_instance_lock(tmp_path)
    lock_path = tmp_path / entrypoint.CONTAINER_INSTANCE_LOCK_FILE
    displaced = tmp_path / "displaced-instance-lock"
    lock_path.rename(displaced)
    lock_path.write_bytes(b"")
    lock_path.chmod(0o600)

    try:
        with pytest.raises(RuntimeError, match="changed while it was opened"):
            entrypoint.verify_container_instance_lock(lock_fd, tmp_path)
    finally:
        entrypoint.release_container_instance_lock(lock_fd)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock")
def test_pre_exec_failure_releases_lock_without_spawning_guard_process(
    tmp_path,
    monkeypatch,
):
    entrypoint = _load_entrypoint()
    entrypoint.CONFIG_DIR = tmp_path
    entrypoint.SETTINGS_FILE = tmp_path / "settings.json"
    entrypoint.CHANNELWATCH_RUNTIME_DIR = tmp_path / "channelwatch-runtime"
    entrypoint.RESTART_REQUIRED_PATH = (
        entrypoint.CHANNELWATCH_RUNTIME_DIR / entrypoint.RESTART_REQUIRED_FILE
    )
    monkeypatch.setattr(entrypoint, "running_as_root", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "config_filesystem_is_read_only",
        lambda _path: False,
    )
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(entrypoint.os, "getegid", lambda: 1000)
    monkeypatch.setattr(entrypoint.os, "getgroups", lambda: [])
    monkeypatch.setattr(
        entrypoint,
        "cleanup_restart_journal_candidates_before_validation",
        lambda: None,
    )
    monkeypatch.setattr(entrypoint, "validate_config_tree", lambda _path: None)
    monkeypatch.setattr(entrypoint, "ensure_settings", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        entrypoint, "merge_bootstrap_env", lambda _created, **_kwargs: None
    )
    monkeypatch.setattr(entrypoint, "chown_tree", lambda *_args: None)
    monkeypatch.setattr(entrypoint, "chmod_config_tree", lambda _path: None)
    monkeypatch.setattr(
        entrypoint, "render_supervisor_config", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(entrypoint, "prepare_standard_streams", lambda: None)
    monkeypatch.setattr(entrypoint, "drop_privileges", lambda *_args: None)
    monkeypatch.setattr(entrypoint, "verify_config_tree_writable", lambda _path: None)
    monkeypatch.setattr(entrypoint, "set_runtime_umask", lambda: None)
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint", "supervisord"])
    monkeypatch.setattr(
        entrypoint.os,
        "execvp",
        lambda *_args: (_ for _ in ()).throw(OSError("exec failed")),
    )

    with pytest.raises(OSError, match="exec failed"):
        entrypoint.main()

    lock_fd = entrypoint.acquire_container_instance_lock(tmp_path)
    entrypoint.release_container_instance_lock(lock_fd)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX flock")
def test_mature_read_only_config_skips_mutation_but_reaches_exec(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("CHANNELWATCH_CONFIG_READ_ONLY", raising=False)
    entrypoint = _load_entrypoint()
    entrypoint.CONFIG_DIR = tmp_path
    entrypoint.SETTINGS_FILE = tmp_path / "settings.json"
    entrypoint.CHANNELWATCH_RUNTIME_DIR = tmp_path / "channelwatch-runtime"
    entrypoint.RESTART_REQUIRED_PATH = (
        entrypoint.CHANNELWATCH_RUNTIME_DIR / entrypoint.RESTART_REQUIRED_FILE
    )
    (tmp_path / entrypoint.CONTAINER_INSTANCE_LOCK_FILE).write_bytes(b"")
    (tmp_path / entrypoint.CONTAINER_INSTANCE_LOCK_FILE).chmod(0o600)
    calls: list[str] = []
    phase = ["root"]
    validation_phases: list[str] = []

    monkeypatch.setattr(entrypoint, "running_as_root", lambda: False)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(entrypoint.os, "getegid", lambda: 1000)
    monkeypatch.setattr(entrypoint.os, "getgroups", lambda: [])
    monkeypatch.setattr(
        entrypoint,
        "config_filesystem_is_read_only",
        lambda _path: True,
    )
    monkeypatch.setattr(
        entrypoint,
        "cleanup_restart_journal_candidates_before_validation",
        lambda: None,
    )
    monkeypatch.setattr(entrypoint, "validate_config_tree", lambda _path: None)
    monkeypatch.setattr(
        entrypoint,
        "validate_read_only_runtime_state",
        lambda: validation_phases.append(phase[0]),
    )
    monkeypatch.setattr(entrypoint, "ensure_settings", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        entrypoint, "merge_bootstrap_env", lambda _created, **_kwargs: None
    )
    monkeypatch.setattr(
        entrypoint, "recover_v099_update_marker_after_image_pull", lambda: False
    )
    monkeypatch.setattr(
        entrypoint,
        "chown_tree",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not chown")),
    )
    monkeypatch.setattr(
        entrypoint,
        "chmod_config_tree",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not chmod")),
    )
    monkeypatch.setattr(
        entrypoint, "render_supervisor_config", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(entrypoint, "prepare_standard_streams", lambda: None)
    monkeypatch.setattr(
        entrypoint,
        "drop_privileges",
        lambda *_args: phase.__setitem__(0, "runtime-user"),
    )
    monkeypatch.setattr(
        entrypoint,
        "verify_config_tree_writable",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not probe")),
    )
    monkeypatch.setattr(entrypoint, "set_runtime_umask", lambda: None)
    monkeypatch.setattr(
        entrypoint, "verify_container_instance_lock", lambda *_args: None
    )
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint", "supervisord"])

    def capture_exec(_program, _argv):
        calls.append("exec")
        raise SystemExit(0)

    monkeypatch.setattr(entrypoint.os, "execvp", capture_exec)

    try:
        with pytest.raises(SystemExit) as exit_info:
            entrypoint.main()

        assert exit_info.value.code == 0
        assert calls == ["exec"]
        assert validation_phases == ["root", "runtime-user"]
        assert entrypoint.os.environ["CHANNELWATCH_CONFIG_READ_ONLY"] == "1"
    finally:
        entrypoint.os.environ.pop("CHANNELWATCH_CONFIG_READ_ONLY", None)


def test_instance_guard_uses_exec_not_a_guardian_process_and_keeps_both_services():
    entrypoint_source = _ENTRYPOINT.read_text(encoding="utf-8")
    supervisor_template = (
        _REPO_DIR
        / "deploy"
        / "config"
        / "supervisor"
        / "supervisord.conf.template"
    ).read_text(encoding="utf-8")

    assert "subprocess" not in entrypoint_source
    assert "os.fork(" not in entrypoint_source
    assert "os.execvp(sys.argv[1], sys.argv[1:])" in entrypoint_source
    assert entrypoint_source.count("acquire_container_instance_lock(CONFIG_DIR)") == 1
    assert "[program:core]" in supervisor_template
    assert "[program:ui]" in supervisor_template
