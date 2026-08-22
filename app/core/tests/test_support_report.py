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
    redact_public_text,
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


def _privacy_parity_payload():
    diagnostics = _payload()["diagnostics"]
    diagnostics.update(
        {
            "channelwatch_version": "0.9.16",
            "core_status": "fatal admin@example.com fd00::1234 fd12::1%5",
            "monitoring_statuses": ["live token=hunter2", "![m](https://evil.example/m)"],
            "notification_providers": [
                "download https://files.example.com/report?X-Amz-Signature=secret&"
                "AWSAccessKeyId=AKIAEXAMPLE&GoogleAccessId=gcs-signer&"
                "sig=compact-secret&safe=value",
                "@everyone #123",
            ],
        }
    )
    return _payload(
        summary=(
            "Local fe80::beef fe80::1%eth0, http://2130706433/admin, "
            "http://0x7f000001/debug, http://127.1/private, "
            "http://0177.0.0.1/private, http://0x7f.0.0.1/private, "
            "http://017700000001/private, http://localhost./private, "
            "http://nas.local./private, http://localhost\u3002/private, "
            "http://nas\uff0elocal\uff61/private and ![tracker][pixel]"
        ),
        expected=(
            "Public 2001:db8::1 remains; private fe80::1. fd00::1! "
            "fe80::1/64? 'fd00::2'; host=fd00::1 host:fd00::2 address/fd00::3; "
            "expanded 0:0:0:0:0:0:0:1 and "
            "0000:0000:0000:0000:0000:0000:0000:0001; "
            "mapped ::ffff:c0a8:101 ::ffff:7f00:1 ::ffff:192.168.1.1 "
            "::ffff:010.0.0.1 ::ffff:0010.0.0.1 "
            "0:0:0:0:0:ffff:c0a8:101; punctuation fd00::4... fd00::5: fd00::6. "
            "public 2001:db8:fd00::1 remains; download "
            "https://files.example.com/report?X-Amz-Signature=secret&"
            "AWSAccessKeyId=AKIAEXAMPLE&GoogleAccessId=gcs-signer&"
            "sig=compact-secret&safe=value.\n\n"
            "Authorization: Token auth-token-value; Basic dXNlcjpwYXNz.\n"
            'Authorization: Digest username="digest-user", realm="dvr", '
            'nonce="digest-nonce", response="digest-response"\n'
            "curl -H 'Authorization: AWS4-HMAC-SHA256 Credential=curl-access, Signature=curl-signature' https://example.com\n"
            "request headers: Proxy-Authorization: Custom nonce=log-nonce, response=log-response\n"
            'payload {"password":"json-password-value", "token":"json-token-value", "api_key":"json-api-key-value"}\n'
            "log Cookie: sid=cookie-secret\n<password>xml-secret</password>\n"
            "Basic test fails; Basic mode fails; Basic auth failed.\n\n"
            "[pixel]: https://evil.example/pixel\n"
            "[beacon]: https://evil.example/beacon"
        ),
        diagnostics=diagnostics,
    )


def _assert_public_privacy_parity(text):
    assert "2001:db8::1" in text
    assert "2001:db8:fd00::1" in text
    assert "safe=value" in text
    assert "X-Amz-Signature=[redacted]" in text
    assert "AWSAccessKeyId=[redacted]" in text
    assert "GoogleAccessId=[redacted]" in text
    assert "sig=[redacted]" in text
    assert "Basic test fails; Basic mode fails; Basic auth failed." in text
    for private_value in (
        "fd00::1234",
        "fd12::1%5",
        "fe80::beef",
        "fe80::1%eth0",
        "fe80::1.",
        "fd00::1!",
        "fe80::1/64",
        "fd00::2",
        "host=fd00::1",
        "host:fd00::2",
        "address/fd00::3",
        "0:0:0:0:0:0:0:1",
        "0000:0000:0000:0000:0000:0000:0000:0001",
        "::ffff:c0a8:101",
        "::ffff:7f00:1",
        "::ffff:192.168.1.1",
        "::ffff:010.0.0.1",
        "::ffff:0010.0.0.1",
        "0:0:0:0:0:ffff:c0a8:101",
        "fd00::4",
        "fd00::5",
        "fd00::6",
        "2130706433",
        "0x7f000001",
        "127.1",
        "0177.0.0.1",
        "0x7f.0.0.1",
        "017700000001",
        "localhost.",
        "nas.local.",
        "localhost\u3002",
        "nas\uff0elocal\uff61",
        "AKIAEXAMPLE",
        "gcs-signer",
        "compact-secret",
        "auth-token-value",
        "dXNlcjpwYXNz",
        "digest-user",
        "digest-nonce",
        "digest-response",
        "curl-access",
        "curl-signature",
        "log-nonce",
        "log-response",
        "json-password-value",
        "json-token-value",
        "json-api-key-value",
        "cookie-secret",
        "xml-secret",
        "admin@example.com",
        "hunter2",
        "secret",
        "evil.example",
        "![",
        "[pixel]:",
        "[beacon]:",
        "@everyone",
        "#123",
    ):
        assert private_value not in text


def test_support_report_direct_render_matches_worker_privacy_sanitization():
    report = _parse(_privacy_parity_payload())
    _assert_public_privacy_parity(render_issue_body(report))


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


def test_support_report_public_preview_matches_worker_adversarial_sanitization():
    payload = _parse(
        _payload(
            summary=(
                "@everyone inspect #123 ![tracker](https://evil.example/pixel) "
                "<img src=x onerror=alert(1)> at "
                "http://admin:p%40ss@192.168.1.68:8089/devices?api_key=hunter2"
            ),
            expected=(
                "Fallback 10.0.0.7 with bearer "
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature; "
                "public trace https://status.example.com/incidents/123?token=secret "
                "and [secret](https://evil.example)"
            ),
            github_username="octocat",
            getchannels_username="Free",
        )
    )

    issue_body = render_issue_body(payload)

    assert "https://status.example.com/incidents/123?token=[redacted]" in issue_body
    assert "[@octocat](https://github.com/octocat)" in issue_body
    assert "[@Free](https://community.getchannels.com/u/Free)" in issue_body
    for private_value in (
        "@everyone", "#123", "![tracker]", "<img", "192.168.1.68",
        "10.0.0.7", "hunter2", "eyJhbGci", "admin", "[secret](https://evil.example)",
    ):
        assert private_value not in issue_body


def test_support_report_neutralizes_formatted_mentions_and_issue_references():
    payload = _parse(
        _payload(
            summary="**@everyone** _@here_ [@admins] **#123** /#456",
            expected="person@example.com and safe @ text with #topic remain readable.",
        )
    )

    issue_body = render_issue_body(payload)

    for active_reference in ("@everyone", "@here", "@admins", "#123", "#456"):
        assert active_reference not in issue_body
    assert "[redacted-email]" in issue_body
    assert "safe @ text" in issue_body
    assert "#topic" in issue_body


def test_support_report_redacts_prose_headers_and_underscore_credentials():
    payload = _parse(
        _payload(
            summary=(
                "password is hunter2; token is token-value; api key is api-value; "
                "Authorization: Bearer bearer-value; Authorization: Basic basic-value; "
                "Authorization: Token auth-token-value; Basic dXNlcjpwYXNz"
            ),
            expected=(
                "access_token=access-value; refresh_token: refresh-value; "
                "client_secret is client-value; private_key=private-value; "
                "password reset page and token handling remain readable; "
                "Basic setup fails and The basic configuration is broken."
            ),
        )
    )

    issue_body = render_issue_body(payload)

    for credential_value in (
        "hunter2", "token-value", "api-value", "bearer-value", "basic-value",
        "access-value", "refresh-value", "client-value", "private-value", "auth-token-value",
        "dXNlcjpwYXNz", "Bearer",
    ):
        assert credential_value not in issue_body
    assert "password reset page and token handling remain readable" in issue_body
    assert "Basic setup fails and The basic configuration is broken" in issue_body


def test_support_report_preserves_basic_prose_and_redacts_embedded_sensitive_headers():
    report = parse_report_payload(
        json.dumps(
            _payload(
                summary="Basic test fails; Basic mode fails; Basic auth failed",
                expected=(
                    'Authorization: AWS4-HMAC-SHA256 Credential=access, '
                    'SignedHeaders=host, Signature=signature-value\n'
                    'Proxy-Authorization: Custom nonce=nonce-value, response=response-value\n'
                    'Basic dXNlcjo= and Basic OnBhc3M=\nSafe detail remains.'
                    "\ncurl -H 'Authorization: AWS4-HMAC-SHA256 Credential=curl-access, Signature=curl-sig' https://example.com"
                    "\nrequest headers: Proxy-Authorization: Custom nonce=log-nonce, response=log-response"
                    "\npayload {\"password\":\"json-pw\", 'token': 'py-token', \"api_key\":\"json-key\"}"
                    "\nlog Cookie: sid=cookie-secret\n<password>xml-secret</password>"
                    '<client_secret>xml-client</client_secret><dsn>xml-dsn</dsn>'
                    '\n{"access_token":"access-value","refresh_token":"refresh-value",'
                    '"client_secret":"client-value","private_key":"private-value",'
                    '"credential":"credential-value","secret":"secret-value",'
                    '"webhook":"webhook-value","dsn":"dsn-value"}'
                    "\ncurl -H 'Authorization: Custom trailing-secret"
                    '\nprefix "Cookie: open-cookie'
                ),
            )
        ).encode("utf-8"),
        262144,
    )
    issue_body = render_issue_body(report)

    assert "Basic test fails; Basic mode fails; Basic auth failed" in issue_body
    assert "Authorization=[redacted]" in issue_body
    assert "Proxy-Authorization=[redacted]" in issue_body
    assert issue_body.count("[redacted-credential]") >= 2
    assert "Safe detail remains." in issue_body
    for secret in ("Credential=access", "signature-value", "nonce-value", "response-value", "AWS4-HMAC", "Custom", "dXNlcjo=", "OnBhc3M="):
        assert secret not in issue_body
    for secret in ("curl-access", "curl-sig", "log-nonce", "log-response"):
        assert secret not in issue_body
    assert "curl -H 'Authorization=[redacted]' https://example.com" in issue_body
    assert "request headers: Proxy-Authorization=[redacted]" in issue_body
    for secret in ("json-pw", "py-token", "json-key", "cookie-secret", "xml-secret", "xml-client", "xml-dsn", "access-value", "refresh-value", "client-value", "private-value", "credential-value", "secret-value", "webhook-value", "dsn-value", "trailing-secret", "open-cookie"):
        assert secret not in issue_body


def test_support_report_redacts_complex_structured_values_and_non_ascii_emails():
    text = (
        '{"password":123456,"api_key":["abc123"],"client_secret":{"nested":true},'
        '"token":"abc\\\"def"} password = "abc def" '
        'authorization = Digest username=alice, response=abcdef\n'
        'josé@example.com 用户@example.com "user name"@example.com admin@localhost user@[192.168.1.5]'
    )
    redacted = redact_public_text(text)
    for private in ("123456", "abc123", "nested", "abc", "def", "abc def", "username=alice", "abcdef", "josé", "用户", "user name", "admin@localhost", "192.168.1.5"):
        assert private not in redacted


def test_support_report_redacts_multiline_structured_credentials_as_one_block():
    cases = (
        '{\n  "secret": {\n    "value": "multiline-json-secret"\n  }\n}',
        "password: |\n  multiline-yaml-secret",
        "secret: {\n  value: multiline-unquoted-secret\n}",
        '{"secret": ["multiline-first",\n"multiline-tail"]}',
        "<password>prefix<value>\nmultiline-xml-secret\n</value></password>",
        "password:\n  indented-yaml-secret",
        "password: &vault !encrypted anchored-yaml-secret\n  anchored-yaml-tail",
        'password = """toml-first\ntoml-tail"""',
        "password = '''toml-literal-first\ntoml-literal-tail'''",
        "<password><value>nested-xml-secret</value></password>",
        "<password><value>nested-xml-first</value>\n<more>nested-xml-tail</more></password>",
    )
    for text in cases:
        redacted = redact_public_text(text)
        assert redacted == "[redacted-structured-data]"
        assert "multiline" not in redacted
    assert "multiline-xml-secret" not in redacted


def test_support_report_preserves_prose_around_sensitive_structured_continuations():
    redacted = redact_public_text(
        "Before remains.\n"
        "password = ini-first-secret\n"
        "  ini-continuation-secret\n"
        "After remains.\n"
        "XML before <password><value>xml-inner-secret</value></password> XML after."
    )
    assert "Before remains." in redacted
    assert "After remains." in redacted
    assert "XML before" in redacted
    assert "XML after." in redacted
    for private in ("ini-first-secret", "ini-continuation-secret", "xml-inner-secret"):
        assert private not in redacted


def test_support_report_redacts_nested_and_lexically_tricky_sensitive_xml():
    cases = (
        '<password>outer<password>nested-secret</password>outer-tail</password>',
        '<cfg:password>outer<cfg:password>namespace-secret</cfg:password>tail</cfg:password>',
        '<password note="fake </password>"><![CDATA[cdata </password> secret]]>'
        '<!-- comment </password> secret --><value>real-secret</value></password>',
    )
    for text in cases:
        redacted = redact_public_text(f"Before remains. {text} After remains.")
        assert "Before remains." in redacted
        assert "After remains." in redacted
        assert redacted.count("[redacted-structured-data]") == 1
        for private in ("nested-secret", "namespace-secret", "cdata", "comment", "real-secret", "outer-tail"):
            assert private not in redacted

    case_sensitive = redact_public_text(
        "Before remains. <password>case-secret</PASSWORD>case-tail</password> After remains."
    )
    assert case_sensitive == "Before remains. [redacted-structured-data] After remains."
    assert redact_public_text("Before remains.\n<password>case-secret</PASSWORD>\nunsafe-tail") == (
        "Before remains.\n[redacted-structured-data]"
    )


def test_support_report_redacts_hcl_heredocs_and_fails_closed_when_unterminated():
    for marker in ("<<EOF", "<<-EOF"):
        redacted = redact_public_text(
            f"Before remains.\npassword = {marker}\nheredoc-secret\nEOF\nsafe_field = visible"
        )
        assert "Before remains." in redacted
        assert "safe_field = visible" in redacted
        assert "heredoc-secret" not in redacted
        assert "EOF" not in redacted

    unterminated = redact_public_text("Before remains.\npassword = <<EOF\nheredoc-secret\nunsafe-tail")
    assert unterminated == "Before remains.\n[redacted-structured-data]"


def test_support_report_redacts_yaml_indentationless_sensitive_sequences_to_their_boundary():
    cases = (
        "password:\n- sequence-secret-one\n- sequence-secret-two",
        "password:\n- name: first\n  value: nested-secret-one\n- name: second\n  config:\n    value: nested-secret-two",
        "password:\n# private sequence follows\n- |\n  block-secret\n- key:\n    nested: mapping-secret",
    )
    for text in cases:
        assert redact_public_text(text) == "[redacted-structured-data]"

    for marker in ("---", "..."):
        redacted = redact_public_text(
            "Before remains.\npassword:\n- sequence-secret\n- nested:\n    value: nested-secret\n"
            f"{marker}\nsafe_field: visible"
        )
        assert "Before remains." in redacted
        assert marker in redacted
        assert "safe_field: visible" in redacted
        assert "sequence-secret" not in redacted
        assert "nested-secret" not in redacted

    sibling = redact_public_text("password:\n- sequence-secret\n  continuation-secret\nsafe_field: visible")
    assert sibling == "[redacted-structured-data]\nsafe_field: visible"


def test_support_report_redacts_yaml_node_properties_before_sensitive_values():
    cases = (
        "password: &anchor\n- property-secret-one\n- property-secret-two",
        "password: !vault\n- tagged-secret-one\n- tagged-secret-two",
        "password: &anchor !!seq [flow-secret-one,\nflow-secret-two]",
        "password: !<tag:example.com,2026:secret> {nested:\n  value: tagged-flow-secret}",
        "password: !!str &anchor |-\n  property-block-secret",
        "password: &anchor # sequence follows\n- commented-property-secret",
    )
    for text in cases:
        assert redact_public_text(text) == "[redacted-structured-data]"

    sibling = redact_public_text("password: &anchor\n- property-secret\nsafe_field: visible")
    assert sibling == "[redacted-structured-data]\nsafe_field: visible"


def test_support_report_redacts_yaml_explicit_sensitive_mapping_keys():
    cases = (
        "? password\n:\n  folded plain secret\n  continuation secret",
        "? 'password'\n: |\n  block scalar secret",
        '? "password"\n:\n- sequence secret one\n- nested:\n    value: sequence secret two',
        '? !!str &key-anchor "password"\n:\n  property key secret',
        "? password\n: &value-anchor # sequence follows\n- explicit property sequence secret",
        "? password\n: [flow secret one,\n  {nested: flow secret two}]",
        "? password\n:\n  nested:\n    child: nested structure secret",
    )
    for text in cases:
        assert redact_public_text(text) == "[redacted-structured-data]"

    for marker in ("---", "..."):
        redacted = redact_public_text(
            f"Before remains.\n? password\n:\n- explicit secret\n{marker}\nsafe_field: visible"
        )
        assert "Before remains." in redacted
        assert marker in redacted
        assert "safe_field: visible" in redacted
        assert "explicit secret" not in redacted

    sibling = redact_public_text("? password\n:\n  explicit secret\nsafe_field: visible")
    assert sibling == "[redacted-structured-data]\nsafe_field: visible"


def test_support_report_recursively_redacts_complete_json_with_decoded_sensitive_keys():
    cases = (
        ('{"pass\\u0077ord":"scalar-secret","safe":"visible"}', ("scalar-secret",), ("safe", "visible")),
        (
            '{"safe":{"name":"visible","secr\\u0065t":{"deep":"object-secret"}},'
            '"items":[{"tok\\u0065n":["array-secret-one","array-secret-two"]}],"keep":[1,2]}',
            ("object-secret", "array-secret-one", "array-secret-two"),
            ("visible", '"keep":[1,2]'),
        ),
        (
            '[{"client\\u005fsecret":"nested-secret"},{"safe":"array-visible"}]',
            ("nested-secret",),
            ("array-visible",),
        ),
        (
            '{\n  "safe": "pretty-visible",\n  "pass\\u0077ord": [\n    "pretty-secret"\n  ]\n}',
            ("pretty-secret",),
            ("pretty-visible",),
        ),
    )
    for text, private_values, public_values in cases:
        redacted = redact_public_text(text)
        for private in private_values:
            assert private not in redacted
        for public in public_values:
            assert public in redacted
        assert "[redacted]" in redacted

    non_sensitive = '{"safe":"visible","count":2}'
    assert redact_public_text(non_sensitive) == non_sensitive
    mixed = redact_public_text('Before {"password":"mixed-secret"} After')
    assert "Before" in mixed and "After" in mixed and "mixed-secret" not in mixed


def test_support_report_redacts_yaml_multiline_explicit_sensitive_scalar_keys():
    cases = (
        "?\n  password\n:\n  - multiline-key-secret-one\n  - multiline-key-secret-two",
        '?\n  !!str &key-anchor "password"\n:\n  nested:\n    value: property-key-secret',
        "?\n  'password'\n: [flow-key-secret-one,\n  {nested: flow-key-secret-two}]",
        "?\n  !<tag:yaml.org,2002:str> password\n: |\n  block-key-secret",
    )
    for text in cases:
        assert redact_public_text(text) == "[redacted-structured-data]"

    sibling = redact_public_text("?\n  password\n:\n  - multiline-key-secret\nsafe_field: visible")
    assert sibling == "[redacted-structured-data]\nsafe_field: visible"
    for marker in ("---", "..."):
        redacted = redact_public_text(
            f"Before remains.\n?\n  password\n:\n  - multiline-key-secret\n{marker}\nsafe_field: visible"
        )
        assert marker in redacted and "safe_field: visible" in redacted
        assert "multiline-key-secret" not in redacted


def test_support_report_decodes_yaml_quoted_sensitive_keys_before_redaction():
    cases = (
        '"pass\\u0077ord": [unicode-secret-one, unicode-secret-two]',
        '"\\x70assword": {nested: hex-secret}',
        '"\\U00000070assword": |\n  long-unicode-secret',
        '? "passw\\x6frd"\n:\n  - same-line-explicit-secret',
        '?\n  !!str &key-anchor "pass\\u0077ord"\n: [multiline-explicit-secret]',
        '?\n  "pass\\qword"\n:\n  malformed-escape-secret',
    )
    for text in cases:
        redacted = redact_public_text(text)
        assert "secret" not in redacted
        assert "[redacted-structured-data]" in redacted

    sibling = redact_public_text(
        '?\n  "pass\\u0077ord"\n:\n  - escaped-key-secret\nsafe_field: visible'
    )
    assert sibling == "[redacted-structured-data]\nsafe_field: visible"
    single_quoted = "'pass''word': visible\n? 'pass''word'\n: still-visible"
    assert redact_public_text(single_quoted) == single_quoted
    standard_escapes = '"safe\\/key": visible\n? "safe\\tkey"\n: still-visible'
    assert redact_public_text(standard_escapes) == standard_escapes


def test_support_report_redacts_decoded_sensitive_keys_in_embedded_json():
    cases = (
        ('Before {"pass\\u0077ord":"embedded-secret"} After', "embedded-secret", ("Before", "After")),
        (
            'Prefix [{"safe":"visible"},{"tok\\u0065n":["array-secret"]}] Suffix',
            "array-secret",
            ("Prefix", "visible", "Suffix"),
        ),
        (
            'Before {\n  "safe": {"name": "nested-visible"},\n'
            '  "secr\\u0065t": {"value": "nested-secret"}\n} After',
            "nested-secret",
            ("Before", "nested-visible", "After"),
        ),
    )
    for text, private_value, public_values in cases:
        redacted = redact_public_text(text)
        assert private_value not in redacted
        for public in public_values:
            assert public in redacted


def test_support_report_classifies_composite_sensitive_structured_keys():
    complete = redact_public_text(
        '{"github_token":"github-secret","database_password":"database-secret",'
        '"smtpPassword":"smtp-secret","webhook_url":"webhook-secret",'
        '"token_count":4,"password_policy":"strict","safe":"visible"}'
    )
    for private in ("github-secret", "database-secret", "smtp-secret", "webhook-secret"):
        assert private not in complete
    for public in ('"token_count":4', '"password_policy":"strict"', '"safe":"visible"'):
        assert public in complete

    embedded = redact_public_text(
        'Before {"nested":{"provider.notification_credentials":"provider-secret",'
        '"clientSecret":"client-secret"},"session_count":2} After'
    )
    assert "provider-secret" not in embedded and "client-secret" not in embedded
    assert "Before" in embedded and '"session_count":2' in embedded and "After" in embedded

    yaml = redact_public_text(
        "github_token: yaml-token-secret\n"
        "database_password:\n  nested-password-secret\n"
        "smtpPassword: {value: smtp-password-secret}\n"
        "webhook_url: |\n  webhook-body-secret\n"
        "? notificationCredential\n:\n  provider-notification-secret\n"
        "token_count: 4\npassword_policy: strict"
    )
    for private in (
        "yaml-token-secret",
        "nested-password-secret",
        "smtp-password-secret",
        "webhook-body-secret",
        "provider-notification-secret",
    ):
        assert private not in yaml
    assert "token_count: 4" in yaml and "password_policy: strict" in yaml


def test_support_report_redacts_provider_access_identifier_composites():
    complete = redact_public_text(
        '{"AWSAccessKeyId":"aws-camel-secret","aws_access_key_id":"aws-snake-secret",'
        '"GoogleAccessId":"google-access-secret","KeyPairId":"key-pair-secret",'
        '"access_key_count":4,"access_policy":"read-only","provider_id":"visible-id"}'
    )
    for private in ("aws-camel-secret", "aws-snake-secret", "google-access-secret", "key-pair-secret"):
        assert private not in complete
    for public in ('"access_key_count":4', '"access_policy":"read-only"', '"provider_id":"visible-id"'):
        assert public in complete

    embedded = redact_public_text(
        'Before {"provider":{"AWSAccessKeyId":{"value":"nested-provider-secret"}},'
        '"key_pair_count":2} After'
    )
    assert "nested-provider-secret" not in embedded
    assert "Before" in embedded and '"key_pair_count":2' in embedded and "After" in embedded

    yaml = redact_public_text(
        "AWSAccessKeyId: yaml-aws-secret\n"
        "GoogleAccessId:\n  yaml-google-secret\n"
        "KeyPairId: [yaml-pair-secret]\n"
        "access_key_count: 4\nkey_pair_count: 2\nprovider_id: visible-id"
    )
    for private in ("yaml-aws-secret", "yaml-google-secret", "yaml-pair-secret"):
        assert private not in yaml
    assert "access_key_count: 4" in yaml and "key_pair_count: 2" in yaml and "provider_id: visible-id" in yaml

    xml = redact_public_text(
        "Before <AWSAccessKeyId><value>xml-provider-secret</value></AWSAccessKeyId> After"
    )
    assert "xml-provider-secret" not in xml and "Before" in xml and "After" in xml

    fused = redact_public_text(
        '{"githubtoken":"fused-github-secret","databasepassword":"fused-database-secret",'
        '"smtppassword":"fused-smtp-secret","notificationcredentials":"fused-notification-secret",'
        '"tokencount":3,"passwordpolicy":"strict"}'
    )
    for private in (
        "fused-github-secret",
        "fused-database-secret",
        "fused-smtp-secret",
        "fused-notification-secret",
    ):
        assert private not in fused
    assert '"tokencount":3' in fused and '"passwordpolicy":"strict"' in fused

    inline = redact_public_text(
        "Before github_token: inline-token-secret After\n"
        "log database_password=inline-password-secret other\n"
        'trace smtpPassword="inline smtp secret" remains'
    )
    for private in ("inline-token-secret", "inline-password-secret", "inline smtp secret"):
        assert private not in inline
    assert "Before github_token: [redacted] After" in inline
    assert "log database_password=[redacted] other" in inline
    assert 'trace smtpPassword=[redacted] remains' in inline

    fused_yaml_xml = redact_public_text(
        "githubtoken: fused-yaml-secret\n"
        "tokencount: 3\npasswordpolicy: strict\n"
        "Before <notificationcredentials>fused-xml-secret</notificationcredentials> After"
    )
    assert "fused-yaml-secret" not in fused_yaml_xml and "fused-xml-secret" not in fused_yaml_xml
    assert "tokencount: 3" in fused_yaml_xml and "passwordpolicy: strict" in fused_yaml_xml


def test_support_report_redacts_account_keys_queries_and_inline_credential_forms():
    json_with_query = redact_public_text(
        '{"url":"/callback?token=abc","password":"other-secret","safe":"visible"}'
    )
    parsed_query = json.loads(json_with_query)
    assert parsed_query["url"] == "/callback?token=[redacted]"
    assert parsed_query["password"] == "[redacted]" and parsed_query["safe"] == "visible"
    json_array = redact_public_text('["/callback?token=array-secret",{"safe":"visible"}]')
    parsed_array = json.loads(json_array)
    assert parsed_array == ["/callback?token=[redacted]", {"safe": "visible"}]
    full_url_json = redact_public_text(
        '{"url":"https://public.example/callback?databasepassword=url-json-secret&ok=1",'
        '"safe":"visible"}'
    )
    parsed_full_url = json.loads(full_url_json)
    assert "url-json-secret" not in parsed_full_url["url"]
    assert "ok=1" in parsed_full_url["url"] and parsed_full_url["safe"] == "visible"
    punctuation = redact_public_text(
        'See (/callback?token=paren-secret). next; quoted "/callback?token=quote-secret", '
        "backticked `/callback?token=tick-secret`; [/callback?token=bracket-secret] "
        "{/callback?token=brace-secret}."
    )
    for private in ("paren-secret", "quote-secret", "tick-secret", "bracket-secret", "brace-secret"):
        assert private not in punctuation
    for delimiter in ("). next", '"', "`", "]", "}." ):
        assert delimiter in punctuation

    safe_is_prose = (
        "Auth is failing today\nSession is expired after reboot\nSignature is invalid\n"
        '{"message":"Auth is failing today; Session is expired after reboot; Signature is invalid",'
        '"safe":"visible"}'
    )
    assert redact_public_text(safe_is_prose) == safe_is_prose

    complete = redact_public_text(
        '{"accountKey":"account-secret","storageAccountKey":"storage-secret",'
        '"accountkey":"fused-account-secret",'
        '"connectionString":"DefaultEndpointsProtocol=https;AccountName=visible;'
        'AccountKey=connection-secret;EndpointSuffix=core.windows.net",'
        '"accesskey":"direct-access-key","secretkey":"direct-secret-key",'
        '"signature":"signature-secret","x-amz-signature":"amz-signature-secret",'
        '"awssecretaccesskey":"aws-secret-access-key","azureaccesskeyid":"azure-access-id",'
        '"oracleaccesskeyid":"oracle-access-id","s3accesskeyid":"s3-access-id",'
        '"provideraccessid":"provider-access-id","sessionid":"session-secret",'
        '"authkey":"auth-secret","account_key_count":2,"accesskeycount":3,'
        '"hmackeypairid":"hmac-pair-secret","githubapikey":"github-api-secret",'
        '"githubprivatekey":"github-private-secret","codesigningkey":"signing-secret",'
        '"discordwebhookurl":"discord-webhook-secret","sentrydsn":"sentry-dsn-secret",'
        '"access_policy":"visible-policy","providerid":"visible-provider"}'
    )
    for private in (
        "account-secret", "storage-secret", "fused-account-secret", "connection-secret",
        "direct-access-key", "direct-secret-key", "signature-secret", "amz-signature-secret", "aws-secret-access-key", "azure-access-id",
        "oracle-access-id", "s3-access-id", "provider-access-id", "session-secret", "auth-secret",
        "hmac-pair-secret", "github-api-secret", "github-private-secret", "signing-secret",
        "discord-webhook-secret", "sentry-dsn-secret",
    ):
        assert private not in complete
    for public in (
        "AccountName=visible", "EndpointSuffix=core.windows.net", '"account_key_count":2',
        '"accesskeycount":3', '"access_policy":"visible-policy"', '"providerid":"visible-provider"',
    ):
        assert public in complete

    structured = redact_public_text(
        "accountKey: yaml-account-secret\n"
        "storageAccountKey: yaml-storage-secret\n"
        "account_key_count: 2\n"
        "Before <accountKey>xml-account-key</accountKey><secretkey>xml-secret-key</secretkey> After"
    )
    assert "yaml-account-secret" not in structured and "yaml-storage-secret" not in structured
    assert "xml-account-key" not in structured and "xml-secret-key" not in structured and "account_key_count: 2" in structured
    assert "Before" in structured and "After" in structured

    inline = redact_public_text(
        "Azure DefaultEndpointsProtocol=https;AccountName=visible;AccountKey=inline-account-secret;"
        "EndpointSuffix=core.windows.net remains\n"
        "github_token=Bearer bearer-secret after\n"
        "database_password:=Basic basic-secret next\n"
        "authkey=>Token token-secret done\n"
        "github_token is word-secret final\n"
        'Before github_token: "unterminated-secret\n'
        "Before database_password='unterminated-password"
    )
    for private in (
        "inline-account-secret", "bearer-secret", "basic-secret", "token-secret", "word-secret",
        "unterminated-secret", "unterminated-password",
    ):
        assert private not in inline
    for public in ("AccountName=visible", "EndpointSuffix=core.windows.net", "after", "next", "done", "final"):
        assert public in inline

    queries = redact_public_text(
        "/callback?github_token=query-secret&ok=1\n"
        "https://public.example/callback?databasepassword=url-secret&ok=2\n"
        "/callback?github%5Ftoken=encoded-secret&safe=3\n"
        "/download?accesskey=access-key-secret&account_key_count=4&providerid=visible"
    )
    for private in ("query-secret", "url-secret", "encoded-secret", "access-key-secret"):
        assert private not in queries
    for public in ("ok=1", "ok=2", "safe=3", "account_key_count=4", "providerid=visible"):
        assert public in queries

    boundary_safe_key = "a" * 256
    boundary_sensitive_key = "a" * 251 + "token"
    overlong_key = "a" * 252 + "token"
    very_long_key = "z" * 10000
    encoded_overlong_key = "a" * 252 + "%74oken"
    query_lengths = redact_public_text(
        f"/cb?{boundary_safe_key}=BOUNDARY_VISIBLE&ok=1\n"
        f"/cb?{boundary_sensitive_key}=BOUNDARY_SECRET&ok=2\n"
        f"/cb?{overlong_key}=OVERLONG_VALUE&ok=3\n"
        f"/cb?{very_long_key}=VERY_LONG_VALUE&ok=4\n"
        f"/cb?{encoded_overlong_key}=ENCODED_OVERLONG_VALUE&ok=5"
    )
    assert "BOUNDARY_VISIBLE" in query_lengths
    for private in ("BOUNDARY_SECRET", "OVERLONG_VALUE", "VERY_LONG_VALUE", "ENCODED_OVERLONG_VALUE"):
        assert private not in query_lengths
    for safe_parameter in ("ok=1", "ok=2", "ok=3", "ok=4", "ok=5"):
        assert safe_parameter in query_lengths


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


def test_support_report_dry_run_endpoint_matches_worker_privacy_sanitization():
    import ui.backend.main as ui_main

    with patch("ui.backend.main.CW_DISABLE_AUTH", True):
        with TestClient(ui_main.app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/v1/support/report-dry-run",
                json=_privacy_parity_payload(),
            )

    assert response.status_code == 200, response.text
    _assert_public_privacy_parity(response.json()["issue_body"])


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


def test_support_report_offline_issue_preview_matches_worker_privacy_sanitization():
    import ui.backend.main as ui_main

    payload = _privacy_parity_payload()
    with patch("ui.backend.main.CW_DISABLE_AUTH", True):
        with TestClient(ui_main.app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/v1/support/offline-package",
                json={"support_code": _schema2_code(payload)},
            )

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content), "r") as package:
        issue_preview = package.read("issue-preview.md").decode("utf-8")
    _assert_public_privacy_parity(issue_preview)
