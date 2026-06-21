import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import Engine, text
from sqlmodel import Session, SQLModel, create_engine

log = logging.getLogger(__name__)

_NETWORK_FS_TYPES: frozenset[str] = frozenset(
    [
        "nfs",
        "nfs4",
        "nfs3",
        "cifs",
        "smbfs",
        "fuse.s3fs",
        "fuse.sshfs",
        "fuse.rclone",
        "fuse.sftpfs",
        "davfs",
        "davfs2",
    ]
)


def detect_filesystem(path: str) -> str:
    """Return the filesystem category for the mount point covering *path*.

    Returns one of:
        ``"native"``  – local filesystem (ext4, xfs, btrfs, tmpfs, …)
        ``"nfs"``     – NFS / NFS4
        ``"cifs"``    – CIFS / SMB
        ``"fuse"``    – FUSE-based network filesystem (s3fs, sshfs, rclone, …)
        ``"unknown"`` – /proc/mounts unavailable or path unresolvable

    The detection is based on ``/proc/mounts`` (Linux only). On platforms where
    the file is absent the function falls back to ``"unknown"``, which is treated
    the same as ``"native"`` for journal-mode purposes — a reasonable safe default
    because non-Linux deployments are local or CI environments, not production
    NAS shares.
    """
    proc_mounts = "/proc/mounts"
    if not os.path.exists(proc_mounts):
        log.debug("detect_filesystem: %s not found; assuming native FS", proc_mounts)
        return "unknown"

    try:
        resolved = str(Path(path).resolve()).replace("\\", "/")
    except (OSError, ValueError):
        resolved = str(path).replace("\\", "/")

    best_mountpoint = ""
    best_fstype = "unknown"

    try:
        with open(proc_mounts, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mountpoint = parts[1].replace("\\", "/")
                fstype = parts[2].lower()

                if not (
                    resolved == mountpoint
                    or resolved.startswith(mountpoint.rstrip("/") + "/")
                ):
                    continue

                if len(mountpoint) > len(best_mountpoint):
                    best_mountpoint = mountpoint
                    best_fstype = fstype
    except OSError as exc:
        log.warning("detect_filesystem: could not read %s: %s", proc_mounts, exc)
        return "unknown"

    if best_fstype == "unknown":
        return "unknown"

    if best_fstype in ("nfs", "nfs4", "nfs3"):
        return "nfs"
    if best_fstype in ("cifs", "smbfs"):
        return "cifs"
    if best_fstype.startswith("fuse."):
        return "fuse"
    return "native"


def _is_network_fs(fs_category: str) -> bool:
    return fs_category in ("nfs", "cifs", "fuse")


def configure_journal_mode(engine: Engine, db_path: Optional[str] = None) -> str:
    """Set the SQLite journal mode based on the filesystem hosting *db_path*.

    WAL mode is enabled only on local/native filesystems.  Network filesystems
    (NFS, CIFS, FUSE-based) stay on the default DELETE journal mode to avoid
    locking failures or silent data corruption.

    Returns the actual journal_mode string returned by SQLite after the PRAGMA.
    """
    fs_category = detect_filesystem(db_path or "") if db_path else "unknown"
    if _is_network_fs(fs_category):
        log.info(
            "SQLite journal mode: keeping DELETE (detected %s filesystem at %s)",
            fs_category,
            db_path or "<in-memory>",
        )
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA journal_mode=DELETE;")).scalar()
        return str(result or "delete")

    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode=WAL;")).scalar()
    mode = str(result or "wal")
    log.info(
        "SQLite journal mode: %s (filesystem: %s, path: %s)",
        mode,
        fs_category,
        db_path or "<in-memory>",
    )
    return mode


def create_db_engine(url: str = "sqlite:///:memory:", **kwargs) -> Engine:
    connect_args = kwargs.pop("connect_args", {})
    if url.startswith("sqlite"):
        connect_args.setdefault("check_same_thread", False)
    return create_engine(url, connect_args=connect_args, **kwargs)


def create_all_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_activity_event_timestamp "
                "ON activity_event (timestamp)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_notification_delivery_delivered_at "
                "ON notification_delivery (delivered_at)"
            )
        )
        conn.commit()


@contextmanager
def get_session(engine: Engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
