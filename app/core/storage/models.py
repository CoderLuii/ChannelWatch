"""Define SQLModel tables for ChannelWatch core state.

The models cover DVR server registration, activity history, notification
delivery audit rows, stream sessions, and local authentication data used by the
UI and monitoring core.
"""

import enum
from typing import Optional
from datetime import datetime, timezone
import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DvrServer(SQLModel, table=True):
    """Store a configured Channels DVR server.

    Each row is keyed by a stable DVR id and records connection details,
    soft-delete state, JSON overrides, and timestamps for lifecycle tracking.
    """

    __tablename__ = "dvr_server"

    id: str = Field(primary_key=True, max_length=64)
    name: str = Field(max_length=255)
    host: str = Field(max_length=255)
    port: int = Field(default=8089)
    enabled: bool = Field(default=True)
    deleted_at: Optional[datetime] = Field(default=None)
    overrides: str = Field(default="{}", sa_column=sa.Column(sa.Text))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ActivityEvent(SQLModel, table=True):
    """Persist user-visible activity emitted by alerts and monitors.

    Rows are keyed by activity id, scoped by DVR id, and indexed by timestamp
    and event type so dashboard and history queries can filter recent events.
    """

    __tablename__ = "activity_event"
    __table_args__ = (
        sa.Index("ix_activity_event_timestamp", "timestamp"),
        sa.Index("ix_activity_event_dvr_id_timestamp", "dvr_id", "timestamp"),
        sa.Index("ix_activity_event_dvr_id_event_type", "dvr_id", "event_type"),
    )

    id: str = Field(primary_key=True, max_length=36)
    dvr_id: str = Field(max_length=64)
    event_type: str = Field(max_length=64)
    title: str = Field(max_length=255)
    message: str = Field(default="", sa_column=sa.Column(sa.Text))
    timestamp: datetime = Field(default_factory=_utcnow)
    icon: str = Field(default="bell", max_length=64)
    channel_name: str = Field(default="", max_length=255)
    channel_number: str = Field(default="", max_length=64)
    device_name: str = Field(default="", max_length=255)
    device_ip: str = Field(default="", max_length=64)
    program_title: str = Field(default="", max_length=255)
    image_url: str = Field(default="", sa_column=sa.Column(sa.Text))
    stream_source: str = Field(default="", max_length=255)
    dvr_name: str = Field(default="", max_length=255)
    extra: str = Field(default="{}", sa_column=sa.Column(sa.Text))
    is_test: bool = Field(default=False)


class AlertHistoryRow(SQLModel, table=True):
    """Record alert deduplication history for a DVR.

    The table stores alert type, tracking key, send time, and extra metadata so
    repeated alerts can decide whether a notification was already sent.
    """

    __tablename__ = "alert_history_row"
    __table_args__ = (
        sa.Index(
            "ix_alert_history_row_dvr_id_sent_at", "dvr_id", "notification_sent_at"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    dvr_id: str = Field(max_length=64)
    alert_type: str = Field(max_length=64)
    tracking_key: str = Field(sa_column=sa.Column(sa.Text))
    notification_sent_at: datetime = Field(default_factory=_utcnow)
    extra: str = Field(default="{}", sa_column=sa.Column(sa.Text))


class NotificationDelivery(SQLModel, table=True):
    """Audit one notification delivery attempt or outcome.

    Delivery rows capture destination channel, provider, status, retry count,
    payload size, and related activity event ids for retry and reporting paths.
    """

    __tablename__ = "notification_delivery"
    __table_args__ = (
        sa.Index("ix_notification_delivery_delivered_at", "delivered_at"),
        sa.Index(
            "ix_notification_delivery_dvr_id_delivered_at", "dvr_id", "delivered_at"
        ),
        sa.Index("ix_notification_delivery_dvr_channel", "dvr_id", "channel"),
        sa.Index("ix_notification_delivery_status", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    dvr_id: str = Field(max_length=64)
    activity_event_id: Optional[str] = Field(default=None, max_length=36)
    provider_type: str = Field(max_length=64)
    channel_id: str = Field(default="", sa_column=sa.Column(sa.Text))
    delivered: bool = Field(default=False)
    error_message: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text, nullable=True)
    )
    delivered_at: datetime = Field(default_factory=_utcnow)
    event_type: str = Field(default="", max_length=64)
    channel: str = Field(default="", sa_column=sa.Column(sa.Text))
    status: str = Field(default="delivered", max_length=32)
    retry_count: int = Field(default=0)
    payload_size: int = Field(default=0)


class StreamSession(SQLModel, table=True):
    """Track an observed playback session for a DVR device.

    The row stores the session id, device and channel names, first and last seen
    times, optional end time, and serialized activity details.
    """

    __tablename__ = "stream_session"
    __table_args__ = (
        sa.Index("ix_stream_session_dvr_id_started_at", "dvr_id", "started_at"),
    )

    id: str = Field(primary_key=True, max_length=36)
    dvr_id: str = Field(max_length=64)
    device_name: str = Field(max_length=255)
    channel_name: str = Field(default="", max_length=255)
    started_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)
    ended_at: Optional[datetime] = Field(default=None)
    activity_data: str = Field(default="{}", sa_column=sa.Column(sa.Text))


class RoleEnum(str, enum.Enum):
    """Enumerate supported local user roles.

    Roles are stored as strings on ``User`` rows and are interpreted by the UI
    authorization layer when granting admin, operator, or viewer access.
    """

    admin = "admin"
    operator = "operator"
    viewer = "viewer"


class User(SQLModel, table=True):
    """Store a local ChannelWatch account.

    Usernames are unique, passwords are stored only as bcrypt hashes, and the
    role string determines the account's authorization level.
    """

    __tablename__ = "user"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(
        max_length=64,
        sa_column=sa.Column(sa.String(64), unique=True, nullable=False),
    )
    password_hash: str = Field(sa_column=sa.Column(sa.Text, nullable=False))
    role: str = Field(default=RoleEnum.viewer, max_length=16)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def set_password(self, password: str) -> None:
        """Hash and store a new plaintext password on this user.

        The bcrypt hash replaces any existing password hash on the model.
        """
        import bcrypt as _bcrypt

        self.password_hash = _bcrypt.hashpw(
            password.encode(), _bcrypt.gensalt()
        ).decode()

    def verify_password(self, password: str) -> bool:
        """Return whether *password* matches this user's stored hash.

        Invalid stored hashes are treated as failed verification and return
        ``False``.
        """
        import bcrypt as _bcrypt

        try:
            return _bcrypt.checkpw(password.encode(), self.password_hash.encode())
        except Exception:
            return False


class UserSession(SQLModel, table=True):
    """Store an authenticated browser session.

    Session rows contain unique session and CSRF tokens, reference the owning
    user id, and are indexed for token lookup and per-user expiry cleanup.
    """

    __tablename__ = "user_session"
    __table_args__ = (
        sa.Index("ix_user_session_token", "token"),
        sa.Index("ix_user_session_user_id_expires", "user_id", "expires_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field()
    token: str = Field(
        max_length=64,
        sa_column=sa.Column(sa.String(64), unique=True, nullable=False),
    )
    csrf_token: str = Field(
        max_length=64,
        sa_column=sa.Column(sa.String(64), nullable=False),
    )
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
