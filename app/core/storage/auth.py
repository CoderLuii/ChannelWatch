"""Manage persisted users, password hashes, and login sessions.

This module owns the core authentication lifecycle for ChannelWatch. It creates
secure random tokens, hashes credentials with bcrypt, persists user sessions,
and invalidates sessions when credentials change.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy as sa
from sqlmodel import select

from .database import get_session
from .models import User, UserSession

SESSION_EXPIRY_SECONDS = 86400


def generate_token() -> str:
    """Return a cryptographically secure hexadecimal token.

    The token is 32 random bytes encoded as lowercase hexadecimal text and is
    suitable for session and CSRF tokens stored in the database.
    """
    return secrets.token_hex(32)


def hash_password(password: str) -> str:
    """Return a bcrypt hash for the supplied plaintext password.

    The password is encoded as UTF-8 before hashing and the resulting bcrypt
    value is returned as text for storage in the user table.
    """
    import bcrypt as _bcrypt

    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether *password* matches a stored bcrypt hash.

    Invalid or malformed stored hashes are treated as authentication failures
    and return ``False`` rather than propagating bcrypt errors.
    """
    import bcrypt as _bcrypt

    try:
        return _bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def get_user_by_username(engine, username: str) -> Optional[User]:
    """Return the user with *username* or ``None`` when it is absent.

    The lookup uses the supplied SQLModel engine and leaves session ownership to
    the shared storage session helper.
    """
    with get_session(engine) as db_session:
        row = db_session.exec(select(User).where(User.username == username)).first()
    return row


def get_user_by_id(engine, user_id: int) -> Optional[User]:
    """Return the user with *user_id* or ``None`` when it is absent.

    The returned model reflects the matching database row and may be used by
    callers that need role or credential metadata.
    """
    with get_session(engine) as db_session:
        row = db_session.exec(select(User).where(User.id == user_id)).first()
    return row


def create_session(
    engine, user_id: int, expiry_seconds: int = SESSION_EXPIRY_SECONDS
) -> UserSession:
    """Create and persist a login session for *user_id*.

    A session token and CSRF token are generated together, expire after
    *expiry_seconds*, and the returned ``UserSession`` contains the persisted id
    plus the token values callers must send back to the client.
    """
    now = datetime.now(timezone.utc)
    token = generate_token()
    csrf = generate_token()
    expires_at = now + timedelta(seconds=expiry_seconds)
    with get_session(engine) as db_session:
        session_obj = UserSession(
            user_id=user_id,
            token=token,
            csrf_token=csrf,
            created_at=now,
            expires_at=expires_at,
        )
        db_session.add(session_obj)
        db_session.commit()
        db_session.refresh(session_obj)
        sid = session_obj.id
    result = UserSession(
        id=sid,
        user_id=user_id,
        token=token,
        csrf_token=csrf,
        created_at=now,
        expires_at=expires_at,
    )
    return result


def get_session_by_token(engine, token: str) -> Optional[UserSession]:
    """Return the unexpired session for *token* or ``None``.

    Expiration timestamps without timezone information are interpreted as UTC so
    legacy rows compare consistently against the current UTC time.
    """
    with get_session(engine) as db_session:
        row = db_session.exec(
            select(UserSession).where(UserSession.token == token)
        ).first()
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now > exp:
        return None
    return row


def invalidate_session(engine, token: str) -> bool:
    """Delete the session identified by *token*.

    Returns ``True`` when a matching row was removed and ``False`` when the
    token was not present.
    """
    with get_session(engine) as db_session:
        row = db_session.exec(
            select(UserSession).where(UserSession.token == token)
        ).first()
        if row is None:
            return False
        db_session.delete(row)
        db_session.commit()
    return True


def invalidate_user_sessions(engine, user_id: int) -> int:
    """Delete all sessions for *user_id* and return the count removed.

    This is used after credential changes so previously issued browser sessions
    cannot continue using stale authentication state.
    """
    with get_session(engine) as db_session:
        sessions = db_session.exec(
            select(UserSession).where(UserSession.user_id == user_id)
        ).all()
        for session in sessions:
            db_session.delete(session)
        db_session.commit()
    return len(sessions)


def cleanup_expired_sessions(engine) -> int:
    """Remove expired sessions and return the number deleted.

    The cutoff is the current UTC time, matching the timestamp generated when
    sessions are created.
    """
    now = datetime.now(timezone.utc)
    with get_session(engine) as db_session:
        expired = db_session.exec(
            select(UserSession).where(UserSession.expires_at < now)
        ).all()
        for s in expired:
            db_session.delete(s)
        db_session.commit()
    return len(expired)


def get_user_count(engine) -> int:
    """Return the total number of persisted user accounts.

    A missing scalar value is normalized to ``0`` so setup code can safely use
    the result to decide whether an initial account is needed.
    """
    with get_session(engine) as db_session:
        result = db_session.execute(
            sa.select(sa.func.count()).select_from(User)
        ).scalar()
    return result or 0


def create_user(engine, username: str, password: str, role: str = "viewer") -> User:
    """Create a user account with the supplied credentials and role.

    Duplicate usernames raise ``ValueError``. The persisted row stores only the
    bcrypt password hash, while the returned model intentionally omits that hash
    from the reconstructed object.
    """
    existing = get_user_by_username(engine, username)
    if existing is not None:
        raise ValueError(f"User {username!r} already exists")
    with get_session(engine) as db_session:
        user = User(username=username, password_hash="", role=role)
        user.set_password(password)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        uid = user.id
    return User(
        id=uid,
        username=username,
        password_hash="",
        role=role,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def reset_password(engine, username: str, password: str) -> bool:
    """Replace a user's password and invalidate their active sessions.

    Returns ``True`` after a matching username is updated and ``False`` when no
    such user exists.
    """
    user_id: Optional[int] = None
    with get_session(engine) as db_session:
        user = db_session.exec(select(User).where(User.username == username)).first()
        if user is None:
            return False
        user_id = user.id
        user.set_password(password)
        user.updated_at = datetime.now(timezone.utc)
        db_session.add(user)
        db_session.commit()
    if user_id is not None:
        invalidate_user_sessions(engine, user_id)
    return True


def update_user_credentials(
    engine,
    user_id: int,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Optional[User]:
    """Update a user's username and optionally password.

    Username collisions raise ``ValueError``. When a password is changed, all
    existing sessions for the user are deleted before the updated user row is
    returned.
    """
    with get_session(engine) as db_session:
        user = db_session.exec(select(User).where(User.id == user_id)).first()
        if user is None:
            return None
        if username and username != user.username:
            existing = db_session.exec(
                select(User).where(User.username == username)
            ).first()
            if existing is not None and existing.id != user.id:
                raise ValueError(f"User {username!r} already exists")
            user.username = username
        if password:
            user.set_password(password)
            for session in db_session.exec(
                select(UserSession).where(UserSession.user_id == user.id)
            ).all():
                db_session.delete(session)
        user.updated_at = datetime.now(timezone.utc)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user
