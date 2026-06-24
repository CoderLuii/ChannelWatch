"""Structured error catalog for the ChannelWatch API backend.

Every error raised through ``structured_error()`` carries a machine-readable
payload that the frontend can map to user-facing copy and remediation links:

    {
        "code":        "ERR_DVR_NOT_FOUND",
        "message":     "DVR 'dvr_abc123' not found",
        "remediation": "Add the DVR in Settings \u2192 General, then save.",
        "docs_url":    null
    }

Backward compatibility: endpoints that have not yet been migrated still raise
plain ``HTTPException(detail="...")`` strings.  The frontend tolerates both
shapes via ``parseApiError()`` in ``lib/error-catalog.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Error codes — keep in sync with lib/error-catalog.ts
# ---------------------------------------------------------------------------


class ErrorCode:
    # Auth
    AUTH_INVALID_KEY = "ERR_AUTH_INVALID_KEY"
    AUTH_RBAC_NOT_ENABLED = "ERR_AUTH_RBAC_NOT_ENABLED"
    AUTH_DB_UNAVAILABLE = "ERR_AUTH_DB_UNAVAILABLE"
    AUTH_UNAUTHENTICATED = "ERR_AUTH_UNAUTHENTICATED"
    AUTH_CREDENTIALS_INVALID = "ERR_AUTH_CREDENTIALS_INVALID"
    AUTH_ADMIN_EXISTS = "ERR_AUTH_ADMIN_EXISTS"
    AUTH_CREDENTIALS_REQUIRED = "ERR_AUTH_CREDENTIALS_REQUIRED"
    AUTH_CSRF_INVALID = "ERR_AUTH_CSRF_INVALID"
    AUTH_FORBIDDEN = "ERR_AUTH_FORBIDDEN"
    AUTH_CROSS_SITE_REJECTED = "ERR_AUTH_CROSS_SITE_REJECTED"
    AUTH_CREDENTIALS_CONFLICT = "ERR_AUTH_CREDENTIALS_CONFLICT"
    AUTH_MODE_UNSUPPORTED = "ERR_AUTH_MODE_UNSUPPORTED"

    # DVR management
    DVR_NOT_FOUND = "ERR_DVR_NOT_FOUND"
    DVR_ALREADY_DELETED = "ERR_DVR_ALREADY_DELETED"
    DVR_NOT_DELETED = "ERR_DVR_NOT_DELETED"
    DVR_CONNECTION_FAILED = "ERR_DVR_CONNECTION_FAILED"
    DVR_TEST_TARGET_REJECTED = "ERR_DVR_TEST_TARGET_REJECTED"

    # Settings
    SETTINGS_SAVE_FAILED = "ERR_SETTINGS_SAVE_FAILED"

    # Backup / restore
    BACKUP_CREATE_FAILED = "ERR_BACKUP_CREATE_FAILED"
    RESTORE_INVALID_ZIP = "ERR_RESTORE_INVALID_ZIP"
    RESTORE_SCHEMA_AHEAD = "ERR_RESTORE_SCHEMA_AHEAD"
    RESTORE_FAILED = "ERR_RESTORE_FAILED"

    # Debug bundle
    DEBUG_BUNDLE_CREATE_FAILED = "ERR_DEBUG_BUNDLE_CREATE_FAILED"

    # Updates
    UPDATE_CHECK_FAILED = "ERR_UPDATE_CHECK_FAILED"
    UPDATE_APPLY_FAILED = "ERR_UPDATE_APPLY_FAILED"
    UPDATE_LOCKED = "ERR_UPDATE_LOCKED"
    UPDATE_IMAGE_REQUIRED = "ERR_UPDATE_IMAGE_REQUIRED"
    UPDATE_ROLLBACK_FAILED = "ERR_UPDATE_ROLLBACK_FAILED"

    # Support reports
    SUPPORT_REPORT_REQUEST_TOO_LARGE = "ERR_SUPPORT_REPORT_REQUEST_TOO_LARGE"
    SUPPORT_REPORT_PAYLOAD_INVALID = "ERR_SUPPORT_REPORT_PAYLOAD_INVALID"
    SUPPORT_REPORT_FORM_INVALID = "ERR_SUPPORT_REPORT_FORM_INVALID"
    SUPPORT_REPORT_ATTACHMENT_TOO_LARGE = "ERR_SUPPORT_REPORT_ATTACHMENT_TOO_LARGE"
    SUPPORT_REPORT_ATTACHMENT_INVALID = "ERR_SUPPORT_REPORT_ATTACHMENT_INVALID"

    # Activity / history
    ACTIVITY_FETCH_FAILED = "ERR_ACTIVITY_FETCH_FAILED"
    ACTIVITY_CLEAR_FAILED = "ERR_ACTIVITY_CLEAR_FAILED"
    ACTIVITY_DB_UNAVAILABLE = "ERR_ACTIVITY_DB_UNAVAILABLE"
    ACTIVITY_SORT_INVALID = "ERR_ACTIVITY_SORT_INVALID"
    ACTIVITY_FORMAT_UNSUPPORTED = "ERR_ACTIVITY_FORMAT_UNSUPPORTED"

    # Feeds
    FEED_DISABLED = "ERR_FEED_DISABLED"
    FEED_TOKEN_INVALID = "ERR_FEED_TOKEN_INVALID"

    # System / supervisor
    CORE_NOT_AVAILABLE = "ERR_CORE_NOT_AVAILABLE"
    RATE_LIMIT_EXCEEDED = "ERR_RATE_LIMIT_EXCEEDED"
    RESTART_FAILED = "ERR_RESTART_FAILED"
    SUPERVISOR_AUTH_MISSING = "ERR_SUPERVISOR_AUTH_MISSING"
    SUPERVISOR_CONNECT_FAILED = "ERR_SUPERVISOR_CONNECT_FAILED"
    SUPERVISOR_AUTH_FAILED = "ERR_SUPERVISOR_AUTH_FAILED"
    SUPERVISOR_COMMAND_FAILED = "ERR_SUPERVISOR_COMMAND_FAILED"
    SUPERVISOR_NOT_AVAILABLE = "ERR_SUPERVISOR_NOT_AVAILABLE"
    LOG_NOT_FOUND = "ERR_LOG_NOT_FOUND"

    # Generic
    INTERNAL_ERROR = "ERR_INTERNAL_ERROR"
    UNKNOWN = "ERR_UNKNOWN"


# ---------------------------------------------------------------------------
# Catalog entry definition
# ---------------------------------------------------------------------------


@dataclass
class CatalogEntry:
    code: str
    http_status: int
    message: str
    remediation: Optional[str] = field(default=None)
    docs_url: Optional[str] = field(default=None)


# ---------------------------------------------------------------------------
# Master catalog — one entry per ErrorCode constant
# ---------------------------------------------------------------------------

_CATALOG: dict[str, CatalogEntry] = {
    # Auth ---------------------------------------------------------------
    ErrorCode.AUTH_INVALID_KEY: CatalogEntry(
        code=ErrorCode.AUTH_INVALID_KEY,
        http_status=401,
        message="Invalid or missing API key.",
        remediation="Check your API key in Settings \u2192 Security.",
    ),
    ErrorCode.AUTH_RBAC_NOT_ENABLED: CatalogEntry(
        code=ErrorCode.AUTH_RBAC_NOT_ENABLED,
        http_status=501,
        message="Role-based access control (RBAC) is not enabled.",
        remediation="Enable RBAC in Settings \u2192 Security, then restart the container.",
    ),
    ErrorCode.AUTH_DB_UNAVAILABLE: CatalogEntry(
        code=ErrorCode.AUTH_DB_UNAVAILABLE,
        http_status=503,
        message="Authentication database is unavailable.",
        remediation="Check container health and storage. Restart the container if the problem persists.",
    ),
    ErrorCode.AUTH_UNAUTHENTICATED: CatalogEntry(
        code=ErrorCode.AUTH_UNAUTHENTICATED,
        http_status=401,
        message="Not authenticated.",
        remediation="Log in or supply a valid API key.",
    ),
    ErrorCode.AUTH_CREDENTIALS_INVALID: CatalogEntry(
        code=ErrorCode.AUTH_CREDENTIALS_INVALID,
        http_status=401,
        message="Invalid credentials.",
        remediation="Double-check your username and password.",
    ),
    ErrorCode.AUTH_ADMIN_EXISTS: CatalogEntry(
        code=ErrorCode.AUTH_ADMIN_EXISTS,
        http_status=409,
        message="An admin user already exists.",
        remediation="Manage existing users instead of creating a new admin.",
    ),
    ErrorCode.AUTH_CREDENTIALS_REQUIRED: CatalogEntry(
        code=ErrorCode.AUTH_CREDENTIALS_REQUIRED,
        http_status=422,
        message="Username and password are required.",
        remediation="Provide both a username and a password.",
    ),
    ErrorCode.AUTH_CSRF_INVALID: CatalogEntry(
        code=ErrorCode.AUTH_CSRF_INVALID,
        http_status=403,
        message="CSRF token missing or invalid.",
        remediation="Reload the page and try again.",
    ),
    ErrorCode.AUTH_FORBIDDEN: CatalogEntry(
        code=ErrorCode.AUTH_FORBIDDEN,
        http_status=403,
        message="Insufficient permissions.",
        remediation="Log in with an account that has sufficient permissions.",
    ),
    ErrorCode.AUTH_CROSS_SITE_REJECTED: CatalogEntry(
        code=ErrorCode.AUTH_CROSS_SITE_REJECTED,
        http_status=403,
        message="Cross-site request rejected.",
        remediation="Submit requests from the ChannelWatch UI only.",
    ),
    ErrorCode.AUTH_CREDENTIALS_CONFLICT: CatalogEntry(
        code=ErrorCode.AUTH_CREDENTIALS_CONFLICT,
        http_status=409,
        message="Credentials could not be updated.",
        remediation="Choose a different username or verify the current account state.",
    ),
    ErrorCode.AUTH_MODE_UNSUPPORTED: CatalogEntry(
        code=ErrorCode.AUTH_MODE_UNSUPPORTED,
        http_status=422,
        message="Unsupported authentication mode.",
        remediation="Use one of the documented setup modes: rbac or none.",
    ),
    # DVR management -----------------------------------------------------
    ErrorCode.DVR_NOT_FOUND: CatalogEntry(
        code=ErrorCode.DVR_NOT_FOUND,
        http_status=404,
        message="DVR not found.",
        remediation="Add a DVR in Settings \u2192 General, or verify the DVR id.",
    ),
    ErrorCode.DVR_ALREADY_DELETED: CatalogEntry(
        code=ErrorCode.DVR_ALREADY_DELETED,
        http_status=409,
        message="DVR is already archived.",
        remediation="Restore the DVR first, or add a new one.",
    ),
    ErrorCode.DVR_NOT_DELETED: CatalogEntry(
        code=ErrorCode.DVR_NOT_DELETED,
        http_status=409,
        message="DVR is not currently archived.",
        remediation="Archive the DVR before attempting to restore it.",
    ),
    ErrorCode.DVR_CONNECTION_FAILED: CatalogEntry(
        code=ErrorCode.DVR_CONNECTION_FAILED,
        http_status=502,
        message="Could not reach the DVR server.",
        remediation="Verify the host, port, and that Channels DVR is running.",
    ),
    ErrorCode.DVR_TEST_TARGET_REJECTED: CatalogEntry(
        code=ErrorCode.DVR_TEST_TARGET_REJECTED,
        http_status=400,
        message="DVR test target rejected by safety validation.",
        remediation="Use a valid Channels DVR host and port; localhost, metadata, link-local, and unsafe hosts are rejected.",
    ),
    # Settings -----------------------------------------------------------
    ErrorCode.SETTINGS_SAVE_FAILED: CatalogEntry(
        code=ErrorCode.SETTINGS_SAVE_FAILED,
        http_status=500,
        message="Failed to save settings.",
        remediation="Check that /config is writable inside the container and try again.",
    ),
    # Activity / history -------------------------------------------------
    ErrorCode.ACTIVITY_FETCH_FAILED: CatalogEntry(
        code=ErrorCode.ACTIVITY_FETCH_FAILED,
        http_status=500,
        message="Failed to retrieve activity history.",
        remediation="Check container logs and storage health.",
    ),
    ErrorCode.ACTIVITY_CLEAR_FAILED: CatalogEntry(
        code=ErrorCode.ACTIVITY_CLEAR_FAILED,
        http_status=500,
        message="Failed to clear activity history.",
        remediation="Check that /config is writable inside the container.",
    ),
    ErrorCode.ACTIVITY_DB_UNAVAILABLE: CatalogEntry(
        code=ErrorCode.ACTIVITY_DB_UNAVAILABLE,
        http_status=503,
        message="Activity database is not available.",
        remediation="Run the migration or restart the container to initialize the database.",
    ),
    ErrorCode.ACTIVITY_SORT_INVALID: CatalogEntry(
        code=ErrorCode.ACTIVITY_SORT_INVALID,
        http_status=400,
        message="Invalid sort parameter. Use 'asc' or 'desc'.",
        remediation="Pass ?sort=asc or ?sort=desc in the request.",
    ),
    ErrorCode.ACTIVITY_FORMAT_UNSUPPORTED: CatalogEntry(
        code=ErrorCode.ACTIVITY_FORMAT_UNSUPPORTED,
        http_status=400,
        message="Unsupported export format. Only 'csv' is supported.",
        remediation="Pass ?format=csv in the request.",
    ),
    # Feeds ---------------------------------------------------------------
    ErrorCode.FEED_DISABLED: CatalogEntry(
        code=ErrorCode.FEED_DISABLED,
        http_status=404,
        message="The requested feed is disabled.",
        remediation="Enable the feed in Settings before requesting it.",
    ),
    ErrorCode.FEED_TOKEN_INVALID: CatalogEntry(
        code=ErrorCode.FEED_TOKEN_INVALID,
        http_status=401,
        message="Invalid feed token.",
        remediation="Regenerate or copy the feed token from Settings.",
    ),
    # System / supervisor ------------------------------------------------
    ErrorCode.CORE_NOT_AVAILABLE: CatalogEntry(
        code=ErrorCode.CORE_NOT_AVAILABLE,
        http_status=501,
        message="Core monitoring components are not available.",
        remediation="Check container startup logs for import errors.",
    ),
    ErrorCode.RATE_LIMIT_EXCEEDED: CatalogEntry(
        code=ErrorCode.RATE_LIMIT_EXCEEDED,
        http_status=429,
        message="Rate limit exceeded. Please slow down.",
        remediation="Wait a moment and retry the request.",
    ),
    ErrorCode.RESTART_FAILED: CatalogEntry(
        code=ErrorCode.RESTART_FAILED,
        http_status=500,
        message="Failed to initiate restart.",
        remediation="Check supervisor and container logs.",
    ),
    ErrorCode.SUPERVISOR_AUTH_MISSING: CatalogEntry(
        code=ErrorCode.SUPERVISOR_AUTH_MISSING,
        http_status=503,
        message="Supervisor control socket is unavailable.",
        remediation="Restart the container to recreate the local supervisor socket.",
    ),
    ErrorCode.SUPERVISOR_CONNECT_FAILED: CatalogEntry(
        code=ErrorCode.SUPERVISOR_CONNECT_FAILED,
        http_status=503,
        message="Could not connect to the Supervisor control interface.",
        remediation="Check that supervisord is running inside the container.",
    ),
    ErrorCode.SUPERVISOR_AUTH_FAILED: CatalogEntry(
        code=ErrorCode.SUPERVISOR_AUTH_FAILED,
        http_status=401,
        message="Supervisor authentication failed.",
        remediation="Restart the container to regenerate supervisor credentials.",
    ),
    ErrorCode.SUPERVISOR_COMMAND_FAILED: CatalogEntry(
        code=ErrorCode.SUPERVISOR_COMMAND_FAILED,
        http_status=500,
        message="Supervisor command failed.",
        remediation="Check supervisord logs for details.",
    ),
    ErrorCode.SUPERVISOR_NOT_AVAILABLE: CatalogEntry(
        code=ErrorCode.SUPERVISOR_NOT_AVAILABLE,
        http_status=503,
        message="Supervisor proxy is not available.",
        remediation="Restart the container to restore supervisor connectivity.",
    ),
    ErrorCode.LOG_NOT_FOUND: CatalogEntry(
        code=ErrorCode.LOG_NOT_FOUND,
        http_status=404,
        message="Log file not found.",
        remediation="Ensure logging is enabled and the container has started correctly.",
    ),
    # Backup / restore ---------------------------------------------------
    ErrorCode.BACKUP_CREATE_FAILED: CatalogEntry(
        code=ErrorCode.BACKUP_CREATE_FAILED,
        http_status=500,
        message="Failed to create backup archive.",
        remediation="Check that /config is readable and that there is enough disk space.",
    ),
    ErrorCode.RESTORE_INVALID_ZIP: CatalogEntry(
        code=ErrorCode.RESTORE_INVALID_ZIP,
        http_status=400,
        message="The uploaded file is not a valid ChannelWatch backup.",
        remediation="Upload a .zip file produced by ChannelWatch's own backup feature.",
    ),
    ErrorCode.RESTORE_SCHEMA_AHEAD: CatalogEntry(
        code=ErrorCode.RESTORE_SCHEMA_AHEAD,
        http_status=409,
        message="Backup was created by a newer version of ChannelWatch.",
        remediation="Upgrade ChannelWatch to the version that created this backup before restoring.",
    ),
    ErrorCode.RESTORE_FAILED: CatalogEntry(
        code=ErrorCode.RESTORE_FAILED,
        http_status=500,
        message="Failed to write restored files.",
        remediation="Check that /config is writable and retry. The pre-restore snapshot is in /config/backups/.",
    ),
    # Debug bundle -------------------------------------------------------
    ErrorCode.DEBUG_BUNDLE_CREATE_FAILED: CatalogEntry(
        code=ErrorCode.DEBUG_BUNDLE_CREATE_FAILED,
        http_status=500,
        message="Failed to create debug bundle.",
        remediation="Check that /config is readable and that there is enough disk space.",
    ),
    # Updates ------------------------------------------------------------
    ErrorCode.UPDATE_CHECK_FAILED: CatalogEntry(
        code=ErrorCode.UPDATE_CHECK_FAILED,
        http_status=502,
        message="Failed to check for updates.",
        remediation="Check internet access from the container and try again.",
    ),
    ErrorCode.UPDATE_APPLY_FAILED: CatalogEntry(
        code=ErrorCode.UPDATE_APPLY_FAILED,
        http_status=500,
        message="Failed to apply the update.",
        remediation="Use Settings -> Updates to review the failure, then retry or roll back if available.",
    ),
    ErrorCode.UPDATE_LOCKED: CatalogEntry(
        code=ErrorCode.UPDATE_LOCKED,
        http_status=409,
        message="Another update operation is already running.",
        remediation="Wait for the current update operation to finish, then refresh the Update Center.",
    ),
    ErrorCode.UPDATE_IMAGE_REQUIRED: CatalogEntry(
        code=ErrorCode.UPDATE_IMAGE_REQUIRED,
        http_status=409,
        message="This update requires a new container image.",
        remediation="Update the ChannelWatch Docker/Unraid/Compose/Helm image through your normal container update path.",
    ),
    ErrorCode.UPDATE_ROLLBACK_FAILED: CatalogEntry(
        code=ErrorCode.UPDATE_ROLLBACK_FAILED,
        http_status=500,
        message="Failed to roll back the active update.",
        remediation="Check /config/channelwatch-runtime/rollback.json and container logs.",
    ),
    # Support reports ----------------------------------------------------
    ErrorCode.SUPPORT_REPORT_REQUEST_TOO_LARGE: CatalogEntry(
        code=ErrorCode.SUPPORT_REPORT_REQUEST_TOO_LARGE,
        http_status=413,
        message="Support report request is too large.",
        remediation="Remove large attachments or send fewer screenshots, then try again.",
    ),
    ErrorCode.SUPPORT_REPORT_PAYLOAD_INVALID: CatalogEntry(
        code=ErrorCode.SUPPORT_REPORT_PAYLOAD_INVALID,
        http_status=422,
        message="Support report details could not be validated.",
        remediation="Review the report fields and try again.",
    ),
    ErrorCode.SUPPORT_REPORT_FORM_INVALID: CatalogEntry(
        code=ErrorCode.SUPPORT_REPORT_FORM_INVALID,
        http_status=400,
        message="Support report upload form is invalid.",
        remediation="Reload ChannelWatch and try submitting the report again.",
    ),
    ErrorCode.SUPPORT_REPORT_ATTACHMENT_TOO_LARGE: CatalogEntry(
        code=ErrorCode.SUPPORT_REPORT_ATTACHMENT_TOO_LARGE,
        http_status=413,
        message="Support report attachment is too large.",
        remediation="Remove large files or attach fewer screenshots, then try again.",
    ),
    ErrorCode.SUPPORT_REPORT_ATTACHMENT_INVALID: CatalogEntry(
        code=ErrorCode.SUPPORT_REPORT_ATTACHMENT_INVALID,
        http_status=422,
        message="Support report attachment could not be validated.",
        remediation="Attach PNG, JPEG, or WebP screenshots and a ChannelWatch-generated debug bundle ZIP.",
    ),
    # Generic ------------------------------------------------------------
    ErrorCode.INTERNAL_ERROR: CatalogEntry(
        code=ErrorCode.INTERNAL_ERROR,
        http_status=500,
        message="An internal server error occurred.",
        remediation="Check container logs for details.",
    ),
    ErrorCode.UNKNOWN: CatalogEntry(
        code=ErrorCode.UNKNOWN,
        http_status=500,
        message="An unexpected error occurred.",
        remediation="Check container logs for details.",
    ),
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def catalog_entry(code: str) -> Optional[CatalogEntry]:
    return _CATALOG.get(code)


def structured_error(
    code: str,
    *,
    message: Optional[str] = None,
    remediation: Optional[str] = None,
    docs_url: Optional[str] = None,
) -> HTTPException:
    """Build a :class:`fastapi.HTTPException` with a structured detail payload.

    Callers should ``raise`` the returned exception::

        raise structured_error(ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found")

    The ``detail`` field in the response body will be a JSON object::

        {
            "code":        "ERR_DVR_NOT_FOUND",
            "message":     "DVR 'dvr_abc123' not found",
            "remediation": "Add a DVR in Settings \u2192 General, or verify the DVR id.",
            "docs_url":    null
        }

    Keyword arguments override the catalog defaults for a specific call.
    """
    entry = _CATALOG.get(code)
    if entry is None:
        entry = _CATALOG[ErrorCode.UNKNOWN]

    detail = {
        "code": entry.code,
        "message": message if message is not None else entry.message,
        "remediation": remediation if remediation is not None else entry.remediation,
        "docs_url": docs_url if docs_url is not None else entry.docs_url,
    }
    return HTTPException(status_code=entry.http_status, detail=detail)
