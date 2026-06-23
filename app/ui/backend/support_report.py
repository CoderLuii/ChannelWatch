from __future__ import annotations

import io
import json
import re
import zipfile
import base64
from html import escape as html_escape
from hashlib import sha256
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


REPORT_MODE_VALUES = {"dry-run", "email-test", "live"}
DEFAULT_REPORT_MODE = "dry-run"
DEFAULT_REPORT_ENDPOINT = "/api/v1/support/report-dry-run"
DEFAULT_REPORT_MAX_BYTES = 262144
DEFAULT_REPORT_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
DEFAULT_REPORT_MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
DEFAULT_REPORT_MAX_SCREENSHOTS = 5
DEFAULT_REPORT_MAX_ATTACHMENTS = DEFAULT_REPORT_MAX_SCREENSHOTS + 1
REPORT_ALLOWED_SCREENSHOT_TYPES = ("image/png", "image/jpeg", "image/webp")
REPORT_ALLOWED_DEBUG_BUNDLE_TYPES = (
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
)
REPORT_ALLOWED_ATTACHMENT_TYPES = (
    *REPORT_ALLOWED_SCREENSHOT_TYPES,
    *REPORT_ALLOWED_DEBUG_BUNDLE_TYPES,
)
DEBUG_BUNDLE_REQUIRED_MEMBERS = frozenset(
    {"manifest.json", "settings_sanitized.json", "logs/app.log", "health_snapshot.json"}
)
DEBUG_BUNDLE_MAX_ENTRIES = 8
DEBUG_BUNDLE_MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
PUBLIC_APP_URL = "https://channelwatch.coderluii.dev"
DEFAULT_REPORT_PORTAL_URL = f"{PUBLIC_APP_URL}/report"
GETCHANNELS_PROFILE_BASE = "https://community.getchannels.com/u"
GITHUB_PROFILE_BASE = "https://github.com"
CHANNELWATCH_LOGO_BASE64 = "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"
CHANNELWATCH_LOGO_DATA_URI = f"data:image/png;base64,{CHANNELWATCH_LOGO_BASE64}"

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_GITHUB_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_GETCHANNELS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PUBLIC_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|webhook|dsn)\s*[:=]\s*([^\s,;]+)"
)
_LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")


class ReportPayloadTooLarge(ValueError):
    pass


class ReportPayloadInvalid(ValueError):
    pass


class ReportAttachmentInvalid(ValueError):
    pass


class ReportAttachmentTooLarge(ValueError):
    pass


def _clean_single_line(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("\x00", "")).strip()


def _clean_multiline(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _normalize_optional_username(
    value: Any, *, pattern: re.Pattern[str], field_name: str
) -> str | None:
    text = _clean_single_line(value).lstrip("@")
    if not text:
        return None
    if not pattern.fullmatch(text):
        raise ValueError(f"{field_name} must be a valid username")
    return text


def normalize_github_username(value: Any) -> str | None:
    return _normalize_optional_username(
        value, pattern=_GITHUB_RE, field_name="GitHub username"
    )


def normalize_getchannels_username(value: Any) -> str | None:
    return _normalize_optional_username(
        value, pattern=_GETCHANNELS_RE, field_name="GetChannels username"
    )


def normalize_email(value: Any) -> str | None:
    text = _clean_single_line(value)
    if not text:
        return None
    if len(text) > 254 or not _EMAIL_RE.fullmatch(text):
        raise ValueError("Email must be a valid email address")
    return text


def redact_public_text(value: str) -> str:
    redacted = _PUBLIC_EMAIL_RE.sub("[redacted-email]", value)
    redacted = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[redacted]", redacted)
    redacted = _LONG_SECRET_RE.sub("[redacted-secret]", redacted)
    return redacted


def _clean_attachment_filename(value: Any) -> str:
    raw = _clean_single_line(value)
    basename = re.split(r"[\\/]+", raw)[-1].strip(". ")
    if not basename:
        raise ReportAttachmentInvalid("Attachment filename is required.")
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", basename).strip()
    if not cleaned:
        raise ReportAttachmentInvalid("Attachment filename is invalid.")
    return cleaned[:120]


def _clean_content_type(value: Any) -> str:
    return _clean_single_line(value).split(";", 1)[0].lower()


def _validate_debug_bundle_zip(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as bundle:
            infos = [info for info in bundle.infolist() if not info.is_dir()]
            if not infos or len(infos) > DEBUG_BUNDLE_MAX_ENTRIES:
                raise ReportAttachmentInvalid("Debug bundle ZIP structure is invalid.")
            total_uncompressed = sum(info.file_size for info in infos)
            if total_uncompressed > DEBUG_BUNDLE_MAX_UNCOMPRESSED_BYTES:
                raise ReportAttachmentInvalid("Debug bundle ZIP expands beyond the allowed size.")
            roots: set[str] = set()
            relative_members: set[str] = set()
            for info in infos:
                name = info.filename.replace("\\", "/")
                if info.flag_bits & 0x1:
                    raise ReportAttachmentInvalid("Encrypted debug bundle ZIPs are not supported.")
                if name.startswith("/") or "../" in f"/{name}" or ":" in name:
                    raise ReportAttachmentInvalid("Debug bundle ZIP contains unsafe paths.")
                if "/" not in name:
                    raise ReportAttachmentInvalid("Debug bundle ZIP structure is invalid.")
                root, relative = name.split("/", 1)
                roots.add(root)
                relative_members.add(relative)
            if len(roots) != 1:
                raise ReportAttachmentInvalid("Debug bundle ZIP structure is invalid.")
            root = next(iter(roots))
            if not root.startswith("channelwatch_debug_"):
                raise ReportAttachmentInvalid("Debug bundle ZIP is not a ChannelWatch debug bundle.")
            if not DEBUG_BUNDLE_REQUIRED_MEMBERS.issubset(relative_members):
                raise ReportAttachmentInvalid("Debug bundle ZIP is missing required ChannelWatch files.")
            if not relative_members.issubset(DEBUG_BUNDLE_REQUIRED_MEMBERS):
                raise ReportAttachmentInvalid("Debug bundle ZIP contains unsupported files.")
            manifest_name = f"{root}/manifest.json"
            manifest_info = bundle.getinfo(manifest_name)
            if manifest_info.file_size > 16 * 1024:
                raise ReportAttachmentInvalid("Debug bundle manifest is too large.")
            manifest = json.loads(bundle.read(manifest_name).decode("utf-8"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("bundle_type") != "debug"
                or manifest.get("created_by") != "channelwatch"
                or manifest.get("bundle_schema_version") != 1
            ):
                raise ReportAttachmentInvalid("Debug bundle manifest is invalid.")
    except (zipfile.BadZipFile, KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportAttachmentInvalid("Debug bundle ZIP structure could not be validated.") from exc


def _detect_attachment_type(
    filename: str, content_type: str, content: bytes, kind: Literal["screenshot", "debug_bundle"]
) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if kind == "debug_bundle":
        if suffix != "zip":
            raise ReportAttachmentInvalid("Debug bundle must be a .zip file.")
        if content_type and content_type not in REPORT_ALLOWED_DEBUG_BUNDLE_TYPES:
            raise ReportAttachmentInvalid("Debug bundle must be a ZIP file.")
        if not content.startswith(b"PK\x03\x04") and not content.startswith(b"PK\x05\x06"):
            raise ReportAttachmentInvalid("Debug bundle ZIP could not be validated.")
        _validate_debug_bundle_zip(content)
        return "application/zip"

    if content_type and content_type not in REPORT_ALLOWED_SCREENSHOT_TYPES:
        raise ReportAttachmentInvalid("Screenshots must be PNG, JPEG, or WebP images.")
    if suffix not in {"png", "jpg", "jpeg", "webp"}:
        raise ReportAttachmentInvalid("Screenshots must use .png, .jpg, .jpeg, or .webp.")
    if suffix == "png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in {"jpg", "jpeg"} and content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if (
        suffix == "webp"
        and len(content) >= 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        return "image/webp"
    raise ReportAttachmentInvalid("Screenshot image could not be validated.")


class ReportFeatureToggles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_watching: bool = False
    vod_watching: bool = False
    disk_space: bool = False
    recording_events: bool = False
    stream_counter: bool = False


class ReportDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channelwatch_version: str | None = Field(default=None, max_length=40)
    dvr_count: int = Field(default=0, ge=0, le=100)
    connected_dvr_count: int = Field(default=0, ge=0, le=100)
    core_status: str | None = Field(default=None, max_length=60)
    monitoring_statuses: list[str] = Field(default_factory=list, max_length=20)
    notification_providers: list[str] = Field(default_factory=list, max_length=20)
    feature_toggles: ReportFeatureToggles = Field(default_factory=ReportFeatureToggles)

    @field_validator(
        "channelwatch_version",
        "core_status",
        mode="before",
    )
    @classmethod
    def clean_optional_string(cls, value: Any) -> str | None:
        text = _clean_single_line(value)
        return text or None

    @field_validator("monitoring_statuses", "notification_providers", mode="before")
    @classmethod
    def clean_string_list(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value[:20]:
            text = _clean_single_line(item)
            if text:
                cleaned.append(text[:80])
        return cleaned


class ReportProblemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    expected: str | None = Field(default=None, max_length=2000)
    getchannels_username: str | None = None
    github_username: str | None = None
    email: str | None = None
    diagnostics: ReportDiagnostics = Field(default_factory=ReportDiagnostics)
    turnstile_token: str | None = Field(default=None, max_length=2048)

    @field_validator("summary", mode="before")
    @classmethod
    def clean_summary(cls, value: Any) -> str:
        return _clean_single_line(value)

    @field_validator("expected", mode="before")
    @classmethod
    def clean_expected(cls, value: Any) -> str | None:
        text = _clean_multiline(value)
        return text or None

    @field_validator("getchannels_username", mode="before")
    @classmethod
    def clean_getchannels_username(cls, value: Any) -> str | None:
        return normalize_getchannels_username(value)

    @field_validator("github_username", mode="before")
    @classmethod
    def clean_github_username(cls, value: Any) -> str | None:
        return normalize_github_username(value)

    @field_validator("email", mode="before")
    @classmethod
    def clean_email(cls, value: Any) -> str | None:
        return normalize_email(value)

    @field_validator("turnstile_token", mode="before")
    @classmethod
    def clean_turnstile_token(cls, value: Any) -> str | None:
        text = _clean_single_line(value)
        return text or None


class ReportAttachmentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=120)
    content_type: str = Field(min_length=1, max_length=80)
    size_bytes: int = Field(ge=1)
    kind: Literal["screenshot", "debug_bundle"]
    sha256: str = Field(min_length=64, max_length=64)


class ReportConfigResponse(BaseModel):
    mode: Literal["dry-run", "email-test", "live"]
    endpoint: str
    portal_url: str = DEFAULT_REPORT_PORTAL_URL
    max_bytes: int
    turnstile_site_key: str | None = None
    attachments_enabled: bool = True
    max_attachment_bytes: int = DEFAULT_REPORT_MAX_ATTACHMENT_BYTES
    max_total_attachment_bytes: int = DEFAULT_REPORT_MAX_TOTAL_ATTACHMENT_BYTES
    max_screenshot_count: int = DEFAULT_REPORT_MAX_SCREENSHOTS
    allowed_attachment_types: tuple[str, ...] = REPORT_ALLOWED_ATTACHMENT_TYPES


class ReportPreviewResponse(BaseModel):
    mode: Literal["dry-run", "email-test", "live"]
    status: Literal["dry-run-complete", "email-test-ready", "live-ready"]
    issue_title: str
    issue_body: str
    email_subject: str
    email_body: str
    email_html: str
    email_in_public_issue: bool = False
    attachments: list[ReportAttachmentSummary] = Field(default_factory=list)
    attachment_total_bytes: int = 0
    attachments_sent: bool = False


def parse_report_mode(value: str | None) -> Literal["dry-run", "email-test", "live"]:
    mode = (value or DEFAULT_REPORT_MODE).strip().lower()
    if mode not in REPORT_MODE_VALUES:
        return DEFAULT_REPORT_MODE
    return mode  # type: ignore[return-value]


def parse_report_payload(raw_body: bytes, max_bytes: int) -> ReportProblemPayload:
    if len(raw_body) > max_bytes:
        raise ReportPayloadTooLarge("Report payload exceeds the configured size limit.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportPayloadInvalid("Report payload must be valid JSON.") from exc
    try:
        return ReportProblemPayload.model_validate(parsed)
    except ValidationError as exc:
        raise ReportPayloadInvalid(str(exc)) from exc


def summarize_report_attachment(
    *,
    filename: Any,
    content_type: Any,
    content: bytes,
    kind: Literal["screenshot", "debug_bundle"],
    max_attachment_bytes: int = DEFAULT_REPORT_MAX_ATTACHMENT_BYTES,
) -> ReportAttachmentSummary:
    safe_filename = _clean_attachment_filename(filename)
    safe_content_type = _clean_content_type(content_type)
    if not content:
        raise ReportAttachmentInvalid("Attachment is empty.")
    if len(content) > max_attachment_bytes:
        raise ReportAttachmentTooLarge("Attachment exceeds the per-file size limit.")
    detected_type = _detect_attachment_type(safe_filename, safe_content_type, content, kind)
    return ReportAttachmentSummary(
        filename=safe_filename,
        content_type=detected_type,
        size_bytes=len(content),
        kind=kind,
        sha256=sha256(content).hexdigest(),
    )


def validate_attachment_limits(
    attachments: list[ReportAttachmentSummary],
    *,
    max_total_attachment_bytes: int = DEFAULT_REPORT_MAX_TOTAL_ATTACHMENT_BYTES,
    max_screenshot_count: int = DEFAULT_REPORT_MAX_SCREENSHOTS,
) -> None:
    screenshot_count = sum(1 for item in attachments if item.kind == "screenshot")
    debug_bundle_count = sum(1 for item in attachments if item.kind == "debug_bundle")
    if screenshot_count > max_screenshot_count:
        raise ReportAttachmentInvalid("Too many screenshots were attached.")
    if debug_bundle_count > 1:
        raise ReportAttachmentInvalid("Only one debug bundle ZIP can be attached.")
    if len(attachments) > DEFAULT_REPORT_MAX_ATTACHMENTS:
        raise ReportAttachmentInvalid("Too many files were attached.")
    total_size = sum(item.size_bytes for item in attachments)
    if total_size > max_total_attachment_bytes:
        raise ReportAttachmentTooLarge("Attachments exceed the total size limit.")


def _format_public_contact(payload: ReportProblemPayload) -> str:
    lines: list[str] = []
    if payload.getchannels_username:
        url = _getchannels_profile_url(payload.getchannels_username)
        lines.append(f"- GetChannels community: [@{payload.getchannels_username}]({url})")
    if payload.github_username:
        url = _github_profile_url(payload.github_username)
        lines.append(f"- GitHub: [@{payload.github_username}]({url})")
    if not lines:
        lines.append("- No public contact handle provided.")
    return "\n".join(lines)


def _markdown_table_value(value: Any) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = re.sub(r"\r\n|\r|\n", r"\\n", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _format_diagnostics(diagnostics: ReportDiagnostics) -> str:
    toggles = diagnostics.feature_toggles
    enabled_toggles = [
        label
        for enabled, label in [
            (toggles.channel_watching, "Channel watching"),
            (toggles.vod_watching, "VOD watching"),
            (toggles.disk_space, "Disk space"),
            (toggles.recording_events, "Recording events"),
            (toggles.stream_counter, "Stream counter"),
        ]
        if enabled
    ]
    monitoring = (
        ", ".join(diagnostics.monitoring_statuses)
        if diagnostics.monitoring_statuses
        else "Not reported"
    )
    providers = (
        ", ".join(diagnostics.notification_providers)
        if diagnostics.notification_providers
        else "None reported"
    )
    return "\n".join(
        [
            "| Field | Value |",
            "| --- | --- |",
            f"| ChannelWatch version | {_markdown_table_value(diagnostics.channelwatch_version or 'Unknown')} |",
            f"| DVRs configured | {diagnostics.dvr_count} |",
            f"| DVRs connected | {diagnostics.connected_dvr_count} |",
            f"| Core status | {_markdown_table_value(diagnostics.core_status or 'Unknown')} |",
            f"| Monitoring | {_markdown_table_value(monitoring)} |",
            f"| Notification providers | {_markdown_table_value(providers)} |",
            f"| Enabled feature toggles | {_markdown_table_value(', '.join(enabled_toggles) if enabled_toggles else 'None reported')} |",
        ]
    )


def _format_attachment_summary(attachments: list[ReportAttachmentSummary]) -> str:
    if not attachments:
        return "No screenshots or debug bundle attached."
    lines: list[str] = []
    for item in attachments:
        label = "Screenshot" if item.kind == "screenshot" else "Debug bundle"
        lines.append(
            f"- {label}: {item.filename} ({item.content_type}, {item.size_bytes} bytes, sha256 {item.sha256[:12]}...)"
        )
    return "\n".join(lines)


def _getchannels_profile_url(username: str | None) -> str | None:
    if not username:
        return None
    return f"{GETCHANNELS_PROFILE_BASE}/{quote(username, safe='')}"


def _github_profile_url(username: str | None) -> str | None:
    if not username:
        return None
    return f"{GITHUB_PROFILE_BASE}/{quote(username, safe='')}"


def _report_reply_subject(payload: ReportProblemPayload, issue_url: str | None = None) -> str:
    issue_match = _issue_number_from_url(issue_url)
    prefix = (
        f"ChannelWatch Issue #{issue_match} Follow-up"
        if issue_match
        else "ChannelWatch Report Follow-up"
    )
    title = payload.summary
    if len(title) > 90:
        title = f"{title[:87].rstrip()}..."
    return f"{prefix} - {title}"


def _mailto_url(payload: ReportProblemPayload, issue_url: str | None = None) -> str | None:
    if not payload.email:
        return None
    body = [
        "Hi,",
        "",
        "Thanks for sending the ChannelWatch report.",
        "",
        f"Report: {payload.summary}",
    ]
    if issue_url:
        body.extend(["", f"Issue: {issue_url}"])
    body.extend(["", ""])
    return (
        f"mailto:{quote(payload.email, safe='')}"
        f"?subject={quote(_report_reply_subject(payload, issue_url), safe='')}"
        f"&body={quote(chr(10).join(body), safe='')}"
    )


def _issue_number_from_url(issue_url: str | None) -> str | None:
    issue_match = re.search(r"/issues/(\d+)(?:$|[/?#])", issue_url or "")
    return issue_match.group(1) if issue_match else None


def _format_bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def _email_row(
    label: str,
    value: str,
    *,
    emphasize: bool = False,
    href: str | None = None,
) -> str:
    weight = "700" if emphasize else "400"
    rendered_value = html_escape(value)
    if href:
        rendered_value = (
            f'<a href="{html_escape(href, quote=True)}" '
            'style="color:#93c5fd;text-decoration:underline;">'
            f"{rendered_value}</a>"
        )
    return (
        "<tr>"
        f'<td style="color:#9aa9bc;font-size:13px;padding:8px 0;">{html_escape(label)}</td>'
        f'<td style="color:#e8f0ff;font-size:13px;font-weight:{weight};padding:8px 0;text-align:right;">'
        f"{rendered_value}</td>"
        "</tr>"
    )


def render_email_html(
    payload: ReportProblemPayload,
    *,
    mode: Literal["dry-run", "email-test", "live"],
    attachments: list[ReportAttachmentSummary] | None = None,
    issue_url: str | None = None,
) -> str:
    attachments = attachments or []
    diagnostics = payload.diagnostics
    submitted_at = datetime.now(timezone.utc).isoformat()
    issue_title = render_issue_title(payload)
    issue_body = render_issue_body(payload)
    reply_url = _mailto_url(payload, issue_url)
    getchannels_url = _getchannels_profile_url(payload.getchannels_username)
    github_url = _github_profile_url(payload.github_username)
    primary_action = ("Open GitHub issue", issue_url) if issue_url else ("Reply to reporter", reply_url)
    secondary_action = ("Reply to reporter", reply_url) if issue_url and reply_url else (None, None)
    action_buttons = (
        (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 4px;">'
            f'<tr><td><a href="{html_escape(primary_action[1], quote=True)}" '
            'style="background:#3b82f6;border-radius:8px;color:#ffffff;display:block;font-size:14px;'
            'font-weight:700;padding:13px 16px;text-align:center;text-decoration:none;">'
            f"{html_escape(primary_action[0])}</a></td></tr>"
            + (
                f'<tr><td style="padding-top:10px;"><a href="{html_escape(secondary_action[1], quote=True)}" '
                'style="background:#111827;border:1px solid #2d4470;border-radius:8px;color:#dbeafe;'
                'display:block;font-size:13px;font-weight:700;padding:12px 16px;text-align:center;text-decoration:none;">'
                f"{html_escape(secondary_action[0])}</a></td></tr>"
                if secondary_action[1]
                else ""
            )
            + "</table>"
        )
        if primary_action[1]
        else '<span style="color:#9aa9bc;font-size:13px;">No reply or issue link is available yet.</span>'
    )
    contact_rows = [
        _email_row(
            "Private email",
            payload.email or "Not provided",
            emphasize=bool(payload.email),
            href=reply_url,
        ),
        _email_row(
            "GetChannels username",
            f"@{payload.getchannels_username}" if payload.getchannels_username else "Not provided",
            href=getchannels_url,
        ),
        _email_row(
            "GitHub username",
            f"@{payload.github_username}" if payload.github_username else "Not provided",
            href=github_url,
        ),
    ]
    diagnostics_rows = [
        _email_row("ChannelWatch version", diagnostics.channelwatch_version or "Unknown"),
        _email_row("DVRs configured", str(diagnostics.dvr_count)),
        _email_row("DVRs connected", str(diagnostics.connected_dvr_count)),
        _email_row("Core status", diagnostics.core_status or "Unknown"),
        _email_row(
            "Monitoring",
            ", ".join(diagnostics.monitoring_statuses)
            if diagnostics.monitoring_statuses
            else "Not reported",
        ),
        _email_row(
            "Notification providers",
            ", ".join(diagnostics.notification_providers)
            if diagnostics.notification_providers
            else "None reported",
        ),
    ]
    attachment_html = (
        "".join(
            [
                (
                    "<tr>"
                    '<td style="border-top:1px solid #22314f;color:#e8f0ff;font-size:13px;padding:10px 0;">'
                    f'{html_escape("Debug bundle" if item.kind == "debug_bundle" else "Screenshot")}</td>'
                    '<td style="border-top:1px solid #22314f;color:#9aa9bc;font-size:13px;padding:10px 0;text-align:right;">'
                    f"{html_escape(item.filename)}<br />"
                    f"{html_escape(_format_bytes(item.size_bytes))} &middot; sha256 {html_escape(item.sha256[:12])}..."
                    "</td>"
                    "</tr>"
                )
                for item in attachments
            ]
        )
        if attachments
        else (
            '<tr><td colspan="2" style="border-top:1px solid #22314f;color:#9aa9bc;'
            'font-size:13px;padding:10px 0;">No screenshots or debug bundle attached.</td></tr>'
        )
    )
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{html_escape(render_email_subject(payload, issue_url))}</title>
  </head>
  <body style="background:#060b14;color:#e8f0ff;font-family:Helvetica,Arial,sans-serif;margin:0;padding:32px 0;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#060b14;">
      <tr>
        <td align="center" style="padding:0 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b1220;border:1px solid #22314f;border-radius:16px;box-shadow:0 18px 36px rgba(0,0,0,0.35);max-width:600px;overflow:hidden;">
            <tr>
              <td style="background:#08111f;color:#60a5fa;font-size:10px;font-weight:700;letter-spacing:.12em;padding:8px 16px;text-align:center;text-transform:uppercase;">ChannelWatch Support</td>
            </tr>
            <tr>
              <td style="background:#111a2e;padding:20px 28px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="width:62px;">
                      <a href="{PUBLIC_APP_URL}" style="display:block;height:52px;text-decoration:none;width:52px;">
                        <img src="{CHANNELWATCH_LOGO_DATA_URI}" width="52" height="52" alt="ChannelWatch" style="border:0;display:block;height:52px;width:52px;" />
                      </a>
                    </td>
                    <td>
                      <div style="color:#f8fbff;font-size:20px;font-weight:700;line-height:1.25;">New ChannelWatch report</div>
                      <div style="color:#9aa9bc;font-size:13px;line-height:1.5;">{html_escape(mode)} &middot; {html_escape(submitted_at)}</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:28px;">
                <h1 style="color:#f8fbff;font-size:20px;line-height:1.3;margin:0 0 8px;">{html_escape(payload.summary)}</h1>
                <p style="color:#9aa9bc;font-size:14px;line-height:1.6;margin:0 0 18px;">{html_escape(payload.expected or "No expected behavior was provided.")}</p>
                <h2 style="color:#f8fbff;font-size:15px;margin:0 0 10px;">Next steps</h2>
                <div style="margin:0 0 14px;">{action_buttons or '<span style="color:#9aa9bc;font-size:13px;">No contact or issue links are available yet.</span>'}</div>
                <hr style="border:0;border-top:1px solid #22314f;margin:22px 0;" />
                <h2 style="color:#f8fbff;font-size:15px;margin:0 0 8px;">Reporter contact</h2>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">{''.join(contact_rows)}</table>
                <h2 style="color:#f8fbff;font-size:15px;margin:22px 0 8px;">Diagnostics</h2>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">{''.join(diagnostics_rows)}</table>
                <h2 style="color:#f8fbff;font-size:15px;margin:22px 0 8px;">Private attachments</h2>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">{attachment_html}</table>
                <h2 style="color:#f8fbff;font-size:15px;margin:22px 0 8px;">Report preview</h2>
                <div style="background:#07101f;border:1px solid #22314f;border-radius:10px;color:#dbeafe;font-size:13px;line-height:1.55;padding:14px;">
                  <div style="color:#60a5fa;font-weight:700;margin-bottom:10px;">{html_escape(issue_title)}</div>
                  <pre style="color:#dbeafe;font-family:Helvetica,Arial,sans-serif;font-size:13px;line-height:1.55;margin:0;white-space:pre-wrap;">{html_escape(issue_body)}</pre>
                </div>
              </td>
            </tr>
            <tr>
              <td style="background:#08111f;border-top:1px solid #22314f;color:#748399;font-size:12px;line-height:1.55;padding:18px 28px;text-align:center;">
                ChannelWatch &middot; Sent only to CoderLuii for troubleshooting
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def render_issue_title(payload: ReportProblemPayload) -> str:
    summary = redact_public_text(payload.summary)
    if len(summary) > 90:
        summary = f"{summary[:87].rstrip()}..."
    return f"[In-App] {summary}"


def render_issue_body(payload: ReportProblemPayload) -> str:
    summary = redact_public_text(payload.summary)
    expected = redact_public_text(payload.expected or "Not provided.")
    return "\n\n".join(
        [
            "# ChannelWatch Support Report",
            "## Summary\n\n" + summary,
            "## Expected behavior\n\n" + expected,
            "## Reporter\n\n" + _format_public_contact(payload),
            "## Diagnostics\n\n" + _format_diagnostics(payload.diagnostics),
        ]
    )


def render_email_subject(
    payload: ReportProblemPayload, issue_url: str | None = None
) -> str:
    title = payload.summary
    if len(title) > 110:
        title = f"{title[:107].rstrip()}..."
    issue_number = _issue_number_from_url(issue_url)
    if issue_number:
        return f"ChannelWatch Issue #{issue_number} - {title}"
    return f"ChannelWatch Report - {title}"


def render_email_body(
    payload: ReportProblemPayload,
    *,
    mode: Literal["dry-run", "email-test", "live"],
    attachments: list[ReportAttachmentSummary] | None = None,
) -> str:
    attachments = attachments or []
    issue_title = render_issue_title(payload)
    issue_body = render_issue_body(payload)
    submitted_at = datetime.now(timezone.utc).isoformat()
    reply_url = _mailto_url(payload)
    getchannels_url = _getchannels_profile_url(payload.getchannels_username)
    github_url = _github_profile_url(payload.github_username)
    return "\n".join(
        [
            "A ChannelWatch report was submitted.",
            "",
            f"Mode: {mode}",
            f"Submitted at: {submitted_at}",
            f"Private email: {payload.email or 'Not provided'}",
            f"Reply by email: {reply_url}" if reply_url else "Reply by email: Not available",
            f"GetChannels username: @{payload.getchannels_username}"
            if payload.getchannels_username
            else "GetChannels username: Not provided",
            f"GetChannels profile: {getchannels_url}"
            if getchannels_url
            else "GetChannels profile: Not provided",
            f"GitHub username: @{payload.github_username}"
            if payload.github_username
            else "GitHub username: Not provided",
            f"GitHub profile: {github_url}" if github_url else "GitHub profile: Not provided",
            "",
            "Private attachments:",
            _format_attachment_summary(attachments),
            "",
            "Public issue title:",
            issue_title,
            "",
            "Public issue body:",
            issue_body,
        ]
    )


def render_report_preview(
    payload: ReportProblemPayload,
    *,
    mode: Literal["dry-run", "email-test", "live"],
    attachments: list[ReportAttachmentSummary] | None = None,
    attachments_sent: bool = False,
) -> ReportPreviewResponse:
    attachments = attachments or []
    status: Literal["dry-run-complete", "email-test-ready", "live-ready"]
    if mode == "email-test":
        status = "email-test-ready"
    elif mode == "live":
        status = "live-ready"
    else:
        status = "dry-run-complete"
    issue_body = render_issue_body(payload)
    return ReportPreviewResponse(
        mode=mode,
        status=status,
        issue_title=render_issue_title(payload),
        issue_body=issue_body,
        email_subject=render_email_subject(payload),
        email_body=render_email_body(payload, mode=mode, attachments=attachments),
        email_html=render_email_html(payload, mode=mode, attachments=attachments),
        email_in_public_issue=bool(payload.email and payload.email in issue_body),
        attachments=attachments,
        attachment_total_bytes=sum(item.size_bytes for item in attachments),
        attachments_sent=attachments_sent,
    )


def render_support_code(
    payload: ReportProblemPayload,
    *,
    created_at: str | None = None,
) -> str:
    envelope = {
        "schema": 1,
        "source": "channelwatch",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "report": payload.model_dump(exclude_none=True),
    }
    raw = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"CW-REPORT-v1-{encoded}"


def build_offline_report_package(
    payload: ReportProblemPayload,
    *,
    attachments: list[tuple[ReportAttachmentSummary, bytes]] | None = None,
    portal_url: str = DEFAULT_REPORT_PORTAL_URL,
) -> bytes:
    attachments = attachments or []
    created_at = datetime.now(timezone.utc).isoformat()
    summaries = [summary for summary, _content in attachments]
    support_code = render_support_code(payload, created_at=created_at)
    issue_title = render_issue_title(payload)
    issue_body = render_issue_body(payload)
    attachment_entries: list[dict[str, Any]] = []
    for index, (summary, _content) in enumerate(attachments, start=1):
        folder = "debug-bundle" if summary.kind == "debug_bundle" else "screenshots"
        path = f"attachments/{folder}/{index:02d}-{summary.filename}"
        attachment_entries.append({**summary.model_dump(), "path": path})

    manifest = {
        "schema": 1,
        "source": "channelwatch",
        "created_at": created_at,
        "upload_url": portal_url,
        "support_code_file": "support-code.txt",
        "public_issue_preview_file": "issue-preview.md",
        "diagnostics_file": "diagnostics-summary.json",
        "attachments": attachment_entries,
    }
    readme = "\n".join(
        [
            "ChannelWatch offline support package",
            "",
            "1. Open the upload site from a browser with internet access:",
            f"   {portal_url}",
            "2. Paste the support code from support-code.txt.",
            "3. Attach the files under attachments/ when the upload page asks for screenshots or a debug bundle.",
            "",
            "The report preview does not include the private email address or attachment filenames.",
            "The support code may include contact fields entered in ChannelWatch so the hosted portal can prefill them.",
            "",
        ]
    )
    issue_preview = "\n\n".join([issue_title, issue_body])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("README.txt", readme)
        package.writestr("support-code.txt", support_code)
        package.writestr("issue-preview.md", issue_preview)
        package.writestr(
            "diagnostics-summary.json",
            json.dumps(payload.diagnostics.model_dump(), indent=2),
        )
        package.writestr("manifest.json", json.dumps(manifest, indent=2))
        for entry, (_summary, content) in zip(attachment_entries, attachments):
            package.writestr(entry["path"], content)
    return buffer.getvalue()
