import json
import io
import zipfile
import base64
import struct
import zlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from ui.backend.support_report import (
    DEBUG_BUNDLE_MAX_COMPRESSION_RATIO,
    DEBUG_BUNDLE_MAX_ENTRIES,
    DEBUG_BUNDLE_MAX_UNCOMPRESSED_BYTES,
    DEBUG_BUNDLE_REQUIRED_MEMBERS,
    ReportAttachmentInvalid,
    ReportPayloadInvalid,
    ReportPayloadTooLarge,
    SCREENSHOT_MAX_DECODED_BYTES,
    SCREENSHOT_MAX_DIMENSION,
    SCREENSHOT_MAX_PIXELS,
    build_offline_report_package,
    parse_report_payload,
    parse_schema2_support_code,
    render_email_html,
    render_issue_body,
    render_report_preview,
    render_support_code,
    summarize_report_attachment,
)


def test_support_report_attachment_policy_matches_shared_worker_fixture():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "report_attachment_policy.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture == {
        "schema": 1,
        "images": {
            "maximum_side": SCREENSHOT_MAX_DIMENSION,
            "maximum_pixels": SCREENSHOT_MAX_PIXELS,
            "maximum_decoded_bytes": SCREENSHOT_MAX_DECODED_BYTES,
        },
        "debug_bundle": {
            "maximum_entries": DEBUG_BUNDLE_MAX_ENTRIES,
            "maximum_expanded_bytes": DEBUG_BUNDLE_MAX_UNCOMPRESSED_BYTES,
            "maximum_compression_ratio": DEBUG_BUNDLE_MAX_COMPRESSION_RATIO,
            "required_members": sorted(DEBUG_BUNDLE_REQUIRED_MEMBERS),
            "manifest": {
                "bundle_type": "debug",
                "created_by": "channelwatch",
                "bundle_schema_version": 1,
            },
        },
    }


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
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b"")


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


def _schema2_code(payload, **overrides):
    envelope = {
        "schema": 2,
        "report_id": "00010203-0405-4607-8809-0a0b0c0d0e0f",
        "created_at": "2026-08-13T00:00:00Z",
        "report": payload,
        "client": {"channelwatch_version": payload["diagnostics"].get("channelwatch_version") or "unknown", "submission_source": "in-app"},
    }
    envelope.update(overrides)
    return "CW-REPORT-v2-" + base64.urlsafe_b64encode(
        json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode()
    ).decode().rstrip("=")


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


@pytest.mark.parametrize(
    "overrides",
    [
        {"created_at": None},
        {"created_at": "not-a-date"},
        {"created_at": "2026-08-13T12:00:00-04:00"},
        {"created_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()},
        {"created_at": "future-beyond-limit"},
        {"client": None},
        {"client": {"channelwatch_version": "0.9.3", "submission_source": "portal"}},
        {"client": {"channelwatch_version": "forged", "submission_source": "in-app"}},
        {"client": {"channelwatch_version": "0.9.13", "submission_source": "in-app"}},
        {"client": {"channelwatch_version": "0.9.3", "submission_source": "in-app", "extra": True}},
    ],
)
def test_schema2_support_code_rejects_invalid_timestamp_or_client(overrides):
    if overrides.get("created_at") == "future-beyond-limit":
        overrides = {
            **overrides,
            "created_at": (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat(),
        }
    with pytest.raises(ReportPayloadInvalid):
        parse_schema2_support_code(_schema2_code(_payload(), **overrides))


def test_schema2_support_code_preserves_unicode_and_exact_original_code():
    payload = _payload(summary="Canal café — 日本語 📺")
    support_code = _schema2_code(payload)
    decoded, envelope = parse_schema2_support_code(support_code)
    assert decoded.summary == "Canal café — 日本語 📺"
    package = build_offline_report_package(decoded, support_code=support_code)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert archive.read("support-code.txt").decode("utf-8") == support_code
    assert envelope["client"] == {
        "channelwatch_version": "0.9.3",
        "submission_source": "in-app",
    }


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


def test_support_report_escapes_diagnostics_markdown_table_cells():
    diagnostics = _payload()["diagnostics"]
    diagnostics["channelwatch_version"] = "0.9.3 | injected\r\n| bad | row |"
    diagnostics["core_status"] = "Running\\path\nnext"
    payload = _parse(_payload(diagnostics=diagnostics))

    issue_body = render_issue_body(payload)

    assert "0.9.3 \\| injected \\| bad \\| row \\|" in issue_body
    assert "Running\\\\path next" in issue_body
    assert "| bad | row |" not in issue_body


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

    expected_support_code = "CW-REPORT-v2-" + base64.urlsafe_b64encode(
        json.dumps({
            "schema": 2,
            "report_id": "00010203-0405-4607-8809-0a0b0c0d0e0f",
            "created_at": "2026-08-13T00:00:00Z",
            "report": payload.model_dump(),
            "client": {"channelwatch_version": "0.9.3", "submission_source": "in-app"},
        }, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    package_bytes = build_offline_report_package(
        payload,
        support_code=expected_support_code,
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
    assert support_code.strip() == expected_support_code
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


def test_support_report_rejects_oversized_image_dimensions():
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (9000).to_bytes(4, "big") + (1).to_bytes(4, "big")
    with pytest.raises(ReportAttachmentInvalid, match="dimensions"):
        summarize_report_attachment(
            filename="huge.png", content_type="image/png", content=png, kind="screenshot"
        )


def test_support_report_rejects_truncated_png_after_valid_header():
    with pytest.raises(ReportAttachmentInvalid, match="incomplete|truncated"):
        summarize_report_attachment(
            filename="truncated.png",
            content_type="image/png",
            content=_png_bytes()[:-8],
            kind="screenshot",
        )


def test_support_report_rejects_duplicate_debug_bundle_paths():
    buffer = io.BytesIO()
    prefix = "channelwatch_debug_20260813T000000Z"
    manifest = json.dumps({"bundle_type": "debug", "bundle_schema_version": 1, "created_by": "channelwatch"})
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(f"{prefix}/manifest.json", manifest)
        bundle.writestr(f"{prefix}/MANIFEST.JSON", manifest)
        bundle.writestr(f"{prefix}/settings_sanitized.json", "{}")
        bundle.writestr(f"{prefix}/logs/app.log", "")
        bundle.writestr(f"{prefix}/health_snapshot.json", "{}")
    with pytest.raises(ReportAttachmentInvalid, match="duplicate"):
        summarize_report_attachment(
            filename="channelwatch_debug.zip",
            content_type="application/zip",
            content=buffer.getvalue(),
            kind="debug_bundle",
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
            payload = _payload()
            support_code = _schema2_code(payload)
            response = client.post(
                "/api/v1/support/offline-package",
                data={"support_code": support_code},
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
        packaged_code = package.read("support-code.txt").decode("utf-8").strip()
    assert "support-code.txt" in names
    assert "manifest.json" in names
    assert "active-stream.png" not in issue_preview
    assert "channelwatch_debug.zip" not in issue_preview
    assert packaged_code == support_code
