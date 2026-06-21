// Error catalog — TypeScript mirror of app/ui/backend/error_catalog.py
// Payload shape returned by migrated endpoints:
//   { code, message, remediation?, docs_url? }
// Unmigrated endpoints return plain string detail; parseApiError handles both.

export const ErrorCode = {
  AUTH_INVALID_KEY: "ERR_AUTH_INVALID_KEY",
  AUTH_RBAC_NOT_ENABLED: "ERR_AUTH_RBAC_NOT_ENABLED",
  AUTH_DB_UNAVAILABLE: "ERR_AUTH_DB_UNAVAILABLE",
  AUTH_UNAUTHENTICATED: "ERR_AUTH_UNAUTHENTICATED",
  AUTH_CREDENTIALS_INVALID: "ERR_AUTH_CREDENTIALS_INVALID",
  AUTH_ADMIN_EXISTS: "ERR_AUTH_ADMIN_EXISTS",
  AUTH_CREDENTIALS_REQUIRED: "ERR_AUTH_CREDENTIALS_REQUIRED",
  AUTH_CSRF_INVALID: "ERR_AUTH_CSRF_INVALID",
  AUTH_FORBIDDEN: "ERR_AUTH_FORBIDDEN",
  AUTH_CROSS_SITE_REJECTED: "ERR_AUTH_CROSS_SITE_REJECTED",
  AUTH_CREDENTIALS_CONFLICT: "ERR_AUTH_CREDENTIALS_CONFLICT",
  AUTH_MODE_UNSUPPORTED: "ERR_AUTH_MODE_UNSUPPORTED",
  DVR_NOT_FOUND: "ERR_DVR_NOT_FOUND",
  DVR_ALREADY_DELETED: "ERR_DVR_ALREADY_DELETED",
  DVR_NOT_DELETED: "ERR_DVR_NOT_DELETED",
  DVR_CONNECTION_FAILED: "ERR_DVR_CONNECTION_FAILED",
  DVR_TEST_TARGET_REJECTED: "ERR_DVR_TEST_TARGET_REJECTED",
  SETTINGS_SAVE_FAILED: "ERR_SETTINGS_SAVE_FAILED",
  BACKUP_CREATE_FAILED: "ERR_BACKUP_CREATE_FAILED",
  RESTORE_INVALID_ZIP: "ERR_RESTORE_INVALID_ZIP",
  RESTORE_SCHEMA_AHEAD: "ERR_RESTORE_SCHEMA_AHEAD",
  RESTORE_FAILED: "ERR_RESTORE_FAILED",
  DEBUG_BUNDLE_CREATE_FAILED: "ERR_DEBUG_BUNDLE_CREATE_FAILED",
  ACTIVITY_FETCH_FAILED: "ERR_ACTIVITY_FETCH_FAILED",
  ACTIVITY_CLEAR_FAILED: "ERR_ACTIVITY_CLEAR_FAILED",
  ACTIVITY_DB_UNAVAILABLE: "ERR_ACTIVITY_DB_UNAVAILABLE",
  ACTIVITY_SORT_INVALID: "ERR_ACTIVITY_SORT_INVALID",
  ACTIVITY_FORMAT_UNSUPPORTED: "ERR_ACTIVITY_FORMAT_UNSUPPORTED",
  FEED_DISABLED: "ERR_FEED_DISABLED",
  FEED_TOKEN_INVALID: "ERR_FEED_TOKEN_INVALID",
  CORE_NOT_AVAILABLE: "ERR_CORE_NOT_AVAILABLE",
  RATE_LIMIT_EXCEEDED: "ERR_RATE_LIMIT_EXCEEDED",
  RESTART_FAILED: "ERR_RESTART_FAILED",
  SUPERVISOR_AUTH_MISSING: "ERR_SUPERVISOR_AUTH_MISSING",
  SUPERVISOR_CONNECT_FAILED: "ERR_SUPERVISOR_CONNECT_FAILED",
  SUPERVISOR_AUTH_FAILED: "ERR_SUPERVISOR_AUTH_FAILED",
  SUPERVISOR_COMMAND_FAILED: "ERR_SUPERVISOR_COMMAND_FAILED",
  SUPERVISOR_NOT_AVAILABLE: "ERR_SUPERVISOR_NOT_AVAILABLE",
  LOG_NOT_FOUND: "ERR_LOG_NOT_FOUND",
  INTERNAL_ERROR: "ERR_INTERNAL_ERROR",
  UNKNOWN: "ERR_UNKNOWN",
  NETWORK: "ERR_NETWORK",
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];

export interface ErrorPayload {
  code: string;
  message: string;
  remediation?: string | null;
  docs_url?: string | null;
}

export interface CatalogEntry {
  code: string;
  message: string;
  remediation?: string;
}

const CATALOG: Record<string, CatalogEntry> = {
  [ErrorCode.AUTH_INVALID_KEY]: {
    code: ErrorCode.AUTH_INVALID_KEY,
    message: "Invalid or missing API key.",
    remediation:
      "Log in normally if this install uses secure auth. If this is an older install still using the legacy shared API key, verify that key in Settings \u2192 Security.",
  },
  [ErrorCode.AUTH_RBAC_NOT_ENABLED]: {
    code: ErrorCode.AUTH_RBAC_NOT_ENABLED,
    message: "Role-based access control (RBAC) is not enabled.",
    remediation:
      "Enable RBAC in Settings \u2192 Security, then restart the container.",
  },
  [ErrorCode.AUTH_DB_UNAVAILABLE]: {
    code: ErrorCode.AUTH_DB_UNAVAILABLE,
    message: "Authentication database is unavailable.",
    remediation:
      "Check container health and storage. Restart the container if the problem persists.",
  },
  [ErrorCode.AUTH_UNAUTHENTICATED]: {
    code: ErrorCode.AUTH_UNAUTHENTICATED,
    message: "Not authenticated.",
    remediation:
      "Log in with your ChannelWatch account. Older installs may still allow legacy API-key access until they are migrated.",
  },
  [ErrorCode.AUTH_CREDENTIALS_INVALID]: {
    code: ErrorCode.AUTH_CREDENTIALS_INVALID,
    message: "Invalid credentials.",
    remediation: "Double-check your username and password.",
  },
  [ErrorCode.AUTH_ADMIN_EXISTS]: {
    code: ErrorCode.AUTH_ADMIN_EXISTS,
    message: "An admin user already exists.",
    remediation: "Manage existing users instead of creating a new admin.",
  },
  [ErrorCode.AUTH_CREDENTIALS_REQUIRED]: {
    code: ErrorCode.AUTH_CREDENTIALS_REQUIRED,
    message: "Username and password are required.",
    remediation: "Provide both a username and a password.",
  },
  [ErrorCode.AUTH_CSRF_INVALID]: {
    code: ErrorCode.AUTH_CSRF_INVALID,
    message: "CSRF token missing or invalid.",
    remediation: "Reload the page and try again.",
  },
  [ErrorCode.AUTH_FORBIDDEN]: {
    code: ErrorCode.AUTH_FORBIDDEN,
    message: "Insufficient permissions.",
    remediation: "Log in with an account that has sufficient permissions.",
  },
  [ErrorCode.AUTH_CROSS_SITE_REJECTED]: {
    code: ErrorCode.AUTH_CROSS_SITE_REJECTED,
    message: "Cross-site request rejected.",
    remediation: "Submit requests from the ChannelWatch UI only.",
  },
  [ErrorCode.AUTH_CREDENTIALS_CONFLICT]: {
    code: ErrorCode.AUTH_CREDENTIALS_CONFLICT,
    message: "Credentials could not be updated.",
    remediation:
      "Choose a different username or verify the current account state.",
  },
  [ErrorCode.AUTH_MODE_UNSUPPORTED]: {
    code: ErrorCode.AUTH_MODE_UNSUPPORTED,
    message: "Unsupported authentication mode.",
    remediation: "Use one of the documented setup modes: rbac or none.",
  },
  [ErrorCode.DVR_NOT_FOUND]: {
    code: ErrorCode.DVR_NOT_FOUND,
    message: "DVR not found.",
    remediation: "Add a DVR in Settings \u2192 General, or verify the DVR id.",
  },
  [ErrorCode.DVR_ALREADY_DELETED]: {
    code: ErrorCode.DVR_ALREADY_DELETED,
    message: "DVR is already archived.",
    remediation: "Restore the DVR first, or add a new one.",
  },
  [ErrorCode.DVR_NOT_DELETED]: {
    code: ErrorCode.DVR_NOT_DELETED,
    message: "DVR is not currently archived.",
    remediation: "Archive the DVR before attempting to restore it.",
  },
  [ErrorCode.DVR_CONNECTION_FAILED]: {
    code: ErrorCode.DVR_CONNECTION_FAILED,
    message: "Could not reach the DVR server.",
    remediation: "Verify the host, port, and that Channels DVR is running.",
  },
  [ErrorCode.DVR_TEST_TARGET_REJECTED]: {
    code: ErrorCode.DVR_TEST_TARGET_REJECTED,
    message: "DVR test target failed safety validation.",
    remediation:
      "Use a valid Channels DVR host and port; localhost, metadata, link-local, and unsafe hosts are rejected.",
  },
  [ErrorCode.SETTINGS_SAVE_FAILED]: {
    code: ErrorCode.SETTINGS_SAVE_FAILED,
    message: "Failed to save settings.",
    remediation:
      "Check that /config is writable inside the container and try again.",
  },
  [ErrorCode.BACKUP_CREATE_FAILED]: {
    code: ErrorCode.BACKUP_CREATE_FAILED,
    message: "Failed to create backup archive.",
    remediation:
      "Check that /config is readable and that there is enough disk space.",
  },
  [ErrorCode.RESTORE_INVALID_ZIP]: {
    code: ErrorCode.RESTORE_INVALID_ZIP,
    message: "The uploaded file is not a valid ChannelWatch backup.",
    remediation:
      "Upload a .zip file produced by ChannelWatch's own backup feature.",
  },
  [ErrorCode.RESTORE_SCHEMA_AHEAD]: {
    code: ErrorCode.RESTORE_SCHEMA_AHEAD,
    message: "Backup was created by a newer version of ChannelWatch.",
    remediation:
      "Upgrade ChannelWatch to the version that created this backup before restoring.",
  },
  [ErrorCode.RESTORE_FAILED]: {
    code: ErrorCode.RESTORE_FAILED,
    message: "Failed to write restored files.",
    remediation:
      "Check that /config is writable and retry. The pre-restore snapshot is in /config/backups/.",
  },
  [ErrorCode.DEBUG_BUNDLE_CREATE_FAILED]: {
    code: ErrorCode.DEBUG_BUNDLE_CREATE_FAILED,
    message: "Failed to create debug bundle.",
    remediation:
      "Check that /config is readable and that there is enough disk space.",
  },
  [ErrorCode.ACTIVITY_FETCH_FAILED]: {
    code: ErrorCode.ACTIVITY_FETCH_FAILED,
    message: "Failed to retrieve activity history.",
    remediation: "Check container logs and storage health.",
  },
  [ErrorCode.ACTIVITY_CLEAR_FAILED]: {
    code: ErrorCode.ACTIVITY_CLEAR_FAILED,
    message: "Failed to clear activity history.",
    remediation: "Check that /config is writable inside the container.",
  },
  [ErrorCode.ACTIVITY_DB_UNAVAILABLE]: {
    code: ErrorCode.ACTIVITY_DB_UNAVAILABLE,
    message: "Activity database is not available.",
    remediation:
      "Run the migration or restart the container to initialize the database.",
  },
  [ErrorCode.ACTIVITY_SORT_INVALID]: {
    code: ErrorCode.ACTIVITY_SORT_INVALID,
    message: "Invalid sort parameter. Use \u2018asc\u2019 or \u2018desc\u2019.",
    remediation: "Pass ?sort=asc or ?sort=desc in the request.",
  },
  [ErrorCode.ACTIVITY_FORMAT_UNSUPPORTED]: {
    code: ErrorCode.ACTIVITY_FORMAT_UNSUPPORTED,
    message: "Unsupported export format. Only \u2018csv\u2019 is supported.",
    remediation: "Pass ?format=csv in the request.",
  },
  [ErrorCode.FEED_DISABLED]: {
    code: ErrorCode.FEED_DISABLED,
    message: "The requested feed is disabled.",
    remediation: "Enable the feed in Settings before requesting it.",
  },
  [ErrorCode.FEED_TOKEN_INVALID]: {
    code: ErrorCode.FEED_TOKEN_INVALID,
    message: "Invalid feed token.",
    remediation: "Regenerate or copy the feed token from Settings.",
  },
  [ErrorCode.CORE_NOT_AVAILABLE]: {
    code: ErrorCode.CORE_NOT_AVAILABLE,
    message: "Core monitoring components are not available.",
    remediation: "Check container startup logs for import errors.",
  },
  [ErrorCode.RATE_LIMIT_EXCEEDED]: {
    code: ErrorCode.RATE_LIMIT_EXCEEDED,
    message: "Rate limit exceeded. Please slow down.",
    remediation: "Wait a moment and retry the request.",
  },
  [ErrorCode.RESTART_FAILED]: {
    code: ErrorCode.RESTART_FAILED,
    message: "Failed to initiate restart.",
    remediation: "Check supervisor and container logs.",
  },
  [ErrorCode.SUPERVISOR_AUTH_MISSING]: {
    code: ErrorCode.SUPERVISOR_AUTH_MISSING,
    message: "Supervisor authentication file is missing or unreadable.",
    remediation: "Restart the container to regenerate supervisor credentials.",
  },
  [ErrorCode.SUPERVISOR_CONNECT_FAILED]: {
    code: ErrorCode.SUPERVISOR_CONNECT_FAILED,
    message: "Could not connect to the Supervisor control interface.",
    remediation: "Check that supervisord is running inside the container.",
  },
  [ErrorCode.SUPERVISOR_AUTH_FAILED]: {
    code: ErrorCode.SUPERVISOR_AUTH_FAILED,
    message: "Supervisor authentication failed.",
    remediation: "Restart the container to regenerate supervisor credentials.",
  },
  [ErrorCode.SUPERVISOR_COMMAND_FAILED]: {
    code: ErrorCode.SUPERVISOR_COMMAND_FAILED,
    message: "Supervisor command failed.",
    remediation: "Check supervisord logs for details.",
  },
  [ErrorCode.SUPERVISOR_NOT_AVAILABLE]: {
    code: ErrorCode.SUPERVISOR_NOT_AVAILABLE,
    message: "Supervisor proxy is not available.",
    remediation: "Restart the container to restore supervisor connectivity.",
  },
  [ErrorCode.LOG_NOT_FOUND]: {
    code: ErrorCode.LOG_NOT_FOUND,
    message: "Log file not found.",
    remediation:
      "Ensure logging is enabled and the container has started correctly.",
  },
  [ErrorCode.INTERNAL_ERROR]: {
    code: ErrorCode.INTERNAL_ERROR,
    message: "An internal server error occurred.",
    remediation: "Check container logs for details.",
  },
  [ErrorCode.UNKNOWN]: {
    code: ErrorCode.UNKNOWN,
    message: "An unexpected error occurred.",
    remediation: "Check container logs for details.",
  },
  [ErrorCode.NETWORK]: {
    code: ErrorCode.NETWORK,
    message: "Network error \u2014 the server could not be reached.",
    remediation: "Check your network connection and try again.",
  },
};

export function catalogEntry(code: string): CatalogEntry {
  return CATALOG[code] ?? CATALOG[ErrorCode.UNKNOWN]!;
}

export function isErrorPayload(value: unknown): value is ErrorPayload {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    "message" in value &&
    typeof (value as ErrorPayload).code === "string" &&
    typeof (value as ErrorPayload).message === "string"
  );
}

export async function parseApiError(response: Response): Promise<ErrorPayload> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    const text = await response.text().catch(() => "");
    return {
      code: ErrorCode.UNKNOWN,
      message: text || `HTTP ${response.status}`,
      remediation: null,
    };
  }

  if (body !== null && typeof body === "object") {
    const b = body as Record<string, unknown>;
    const detail = b["detail"];

    if (isErrorPayload(detail)) {
      return detail;
    }

    if (Array.isArray(detail)) {
      const parts = detail
        .map((e: unknown) => {
          if (typeof e === "object" && e !== null) {
            const entry = e as Record<string, unknown>;
            const loc = Array.isArray(entry["loc"])
              ? entry["loc"].slice(-1)[0]
              : null;
            const msg = entry["msg"] ?? "";
            return loc ? `${loc}: ${msg}` : String(msg);
          }
          return String(e);
        })
        .join("; ");
      return { code: ErrorCode.UNKNOWN, message: parts, remediation: null };
    }

    if (typeof detail === "string") {
      return { code: ErrorCode.UNKNOWN, message: detail, remediation: null };
    }
  }

  return {
    code: ErrorCode.UNKNOWN,
    message: `HTTP ${response.status}`,
    remediation: null,
  };
}

export function networkError(message?: string): ErrorPayload {
  return {
    code: ErrorCode.NETWORK,
    message: message ?? "Network error \u2014 the server could not be reached.",
    remediation: "Check your network connection and try again.",
  };
}
