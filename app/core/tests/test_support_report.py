import json
import io
import zipfile
import base64
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from ui.backend.support_report import (
    ReportAttachmentInvalid,
    ReportPayloadInvalid,
    ReportPayloadTooLarge,
    build_offline_report_package,
    parse_report_payload,
    render_email_html,
    render_issue_body,
    render_report_preview,
    render_support_code,
    summarize_report_attachment,
)


def _payload(**overrides):
    base = {
        "summary": "Active Streams shows a stream but no activity appears",
        "expected": "A channel watching activity event should appear.",
        "getchannels_username": "@Matthew_Crommert",
        "github_username": "@CoderLuii",
        "email": "viewer@example.com",
        "diagnostics": {
            "channelwatch_version": "0.9.3",
            "dvr_count": 1,
            "connected_dvr_count": 1,
            "core_status": "Running",
            "monitoring_statuses": ["healthy: 1"],
            "notification_providers": ["Pushover"],
            "feature_toggles": {
                "channel_watching": True,
                "vod_watching": True,
                "disk_space": True,
                "recording_events": True,
                "stream_counter": False,
            },
        },
    }
    base.update(overrides)
    return base


def _parse(payload):
    return parse_report_payload(json.dumps(payload).encode("utf-8"), 262144)


def _png_bytes():
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _zip_bytes():
    buffer = io.BytesIO()
    prefix = "channelwatch_debug_20260622T000000Z"
    manifest = {
        "bundle_type": "debug",
        "bundle_schema_version": 1,
        "created_by": "channelwatch",
    }
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"{prefix}/manifest.json", json.dumps(manifest))
        bundle.writestr(f"{prefix}/settings_sanitized.json", "{}")
        bundle.writestr(f"{prefix}/logs/app.log", "")
        bundle.writestr(f"{prefix}/health_snapshot.json", "{}")
    return buffer.getvalue()


def _debug_zip_with_extra_file():
    buffer = io.BytesIO()
    prefix = "channelwatch_debug_20260622T000000Z"
    manifest = {
        "bundle_type": "debug",
        "bundle_schema_version": 1,
        "created_by": "channelwatch",
    }
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"{prefix}/manifest.json", json.dumps(manifest))
        bundle.writestr(f"{prefix}/settings_sanitized.json", "{}")
        bundle.writestr(f"{prefix}/logs/app.log", "")
        bundle.writestr(f"{prefix}/health_snapshot.json", "{}")
        bundle.writestr(f"{prefix}/extra.exe", "not allowed")
    return buffer.getvalue()


def test_support_report_normalizes_public_usernames():
    payload = _parse(
        _payload(
            getchannels_username=" @Matthew_Crommert ",
            github_username=" @CoderLuii ",
        )
    )

    assert payload.getchannels_username == "Matthew_Crommert"
    assert payload.github_username == "CoderLuii"


def test_support_report_requires_problem_summary():
    with pytest.raises(ReportPayloadInvalid):
        _parse(_payload(summary="   "))


def test_support_report_rejects_oversized_payload_before_json_validation():
    raw = b"x" * 262145

    with pytest.raises(ReportPayloadTooLarge):
        parse_report_payload(raw, 262144)


def test_support_report_public_issue_excludes_private_email():
    payload = _parse(_payload(email="private-person@example.com"))

    issue_body = render_issue_body(payload)

    assert "private-person@example.com" not in issue_body
    assert "Email" not in issue_body


def test_support_report_redacts_email_and_secret_patterns_from_public_text():
    payload = _parse(
        _payload(
            summary="Issue for person@example.com with api_key=abc123",
            expected="token=abcdefghijklmnopqrstuvwxyz123456 and email me@example.com",
        )
    )

    issue_body = render_issue_body(payload)

    assert "person@example.com" not in issue_body
    assert "me@example.com" not in issue_body
    assert "api_key=abc123" not in issue_body
    assert "abcdefghijklmnopqrstuvwxyz123456" not in issue_body
    assert "[redacted-email]" in issue_body
    assert "api_key=[redacted]" in issue_body


def test_support_report_dry_run_preview_has_no_delivery_claims():
    payload = _parse(_payload())

    preview = render_report_preview(payload, mode="dry-run")

    assert preview.status == "dry-run-complete"
    assert preview.issue_title.startswith("[In-App] ")
    assert preview.email_in_public_issue is False
    assert not hasattr(preview, "issue_url")
    assert "viewer@example.com" not in preview.issue_body


def test_support_report_support_code_is_portable_report_draft():
    payload = _parse(_payload())

    support_code = render_support_code(payload, created_at="2026-06-22T00:00:00+00:00")
    encoded = support_code.removeprefix("CW-REPORT-v1-")
    padded = encoded + ("=" * ((4 - len(encoded) % 4) % 4))
    decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

    assert decoded["schema"] == 1
    assert decoded["source"] == "channelwatch"
    assert decoded["created_at"] == "2026-06-22T00:00:00+00:00"
    assert decoded["report"]["summary"] == "Active Streams shows a stream but no activity appears"
    assert decoded["report"]["email"] == "viewer@example.com"


def test_support_report_private_attachments_are_summarized_but_not_public():
    payload = _parse(_payload())
    attachments = [
        summarize_report_attachment(
            filename="screen-active-stream.png",
            content_type="image/png",
            content=_png_bytes(),
            kind="screenshot",
        ),
        summarize_report_attachment(
            filename="channelwatch_debug.zip",
            content_type="application/zip",
            content=_zip_bytes(),
            kind="debug_bundle",
        ),
    ]

    preview = render_report_preview(payload, mode="dry-run", attachments=attachments)

    assert [item.filename for item in preview.attachments] == [
        "screen-active-stream.png",
        "channelwatch_debug.zip",
    ]
    assert preview.attachment_total_bytes == sum(item.size_bytes for item in attachments)
    assert preview.attachments_sent is False
    assert "screen-active-stream.png" not in preview.issue_body
    assert "channelwatch_debug.zip" not in preview.issue_body
    assert "screen-active-stream.png" in preview.email_body
    assert "channelwatch_debug.zip" in preview.email_body
    assert "channelwatch-logo.png" not in preview.issue_body
    assert "# ChannelWatch Support Report" in preview.issue_body
    assert "## Diagnostics" in preview.issue_body
    assert "| Field | Value |" in preview.issue_body
    assert "[@Matthew_Crommert](https://community.getchannels.com/u/Matthew_Crommert)" in preview.issue_body


def test_support_report_offline_package_contains_validated_private_files():
    payload = _parse(_payload(email="private-person@example.com"))
    png_bytes = _png_bytes()
    zip_bytes = _zip_bytes()
    screenshot = summarize_report_attachment(
        filename="screen-active-stream.png",
        content_type="image/png",
        content=png_bytes,
        kind="screenshot",
    )
    bundle = summarize_report_attachment(
        filename="channelwatch_debug.zip",
        content_type="application/zip",
        content=zip_bytes,
        kind="debug_bundle",
    )

    package_bytes = build_offline_report_package(
        payload,
        attachments=[
            (screenshot, png_bytes),
            (bundle, zip_bytes),
        ],
        portal_url="https://channelwatch.coderluii.dev/report",
    )

    with zipfile.ZipFile(io.BytesIO(package_bytes), "r") as package:
        names = set(package.namelist())
        issue_preview = package.read("issue-preview.md").decode("utf-8")
        manifest = json.loads(package.read("manifest.json").decode("utf-8"))
        support_code = package.read("support-code.txt").decode("utf-8")

    assert "README.txt" in names
    assert "support-code.txt" in names
    assert "issue-preview.md" in names
    assert "diagnostics-summary.json" in names
    assert "private-person@example.com" not in issue_preview
    assert "screen-active-stream.png" not in issue_preview
    assert "channelwatch_debug.zip" not in issue_preview
    assert support_code.startswith("CW-REPORT-v1-")
    assert manifest["upload_url"] == "https://channelwatch.coderluii.dev/report"
    assert [item["filename"] for item in manifest["attachments"]] == [
        "screen-active-stream.png",
        "channelwatch_debug.zip",
    ]
    assert "attachments/screenshots/01-screen-active-stream.png" in names
    assert "attachments/debug-bundle/02-channelwatch_debug.zip" in names


def test_support_report_branded_email_html_keeps_public_issue_private():
    payload = _parse(_payload(email="private-person@example.com"))
    attachments = [
        summarize_report_attachment(
            filename="screen-active-stream.png",
            content_type="image/png",
            content=_png_bytes(),
            kind="screenshot",
        )
    ]

    html = render_email_html(
        payload,
        mode="dry-run",
        attachments=attachments,
        issue_url="https://github.com/CoderLuii/ChannelWatch/issues/32",
    )

    assert "ChannelWatch Support" in html
    assert "New ChannelWatch report" in html
    assert "Next steps" in html
    assert "private-person@example.com" in html
    assert "mailto:private-person%40example.com" in html
    assert "https://community.getchannels.com/u/Matthew_Crommert" in html
    assert "https://github.com/CoderLuii" in html
    assert "Open GitHub issue" in html
    assert "Reply to reporter" in html
    assert "Open Channels profile" not in html
    assert "screen-active-stream.png" in html
    assert "channelwatch-logo.png" not in html
    assert "Private maintainer" not in html
    assert "Report preview" in html
    public_section = html.split("Report preview", 1)[1]
    assert "private-person@example.com" not in public_section


def test_support_report_rejects_invalid_attachment_type():
    with pytest.raises(ReportAttachmentInvalid):
        summarize_report_attachment(
            filename="notes.txt",
            content_type="text/plain",
            content=b"plain text",
            kind="screenshot",
        )


def test_support_report_rejects_fake_debug_bundle_zip():
    with pytest.raises(ReportAttachmentInvalid):
        summarize_report_attachment(
            filename="channelwatch_debug.zip",
            content_type="application/zip",
            content=b"PK\x05\x06" + b"\x00" * 18,
            kind="debug_bundle",
        )


def test_support_report_rejects_debug_bundle_with_extra_files():
    with pytest.raises(ReportAttachmentInvalid):
        summarize_report_attachment(
            filename="channelwatch_debug.zip",
            content_type="application/zip",
            content=_debug_zip_with_extra_file(),
            kind="debug_bundle",
        )


def test_support_report_dry_run_endpoint_accepts_private_attachments():
    import ui.backend.main as ui_main

    with patch("ui.backend.main.CW_DISABLE_AUTH", True):
        with TestClient(ui_main.app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/v1/support/report-dry-run",
                data={"payload": json.dumps(_payload())},
                files=[
                    ("screenshots", ("active-stream.png", _png_bytes(), "image/png")),
                    ("debug_bundle", ("channelwatch_debug.zip", _zip_bytes(), "application/zip")),
                ],
            )

    assert response.status_code == 200
    body = response.json()
    assert [item["filename"] for item in body["attachments"]] == [
        "active-stream.png",
        "channelwatch_debug.zip",
    ]
    assert "active-stream.png" not in body["issue_body"]
    assert "channelwatch_debug.zip" not in body["issue_body"]
    assert body["attachments_sent"] is False


def test_support_report_offline_package_endpoint_returns_zip():
    import ui.backend.main as ui_main

    with patch("ui.backend.main.CW_DISABLE_AUTH", True):
        with TestClient(ui_main.app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/v1/support/offline-package",
                data={"payload": json.dumps(_payload())},
                files=[
                    ("screenshots", ("active-stream.png", _png_bytes(), "image/png")),
                    ("debug_bundle", ("channelwatch_debug.zip", _zip_bytes(), "application/zip")),
                ],
            )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content), "r") as package:
        names = set(package.namelist())
        issue_preview = package.read("issue-preview.md").decode("utf-8")
    assert "support-code.txt" in names
    assert "manifest.json" in names
    assert "active-stream.png" not in issue_preview
    assert "channelwatch_debug.zip" not in issue_preview
