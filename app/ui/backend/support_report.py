from __future__ import annotations

import io
import ipaddress
import json
import re
import struct
import zlib
import zipfile
import base64
from html import escape as html_escape
from hashlib import sha256
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import parse_qsl, quote, unquote_plus, urlencode, urlsplit, urlunsplit

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
SCREENSHOT_MAX_DIMENSION = 8192
SCREENSHOT_MAX_PIXELS = 40_000_000
SCREENSHOT_MAX_DECODED_BYTES = 160 * 1024 * 1024
DEBUG_BUNDLE_MAX_COMPRESSION_RATIO = 100
PUBLIC_APP_URL = "https://channelwatch.coderluii.dev"
DEFAULT_REPORT_PORTAL_URL = f"{PUBLIC_APP_URL}/report"
GETCHANNELS_PROFILE_BASE = "https://community.getchannels.com/u"
GITHUB_PROFILE_BASE = "https://github.com"
CHANNELWATCH_LOGO_BASE64 = "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"
CHANNELWATCH_LOGO_DATA_URI = f"data:image/png;base64,{CHANNELWATCH_LOGO_BASE64}"

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_GITHUB_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_GETCHANNELS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PUBLIC_EMAIL_RE = re.compile(r'"[^"\r\n]+"@[^\s<>()]+|[^\s<>()@]+@[^\s<>()]+', re.I)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|"
    r"client[\s_-]*secret|private[\s_-]*key|authorization|credential|token|"
    r"secret|password|passwd|webhook|dsn)\b\s*(?:(?:is)\b\s*|[:=]\s*)"
    r"(?:(?:bearer|basic|token)\s+)?([^\s,;]+)"
)
_BEARER_CREDENTIAL_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_BASIC_CREDENTIAL_RE = re.compile(
    r"(?i)\bbasic\s+((?:[a-z0-9+/]{4})*"
    r"(?:[a-z0-9+/]{4}|[a-z0-9+/]{2}==|[a-z0-9+/]{3}=))"
    r"(?=$|[\s,;.!?'\"])",
)
_QUOTED_SENSITIVE_HEADER_RE = re.compile(r"(['\"])\b(proxy-authorization|authorization|set-cookie|cookie)\s*:[^\r\n]*?\1", re.I)
_UNCLOSED_SENSITIVE_HEADER_RE = re.compile(r"(['\"])\b(proxy-authorization|authorization|set-cookie|cookie)\s*:[^\r\n]*$", re.I | re.M)
_SENSITIVE_HEADER_RE = re.compile(r"(^|[^'\"])\b(proxy-authorization|authorization|set-cookie|cookie)\s*[:=][^\r\n]*", re.I | re.M)
_STRUCTURED_SENSITIVE_KEY = (
    r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"private[_ -]?key|authorization|credential|token|secret|password|passwd|webhook|dsn"
)
_QUOTED_SENSITIVE_VALUE_RE = re.compile(
    rf"(['\"])({_STRUCTURED_SENSITIVE_KEY})\1\s*:\s*"
    rf"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\[[^\]\r\n]*\]|\{{[^}}\r\n]*\}}|[^,}}\r\n]+)", re.I
)
_UNQUOTED_KEY_QUOTED_VALUE_RE = re.compile(
    rf"\b({_STRUCTURED_SENSITIVE_KEY})\b\s*(?:(?:is)\b\s*|[:=]\s*)"
    rf"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')", re.I
)
_XML_SENSITIVE_VALUE_RE = re.compile(rf"<({_STRUCTURED_SENSITIVE_KEY})>[^<\r\n]*</\1>", re.I)
_STRUCTURED_ASSIGNMENT_RE = re.compile(
    rf"(?<![\w-])(?:(?P<double>\"(?:\\.|[^\"\\\r\n])*\")|"
    rf"(?P<single>'(?:''|[^'\r\n])*')|(?P<plain>[A-Za-z][A-Za-z0-9_./-]{{0,127}}))"
    rf"(?:[ \t]*(?P<operator>=>|:=|[:=])[ \t]*|[ \t]+(?P<word_operator>is)[ \t]+)",
    re.I,
)
_QUERY_ASSIGNMENT_RE = re.compile(
    r'''(?P<prefix>[?&;])(?P<key>[^=&;#\s"'`,}\])]+)=(?P<value>[^&;#\s"'`,}\])]*?)'''
    r"(?=$|[&;#\s\"'`,}\])])"
)
_LEGACY_STRUCTURED_SCALAR_KEY_RE = re.compile(
    rf"(?:{_STRUCTURED_SENSITIVE_KEY}|proxy-authorization|set-cookie|cookie)", re.I
)
_EXPLICIT_YAML_KEY_INDICATOR_RE = re.compile(
    r"^(?P<indent>[ \t]*)\?(?P<tail>[^\r\n]*)(?:\r?\n|$)", re.M
)
_SENSITIVE_STRUCTURED_COMPONENTS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "privatekey",
    "signingkey",
    "webhookurl",
    "accesskeyid",
    "awsaccesskeyid",
    "googleaccessid",
    "keypairid",
    "sig",
    "signature",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "token",
    "tokens",
    "secret",
    "secrets",
    "password",
    "passwd",
    "webhook",
    "dsn",
    "cookie",
    "cookies",
    "session",
    "sessions",
}
_SENSITIVE_STRUCTURED_PHRASES = {
    ("access", "key"),
    ("access", "id"),
    ("account", "key"),
    ("key", "pair", "id"),
    ("secret", "key"),
    ("api", "key"),
    ("private", "key"),
    ("signing", "key"),
    ("access", "token"),
    ("refresh", "token"),
    ("client", "secret"),
}
_SAFE_STRUCTURED_METADATA_SUFFIXES = {"count", "counts", "policy", "policies"}
_SENSITIVE_FUSED_STRUCTURED_SUFFIXES = (
    "keypairid",
    "apikey",
    "privatekey",
    "signingkey",
    "webhookurl",
    "dsn",
    "accesskeyid",
    "accessid",
    "accesskey",
    "accountkey",
    "secretkey",
    "authkey",
    "sessionid",
    "signature",
    "credentials",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)
_MAX_EMBEDDED_JSON_CANDIDATES = 64
_MAX_EMBEDDED_JSON_CHARS = 64 * 1024
_LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
_PUBLIC_URL_RE = re.compile(r'''https?://[^\s<>()"'`,}]+''', re.I)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_PRIVATE_IPV4_RE = re.compile(
    r"\b(?:10|127)\.(?:\d{1,3}\.){2}\d{1,3}\b|"
    r"\b169\.254\.(?:\d{1,3}\.)\d{1,3}\b|"
    r"\b172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3}\b|"
    r"\b192\.168\.(?:\d{1,3}\.)\d{1,3}\b"
)
_IPV6_CANDIDATE_RE = re.compile(
    r"(?:(?:[0-9a-f]{1,4}:){2,}[0-9a-f:.]*|[0-9a-f]{1,4}::[0-9a-f:.]*|::[0-9a-f:.]+)"
    r"(?:%[a-z0-9_.~-]+)?(?:/\d{1,3})?",
    re.I,
)
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


def _parse_whatwg_ipv4(address_text: str) -> ipaddress.IPv4Address | None:
    parts = address_text.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        return None
    numbers: list[int] = []
    for part in parts:
        lowered = part.lower()
        if lowered.startswith("0x"):
            digits, base = lowered[2:], 16
            if not digits or not re.fullmatch(r"[0-9a-f]+", digits):
                return None
        elif len(part) > 1 and part.startswith("0"):
            digits, base = part[1:], 8
            if not digits or not re.fullmatch(r"[0-7]+", digits):
                return None
        else:
            digits, base = part, 10
            if not digits.isdecimal():
                return None
        numbers.append(int(digits, base))
    if any(number > 255 for number in numbers[:-1]):
        return None
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        return None
    value = numbers[-1]
    for index, number in enumerate(numbers[:-1]):
        value += number << (8 * (3 - index))
    return ipaddress.IPv4Address(value)


def _is_private_hostname(hostname: str | None) -> bool:
    normalized = (
        (hostname or "")
        .lower()
        .translate(str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."}))
        .strip("[]")
        .removesuffix(".")
    )
    if normalized in {"localhost", "::1"} or normalized.endswith(".local"):
        return True
    address_text = normalized.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        address = _parse_whatwg_ipv4(address_text)
        if address is None:
            if re.fullmatch(r"(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*", address_text):
                return True
            return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return _is_private_hostname(str(address.ipv4_mapped))
        return address.is_loopback or address.is_link_local or address in ipaddress.ip_network("fc00::/7")
    first, second = int(str(address).split(".")[0]), int(str(address).split(".")[1])
    return (
        first in {10, 127}
        or (first == 169 and second == 254)
        or (first == 172 and 16 <= second <= 31)
        or (first == 192 and second == 168)
    )


def _classify_private_ipv6(address_text: str) -> bool | None:
    address_text = address_text.split("/", 1)[0].split("%", 1)[0]
    try:
        address = ipaddress.IPv6Address(address_text)
    except ValueError:
        if "." not in address_text or ":" not in address_text:
            return None
        prefix, dotted_tail = address_text.rsplit(":", 1)
        parts = dotted_tail.split(".")
        if len(parts) != 4 or any(not part.isdecimal() for part in parts):
            return None
        octets = [int(part, 10) for part in parts]
        if any(octet > 255 for octet in octets):
            return None
        normalized = f"{prefix}:{(octets[0] << 8) | octets[1]:x}:{(octets[2] << 8) | octets[3]:x}"
        try:
            address = ipaddress.IPv6Address(normalized)
        except ValueError:
            return None
    if address.ipv4_mapped:
        return _is_private_hostname(str(address.ipv4_mapped))
    return address.is_loopback or address.is_link_local or address in ipaddress.ip_network("fc00::/7")


def _redact_private_ipv6_candidate(match: re.Match[str]) -> str:
    candidate = match.group(0)
    address_text = candidate
    punctuation = ""
    while address_text:
        classification = _classify_private_ipv6(address_text)
        if classification is not None:
            return f"[redacted-private-address]{punctuation}" if classification else candidate
        if address_text[-1] not in ".:":
            return candidate
        punctuation = address_text[-1] + punctuation
        address_text = address_text[:-1]
    return candidate


def _redact_standalone_basic(match: re.Match[str]) -> str:
    try:
        decoded = base64.b64decode(match.group(1), validate=True)
    except (ValueError, TypeError):
        return match.group(0)
    return "[redacted-credential]" if b":" in decoded else match.group(0)


def _is_sensitive_query_key(key: str) -> bool:
    if re.search(r"%(?![0-9A-Fa-f]{2})", key):
        return True
    try:
        decoded = unquote_plus(key)
    except (UnicodeDecodeError, ValueError):
        return True
    normalized = re.sub(r"[^a-z0-9]", "", decoded.lower())
    return normalized in {"code", "key", "policy", "expires"} or _is_sensitive_structured_key(decoded)


def _redact_sensitive_query_fragments(value: str) -> str:
    def redact(match: re.Match[str]) -> str:
        if len(match.group("key")) <= 256 and not _is_sensitive_query_key(match.group("key")):
            return match.group(0)
        raw_value = match.group("value")
        suffix_match = re.search(r"[.!?]+$", raw_value)
        suffix = suffix_match.group(0) if suffix_match else ""
        return f"{match.group('prefix')}{match.group('key')}=[redacted]{suffix}"

    return _QUERY_ASSIGNMENT_RE.sub(
        redact,
        value,
    )


def _sanitize_public_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    punctuation = ""
    while raw.endswith((".", "!")):
        punctuation = raw[-1] + punctuation
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        if parsed.username or parsed.password or _is_private_hostname(parsed.hostname):
            return f"[redacted-private-url]{punctuation}"
        query = urlencode(
            [
                (key, "[redacted]" if _is_sensitive_query_key(key) else value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, "")).replace(
            "%5Bredacted%5D", "[redacted]"
        ) + punctuation
    except ValueError:
        return f"[redacted-url]{punctuation}"


def _find_balanced_value_end(value: str, start: int) -> int | None:
    pairs = {"{": "}", "[": "]"}
    opening = value[start]
    closing = pairs.get(opening)
    if not closing:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(value)):
        character = value[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in "'\"":
            quote = character
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _indented_continuation_end(value: str, line_end: int, base_indent: int) -> int:
    if line_end >= len(value):
        return line_end
    cursor = line_end + (2 if value.startswith("\r\n", line_end) else 1)
    end = line_end
    while cursor <= len(value):
        next_end = value.find("\n", cursor)
        next_end = len(value) if next_end < 0 else next_end
        line = value[cursor:next_end].rstrip("\r")
        indent = len(line) - len(line.lstrip(" \t"))
        if line.strip() and indent <= base_indent:
            break
        end = next_end
        if next_end >= len(value):
            break
        cursor = next_end + 1
    return end


def _indentationless_sequence_end(value: str, line_end: int, base_indent: int) -> int:
    if line_end >= len(value):
        return line_end
    cursor = line_end + 1
    end = line_end
    pending_end = line_end
    saw_sequence = False
    while cursor <= len(value):
        next_end = value.find("\n", cursor)
        next_end = len(value) if next_end < 0 else next_end
        line = value[cursor:next_end].rstrip("\r")
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        if indent == 0 and stripped in {"---", "..."}:
            break
        if not stripped or stripped.startswith("#"):
            pending_end = next_end
        elif indent == base_indent and re.match(r"-(?:[ \t]|$)", stripped):
            saw_sequence = True
            end = next_end
        elif saw_sequence and indent > base_indent:
            end = next_end
        else:
            break
        if saw_sequence and pending_end > end:
            end = pending_end
        if next_end >= len(value):
            break
        cursor = next_end + 1
    return end if saw_sequence else line_end


def _strip_yaml_node_properties(value: str) -> tuple[str, int, bool]:
    cursor = 0
    found = False
    while cursor < len(value):
        whitespace_end = cursor
        while whitespace_end < len(value) and value[whitespace_end] in " \t":
            whitespace_end += 1
        token_start = whitespace_end
        if token_start >= len(value) or value[token_start] not in "!&":
            break
        if value.startswith("!<", token_start):
            token_end = value.find(">", token_start + 2)
            if token_end < 0:
                return "", len(value), True
            token_end += 1
        else:
            token_end = token_start + 1
            while token_end < len(value) and value[token_end] not in " \t":
                token_end += 1
        if token_end == token_start + 1:
            break
        found = True
        cursor = token_end
    while cursor < len(value) and value[cursor] in " \t":
        cursor += 1
    if value.startswith("#", cursor):
        return "", len(value), found
    return value[cursor:], cursor, found


def _is_sensitive_structured_key(value: str) -> bool:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", separated)
    components = [component.lower() for component in re.findall(r"[A-Za-z0-9]+", separated)]
    compact = "".join(components)
    if (
        not components
        or components[-1] in _SAFE_STRUCTURED_METADATA_SUFFIXES
        or (
            len(components) == 1
            and compact.endswith(tuple(_SAFE_STRUCTURED_METADATA_SUFFIXES))
        )
    ):
        return False
    if any(component in _SENSITIVE_STRUCTURED_COMPONENTS for component in components):
        return True
    if len(components) == 1 and compact.endswith(_SENSITIVE_FUSED_STRUCTURED_SUFFIXES):
        return True
    return any(
        components[index : index + len(phrase)] == list(phrase)
        for phrase in _SENSITIVE_STRUCTURED_PHRASES
        for index in range(len(components) - len(phrase) + 1)
    )


def _decode_yaml_double_quoted_key(value: str) -> str | None:
    if len(value) > 512:
        return None
    escapes = {
        "0": "\0",
        "a": "\a",
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "v": "\v",
        "f": "\f",
        "r": "\r",
        "e": "\x1b",
        " ": " ",
        '"': '"',
        "/": "/",
        "\\": "\\",
        "N": "\u0085",
        "_": "\u00a0",
        "L": "\u2028",
        "P": "\u2029",
    }
    decoded: list[str] = []
    cursor = 0
    while cursor < len(value):
        character = value[cursor]
        if character != "\\":
            if ord(character) < 0x20:
                return None
            decoded.append(character)
            cursor += 1
            continue
        if cursor + 1 >= len(value):
            return None
        escape = value[cursor + 1]
        if escape in escapes:
            decoded.append(escapes[escape])
            cursor += 2
            continue
        width = {"x": 2, "u": 4, "U": 8}.get(escape)
        if width is None or cursor + 2 + width > len(value):
            return None
        digits = value[cursor + 2 : cursor + 2 + width]
        if not re.fullmatch(r"[0-9A-Fa-f]+", digits):
            return None
        codepoint = int(digits, 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return None
        decoded.append(chr(codepoint))
        cursor += 2 + width
    return "".join(decoded)


def _decode_yaml_quoted_key(value: str) -> tuple[str | None, bool]:
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            return None, True
        decoded = _decode_yaml_double_quoted_key(value[1:-1])
        return decoded, decoded is None
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'") or len(value) > 512:
            return None, True
        inner = value[1:-1]
        decoded: list[str] = []
        cursor = 0
        while cursor < len(inner):
            if inner[cursor] != "'":
                decoded.append(inner[cursor])
                cursor += 1
            elif cursor + 1 < len(inner) and inner[cursor + 1] == "'":
                decoded.append("'")
                cursor += 2
            else:
                return None, True
        return "".join(decoded), False
    return value, False


def _is_sensitive_yaml_explicit_key(value: str) -> bool:
    semantic, _, _ = _strip_yaml_node_properties(value.strip())
    semantic = re.sub(r"[ \t]+#[^\r\n]*$", "", semantic).strip()
    decoded, malformed = _decode_yaml_quoted_key(semantic)
    return malformed or (decoded is not None and _is_sensitive_structured_key(decoded))


def _structured_assignment_is_sensitive(match: re.Match[str]) -> bool:
    quoted = match.group("double") or match.group("single")
    if quoted is None:
        decoded, malformed = match.group("plain") or "", False
    else:
        decoded, malformed = _decode_yaml_quoted_key(quoted)
    if malformed or decoded is None:
        return True
    if match.group("word_operator"):
        separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", decoded)
        separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", separated)
        components = [component.lower() for component in re.findall(r"[A-Za-z0-9]+", separated)]
        composite_shape = bool(re.search(r"[_./-]|[a-z0-9][A-Z]", decoded))
        fused_shape = (
            len(components) == 1
            and components[0] not in _SENSITIVE_STRUCTURED_COMPONENTS
            and components[0].endswith(_SENSITIVE_FUSED_STRUCTURED_SUFFIXES)
        )
        if not composite_shape and not fused_shape:
            return False
    return _is_sensitive_structured_key(decoded)


def _explicit_yaml_value_end(value: str, rhs_start: int, line_end: int, base_indent: int) -> int:
    rhs = value[rhs_start:line_end].rstrip("\r")
    stripped_rhs = rhs.lstrip()
    value_start = rhs_start + len(rhs) - len(stripped_rhs)
    semantic_rhs, property_offset, had_properties = _strip_yaml_node_properties(stripped_rhs)
    semantic_start = value_start + property_offset
    if semantic_rhs.startswith(("{", "[")):
        return _find_balanced_value_end(value, semantic_start) or len(value)
    sequence_end = _indentationless_sequence_end(value, line_end, base_indent) if not semantic_rhs else line_end
    continuation_end = max(_indented_continuation_end(value, line_end, base_indent), sequence_end)
    yaml_marker = re.fullmatch(r"(?:[|>][+-]?\d?|[|>]\d?[+-]?)", semantic_rhs)
    if yaml_marker or continuation_end > line_end or not semantic_rhs or had_properties:
        return continuation_end if continuation_end > line_end else line_end
    return line_end


def _redact_explicit_yaml_sensitive_values(value: str) -> str:
    cursor = 0
    while match := _EXPLICIT_YAML_KEY_INDICATOR_RE.search(value, cursor):
        base_indent = len(match.group("indent"))
        tail = match.group("tail").strip()
        key_end = match.end()
        sensitive = bool(tail and not tail.startswith("#") and _is_sensitive_yaml_explicit_key(tail))
        if not tail or tail.startswith("#"):
            line_cursor = match.end()
            while line_cursor <= len(value):
                next_end = value.find("\n", line_cursor)
                next_end = len(value) if next_end < 0 else next_end
                line = value[line_cursor:next_end].rstrip("\r")
                stripped = line.lstrip(" \t")
                indent = len(line) - len(stripped)
                if not stripped or stripped.startswith("#"):
                    if next_end >= len(value):
                        break
                    line_cursor = next_end + 1
                    continue
                if indent > base_indent:
                    sensitive = _is_sensitive_yaml_explicit_key(stripped)
                    key_end = next_end + (1 if next_end < len(value) else 0)
                break
        if not sensitive:
            cursor = match.end()
            continue

        line_cursor = key_end
        colon_start: int | None = None
        rhs_start: int | None = None
        line_end = len(value)
        while line_cursor <= len(value):
            next_end = value.find("\n", line_cursor)
            next_end = len(value) if next_end < 0 else next_end
            line = value[line_cursor:next_end].rstrip("\r")
            stripped = line.lstrip(" \t")
            indent = len(line) - len(stripped)
            if not stripped or stripped.startswith("#"):
                if next_end >= len(value):
                    break
                line_cursor = next_end + 1
                continue
            if indent == base_indent and re.match(r":(?:[ \t]|$)", stripped):
                colon_start = line_cursor
                colon_offset = indent + 1
                while colon_offset < len(line) and line[colon_offset] in " \t":
                    colon_offset += 1
                rhs_start = line_cursor + colon_offset
                line_end = next_end
            break
        start = match.start() + base_indent
        end = len(value) if colon_start is None or rhs_start is None else _explicit_yaml_value_end(
            value, rhs_start, line_end, base_indent
        )
        value = f"{value[:start]}[redacted-structured-data]{value[end:]}"
        cursor = start + len("[redacted-structured-data]")
    return value


def _redact_complete_json(value: str) -> str | None:
    trimmed = value.strip()
    if not (
        (trimmed.startswith("{") and trimmed.endswith("}"))
        or (trimmed.startswith("[") and trimmed.endswith("]"))
    ):
        return None
    try:
        parsed = json.loads(trimmed)
    except (json.JSONDecodeError, TypeError):
        return None

    def redact(node: Any) -> tuple[Any, bool]:
        if isinstance(node, dict):
            changed = False
            result: dict[str, Any] = {}
            for key, child in node.items():
                if _is_sensitive_structured_key(key):
                    result[key] = "[redacted]"
                    changed = True
                else:
                    result[key], child_changed = redact(child)
                    changed = changed or child_changed
            return result, changed
        if isinstance(node, list):
            changed = False
            result = []
            for child in node:
                redacted_child, child_changed = redact(child)
                result.append(redacted_child)
                changed = changed or child_changed
            return result, changed
        if isinstance(node, str):
            redacted_string = _redact_structured_sensitive_values(node)
            return redacted_string, redacted_string != node
        return node, False

    redacted, changed = redact(parsed)
    if changed and isinstance(parsed, dict) and all(_is_sensitive_structured_key(key) for key in parsed):
        return "[redacted-structured-data]"
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":")) if changed else value


def _redact_embedded_json(value: str) -> str:
    cursor = 0
    attempts = 0
    while cursor < len(value):
        candidates = [index for opening in "[{" if (index := value.find(opening, cursor)) >= 0]
        if not candidates:
            break
        start = min(candidates)
        attempts += 1
        if attempts > _MAX_EMBEDDED_JSON_CANDIDATES:
            return f"{value[:start]}[redacted-structured-data]"
        end = _find_balanced_value_end(value, start)
        if end is None:
            cursor = start + 1
            continue
        if end - start > _MAX_EMBEDDED_JSON_CHARS:
            value = f"{value[:start]}[redacted-structured-data]{value[end:]}"
            cursor = start + len("[redacted-structured-data]")
            continue
        candidate = value[start:end]
        redacted = _redact_complete_json(candidate)
        if redacted is None:
            cursor = start + 1
            continue
        if redacted != candidate:
            value = f"{value[:start]}{redacted}{value[end:]}"
            cursor = start + len(redacted)
        else:
            cursor = end
    return value


def _xml_token_end(value: str, start: int) -> int | None:
    quote: str | None = None
    for index in range(start + 1, len(value)):
        character = value[index]
        if quote:
            if character == quote:
                quote = None
        elif character in "'\"":
            quote = character
        elif character == ">":
            return index + 1
    return None


def _next_sensitive_xml_open(value: str, cursor: int) -> tuple[int, int, str] | None:
    while cursor < len(value):
        token_start = value.find("<", cursor)
        if token_start < 0:
            return None
        if value.startswith("<!--", token_start):
            end = value.find("-->", token_start + 4)
            if end < 0:
                return None
            cursor = end + 3
            continue
        if value.startswith("<![CDATA[", token_start):
            end = value.find("]]>", token_start + 9)
            if end < 0:
                return None
            cursor = end + 3
            continue
        if value.startswith("<?", token_start):
            end = value.find("?>", token_start + 2)
            if end < 0:
                return None
            cursor = end + 2
            continue
        token_end = _xml_token_end(value, token_start)
        if token_end is None:
            return None
        token = value[token_start:token_end]
        name_match = re.match(r"<\s*([A-Za-z_][\w.:-]*)", token)
        if name_match and _is_sensitive_structured_key(name_match.group(1).rsplit(":", 1)[-1]):
            return token_start, token_end, name_match.group(1)
        cursor = token_end
    return None


def _sensitive_xml_element_end(value: str, start: int, opening_end: int, tag: str) -> int:
    if re.search(r"/\s*>$", value[start:opening_end]):
        return opening_end
    depth = 1
    cursor = opening_end
    while cursor < len(value):
        token_start = value.find("<", cursor)
        if token_start < 0:
            return len(value)
        if value.startswith("<!--", token_start):
            end = value.find("-->", token_start + 4)
            cursor = len(value) if end < 0 else end + 3
            continue
        if value.startswith("<![CDATA[", token_start):
            end = value.find("]]>", token_start + 9)
            cursor = len(value) if end < 0 else end + 3
            continue
        if value.startswith("<?", token_start):
            end = value.find("?>", token_start + 2)
            cursor = len(value) if end < 0 else end + 2
            continue
        token_end = _xml_token_end(value, token_start)
        if token_end is None:
            return len(value)
        token = value[token_start:token_end]
        name_match = re.match(r"<\s*(/?)\s*([A-Za-z_][\w.:-]*)", token)
        if name_match and name_match.group(2) == tag:
            if name_match.group(1):
                depth -= 1
                if depth == 0:
                    return token_end
            elif not re.search(r"/\s*>$", token):
                depth += 1
        cursor = token_end
    return len(value)


def _redact_sensitive_xml(value: str) -> str:
    cursor = 0
    while opening := _next_sensitive_xml_open(value, cursor):
        start, opening_end, tag = opening
        end = _sensitive_xml_element_end(value, start, opening_end, tag)
        value = f"{value[:start]}[redacted-structured-data]{value[end:]}"
        cursor = start + len("[redacted-structured-data]")
    return value


def _redact_structured_sensitive_values(value: str) -> str:
    parsed_json = _redact_complete_json(value)
    if parsed_json is not None:
        return parsed_json
    value = _redact_embedded_json(value)
    value = _redact_sensitive_query_fragments(value)
    value = _redact_sensitive_xml(value)
    value = _redact_explicit_yaml_sensitive_values(value)
    root_container = value.strip().startswith(("{", "[")) and value.strip().endswith(("}", "]"))
    cursor = 0
    while match := _STRUCTURED_ASSIGNMENT_RE.search(value, cursor):
        if not _structured_assignment_is_sensitive(match):
            cursor = match.end()
            continue
        rhs_start = match.end()
        existing_redaction = next(
            (
                marker
                for marker in ('"[redacted]"', "'[redacted]'", "[redacted]")
                if value.startswith(marker, rhs_start)
            ),
            None,
        )
        if existing_redaction is not None:
            cursor = rhs_start + len(existing_redaction)
            continue
        line_start = value.rfind("\n", 0, match.start()) + 1
        newline = value.find("\n", rhs_start)
        line_end = len(value) if newline < 0 else newline
        rhs = value[rhs_start:line_end].rstrip("\r")
        stripped_rhs = rhs.lstrip()
        value_start = rhs_start + len(rhs) - len(stripped_rhs)
        prefix = value[line_start:match.start()]
        if re.search(r"\b(?:proxy-authorization|authorization|set-cookie|cookie)\s*[:=]", prefix, re.I):
            cursor = match.end()
            continue
        base_indent = len(prefix) - len(prefix.lstrip(" \t"))
        structural_line = re.fullmatch(r"[ \t]*(?:-[ \t]+)?", prefix) is not None
        plain_key = match.group("plain")
        operator = (match.group("operator") or match.group("word_operator") or "").lower()
        credential_prefix = re.match(r"(?:Bearer|Basic|Token)[ \t]+", stripped_rhs, re.I) is not None
        inline_assignment = operator != ":" or credential_prefix
        needs_composite_scalar_redaction = structural_line and (
            plain_key is None or _LEGACY_STRUCTURED_SCALAR_KEY_RE.fullmatch(plain_key) is None
        ) and not inline_assignment
        composite_key = plain_key is None or _LEGACY_STRUCTURED_SCALAR_KEY_RE.fullmatch(plain_key) is None
        end: int | None = None
        multiline = False

        semantic_rhs, property_offset, had_yaml_properties = _strip_yaml_node_properties(stripped_rhs)
        semantic_start = value_start + property_offset
        triple = "\"\"\"" if semantic_rhs.startswith("\"\"\"") else "'''" if semantic_rhs.startswith("'''") else None
        heredoc = re.fullmatch(r"<<(-?)([A-Za-z_][\w-]*)[ \t]*", semantic_rhs)
        if heredoc:
            allow_indent = bool(heredoc.group(1))
            delimiter = re.escape(heredoc.group(2))
            terminator = re.compile(
                rf"^{r'[ \t]*' if allow_indent else ''}{delimiter}[ \t]*\r?$",
                re.M,
            ).search(value, line_end + 1)
            end = len(value) if terminator is None else terminator.end()
            multiline = True
        elif triple:
            closing = value.find(triple, semantic_start + 3)
            end = len(value) if closing < 0 else closing + 3
            multiline = "\n" in value[semantic_start:end]
        elif semantic_rhs.startswith(("{", "[")):
            end = _find_balanced_value_end(value, semantic_start)
            multiline = end is None or "\n" in value[semantic_start:end]
            end = len(value) if end is None else end
        else:
            sequence_end = _indentationless_sequence_end(value, line_end, base_indent) if not semantic_rhs else line_end
            continuation_end = max(_indented_continuation_end(value, line_end, base_indent), sequence_end)
            has_continuation = continuation_end > line_end and structural_line
            yaml_marker = re.fullmatch(r"(?:[|>][+-]?\d?|[|>]\d?[+-]?)", semantic_rhs)
            if yaml_marker or has_continuation or not semantic_rhs:
                end = continuation_end if has_continuation else line_end
                multiline = has_continuation
            elif had_yaml_properties:
                end = line_end
            elif needs_composite_scalar_redaction:
                end = line_end

        if end is None:
            if composite_key and (not structural_line or inline_assignment):
                inline_end: int | None = None
                if stripped_rhs.startswith('"'):
                    quoted = re.match(r'"(?:\\.|[^"\\])*"', stripped_rhs)
                    inline_end = value_start + quoted.end() if quoted else line_end
                elif stripped_rhs.startswith("'"):
                    quoted = re.match(r"'(?:''|[^'])*'", stripped_rhs)
                    inline_end = value_start + quoted.end() if quoted else line_end
                else:
                    token = re.match(
                        r"(?:Bearer|Basic|Token)[ \t]+[^\s,;&]+|[^\s,;&]+",
                        stripped_rhs,
                        re.I,
                    )
                    inline_end = value_start + token.end() if token else None
                    while inline_end and inline_end > value_start and value[inline_end - 1] in ".!?":
                        inline_end -= 1
                if inline_end and inline_end > value_start:
                    value = f"{value[:value_start]}[redacted]{value[inline_end:]}"
                    cursor = value_start + len("[redacted]")
                    continue
            cursor = match.end()
            continue
        if root_container and multiline:
            return "[redacted-structured-data]"
        value = f"{value[:match.start()]}[redacted-structured-data]{value[end:]}"
        cursor = match.start() + len("[redacted-structured-data]")
    return value


def redact_public_text(value: str) -> str:
    redacted = _redact_structured_sensitive_values(value)
    redacted = re.sub(r"^[ \t]{0,3}\[[^\]\r\n]+\]:[^\r\n]*(?:\r?\n|$)", "", redacted, flags=re.M)
    redacted = re.sub(r"!\[([^\]]*)\]\s*\[[^\]]*\]", r"\1 [image removed]", redacted)
    redacted = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1 [image removed]", redacted)
    redacted = re.sub(r"!\[([^\]]+)\](?!\s*[\[(])", r"\1 [image removed]", redacted)
    redacted = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", redacted)
    redacted = _PUBLIC_URL_RE.sub(_sanitize_public_url, redacted)
    redacted = _PUBLIC_EMAIL_RE.sub("[redacted-email]", redacted)
    redacted = _QUOTED_SENSITIVE_VALUE_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}{m.group(1)}:"[redacted]"', redacted)
    redacted = _UNQUOTED_KEY_QUOTED_VALUE_RE.sub(lambda m: f'{m.group(1)}=[redacted]', redacted)
    redacted = _XML_SENSITIVE_VALUE_RE.sub(lambda m: f"<{m.group(1)}>[redacted]</{m.group(1)}>", redacted)
    redacted = _QUOTED_SENSITIVE_HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}=[redacted]{m.group(1)}", redacted)
    redacted = _UNCLOSED_SENSITIVE_HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}=[redacted]", redacted)
    redacted = _SENSITIVE_HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}=[redacted]", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(lambda m: m.group(0) if m.group(2).startswith("[redacted]") else f"{m.group(1)}=[redacted]", redacted)
    redacted = _BEARER_CREDENTIAL_RE.sub("bearer=[redacted]", redacted)
    redacted = _BASIC_CREDENTIAL_RE.sub(_redact_standalone_basic, redacted)
    redacted = _IPV6_CANDIDATE_RE.sub(_redact_private_ipv6_candidate, redacted)
    redacted = _PRIVATE_IPV4_RE.sub("[redacted-private-address]", redacted)
    redacted = _JWT_RE.sub("[redacted-secret]", redacted)
    redacted = _LONG_SECRET_RE.sub("[redacted-secret]", redacted)
    redacted = redacted.replace("<", "&lt;").replace(">", "&gt;")
    redacted = re.sub(r"@(?=[A-Za-z0-9_-])", "@\u200b", redacted)
    redacted = re.sub(r"#(?=\d)", "#\u200b", redacted)
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
            normalized_names: set[str] = set()
            for info in infos:
                name = info.filename.replace("\\", "/")
                if info.flag_bits & 0x1:
                    raise ReportAttachmentInvalid("Encrypted debug bundle ZIPs are not supported.")
                if name.startswith("/") or "../" in f"/{name}" or ":" in name:
                    raise ReportAttachmentInvalid("Debug bundle ZIP contains unsafe paths.")
                normalized = "/".join(part for part in name.split("/") if part not in {"", "."}).casefold()
                if normalized in normalized_names:
                    raise ReportAttachmentInvalid("Debug bundle ZIP contains duplicate paths.")
                normalized_names.add(normalized)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if (unix_mode & 0o170000) == 0o120000:
                    raise ReportAttachmentInvalid("Debug bundle ZIP contains unsupported links.")
                if info.file_size and info.compress_size == 0:
                    raise ReportAttachmentInvalid("Debug bundle ZIP compression metadata is invalid.")
                if info.compress_size and info.file_size / info.compress_size > DEBUG_BUNDLE_MAX_COMPRESSION_RATIO:
                    raise ReportAttachmentInvalid("Debug bundle ZIP compression ratio is unsafe.")
                offset = info.header_offset
                if offset < 0 or offset + 30 > len(content) or content[offset:offset + 4] != b"PK\x03\x04":
                    raise ReportAttachmentInvalid("Debug bundle ZIP headers are inconsistent.")
                local_name_length = int.from_bytes(content[offset + 26:offset + 28], "little")
                local_extra_length = int.from_bytes(content[offset + 28:offset + 30], "little")
                local_flags = int.from_bytes(content[offset + 6:offset + 8], "little")
                local_method = int.from_bytes(content[offset + 8:offset + 10], "little")
                if local_flags != info.flag_bits or local_method != info.compress_type:
                    raise ReportAttachmentInvalid("Debug bundle ZIP headers are inconsistent.")
                if not local_flags & 0x8:
                    local_crc = int.from_bytes(content[offset + 14:offset + 18], "little")
                    local_compressed = int.from_bytes(content[offset + 18:offset + 22], "little")
                    local_uncompressed = int.from_bytes(content[offset + 22:offset + 26], "little")
                    if (local_crc, local_compressed, local_uncompressed) != (
                        info.CRC,
                        info.compress_size,
                        info.file_size,
                    ):
                        raise ReportAttachmentInvalid("Debug bundle ZIP headers are inconsistent.")
                local_name_start = offset + 30
                local_name_end = local_name_start + local_name_length
                if local_name_end + local_extra_length > len(content):
                    raise ReportAttachmentInvalid("Debug bundle ZIP headers are inconsistent.")
                local_name = content[local_name_start:local_name_end].decode(
                    "utf-8" if info.flag_bits & 0x800 else "cp437"
                )
                if local_name != info.orig_filename:
                    raise ReportAttachmentInvalid("Debug bundle ZIP headers are inconsistent.")
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
            if bundle.testzip() is not None:
                raise ReportAttachmentInvalid("Debug bundle ZIP content is corrupt.")
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


def _image_dimensions(content: bytes, image_type: str) -> tuple[int, int]:
    try:
        if image_type == "image/png":
            if len(content) < 24 or content[12:16] != b"IHDR":
                raise ValueError
            return struct.unpack(">II", content[16:24])
        if image_type == "image/webp":
            chunk = content[12:16]
            if chunk == b"VP8X" and len(content) >= 30:
                return (1 + int.from_bytes(content[24:27], "little"), 1 + int.from_bytes(content[27:30], "little"))
            if chunk == b"VP8 " and len(content) >= 30 and content[23:26] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", content[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if chunk == b"VP8L" and len(content) >= 25 and content[20] == 0x2F:
                bits = int.from_bytes(content[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            raise ValueError
        if image_type == "image/jpeg":
            offset = 2
            while offset + 4 <= len(content):
                if content[offset] != 0xFF:
                    raise ValueError
                marker = content[offset + 1]
                offset += 2
                if marker in {0xD8, 0xD9}:
                    continue
                length = int.from_bytes(content[offset:offset + 2], "big")
                if length < 2 or offset + length > len(content):
                    raise ValueError
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    if length < 7:
                        raise ValueError
                    return int.from_bytes(content[offset + 5:offset + 7], "big"), int.from_bytes(content[offset + 3:offset + 5], "big")
                offset += length
    except (IndexError, struct.error, ValueError):
        pass
    raise ReportAttachmentInvalid("Screenshot dimensions could not be validated.")


def _validate_image_dimensions(content: bytes, image_type: str) -> None:
    width, height = _image_dimensions(content, image_type)
    pixels = width * height
    if width <= 0 or height <= 0:
        raise ReportAttachmentInvalid("Screenshot dimensions are invalid.")
    if width > SCREENSHOT_MAX_DIMENSION or height > SCREENSHOT_MAX_DIMENSION:
        raise ReportAttachmentInvalid("Screenshot dimensions exceed the allowed limit.")
    if pixels > SCREENSHOT_MAX_PIXELS or pixels * 4 > SCREENSHOT_MAX_DECODED_BYTES:
        raise ReportAttachmentInvalid("Screenshot decoded size exceeds the allowed limit.")


def _validate_image_structure(content: bytes, image_type: str) -> None:
    if image_type == "image/png":
        offset = 8
        saw_ihdr = False
        saw_iend = False
        while offset + 12 <= len(content):
            length = int.from_bytes(content[offset:offset + 4], "big")
            chunk_type = content[offset + 4:offset + 8]
            end = offset + 12 + length
            if end > len(content):
                raise ReportAttachmentInvalid("Screenshot image is truncated.")
            chunk_data = content[offset + 8:offset + 8 + length]
            expected_crc = int.from_bytes(content[offset + 8 + length:end], "big")
            if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
                raise ReportAttachmentInvalid("Screenshot image checksum is invalid.")
            if not saw_ihdr and (chunk_type != b"IHDR" or length != 13):
                raise ReportAttachmentInvalid("Screenshot PNG header is invalid.")
            saw_ihdr = saw_ihdr or chunk_type == b"IHDR"
            if chunk_type == b"IEND":
                if length != 0 or end != len(content):
                    raise ReportAttachmentInvalid("Screenshot PNG ending is invalid.")
                saw_iend = True
                break
            offset = end
        if not saw_ihdr or not saw_iend:
            raise ReportAttachmentInvalid("Screenshot image is incomplete.")
    elif image_type == "image/jpeg":
        if len(content) < 4 or content[-2:] != b"\xff\xd9":
            raise ReportAttachmentInvalid("Screenshot JPEG is incomplete.")
    elif image_type == "image/webp":
        if len(content) < 20 or int.from_bytes(content[4:8], "little") + 8 != len(content):
            raise ReportAttachmentInvalid("Screenshot WebP container is incomplete.")


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
        detected = "image/png"
        _validate_image_dimensions(content, detected)
        _validate_image_structure(content, detected)
        return detected
    if suffix in {"jpg", "jpeg"} and content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
        _validate_image_dimensions(content, detected)
        _validate_image_structure(content, detected)
        return detected
    if (
        suffix == "webp"
        and len(content) >= 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        detected = "image/webp"
        _validate_image_dimensions(content, detected)
        _validate_image_structure(content, detected)
        return detected
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

    kind: Literal["problem", "feature"] = "problem"
    area: Literal[
        "dashboard",
        "activity",
        "notifications",
        "dvr_monitoring",
        "updates",
        "backup_restore",
        "authentication_security",
        "other",
    ] | None = None
    summary: str = Field(min_length=1, max_length=500)
    expected: str | None = Field(default=None, max_length=2000)
    use_case: str | None = Field(default=None, max_length=2000)
    getchannels_username: str | None = None
    github_username: str | None = None
    email: str | None = None
    diagnostics: ReportDiagnostics = Field(default_factory=ReportDiagnostics)
    turnstile_token: str | None = Field(default=None, max_length=2048)

    @field_validator("summary", mode="before")
    @classmethod
    def clean_summary(cls, value: Any) -> str:
        return _clean_single_line(value)

    @field_validator("expected", "use_case", mode="before")
    @classmethod
    def clean_multiline_field(cls, value: Any) -> str | None:
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
    def public_value(value: Any, fallback: str) -> str:
        cleaned = redact_public_text(_clean_single_line(value) or fallback)
        cleaned = _PUBLIC_URL_RE.sub("[redacted-url]", cleaned)
        return _markdown_table_value(cleaned)

    return "\n".join(
        [
            "| Field | Value |",
            "| --- | --- |",
            f"| ChannelWatch version | {public_value(diagnostics.channelwatch_version, 'Unknown')} |",
            f"| DVRs configured | {diagnostics.dvr_count} |",
            f"| DVRs connected | {diagnostics.connected_dvr_count} |",
            f"| Core status | {public_value(diagnostics.core_status, 'Unknown')} |",
            f"| Monitoring | {public_value(monitoring, 'Not reported')} |",
            f"| Notification providers | {public_value(providers, 'None reported')} |",
            f"| Enabled feature toggles | {public_value(', '.join(enabled_toggles), 'None reported')} |",
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
    is_feature = payload.kind == "feature"
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
    diagnostics_section = (
        ""
        if is_feature
        else (
            '<h2 style="color:#f8fbff;font-size:15px;margin:22px 0 8px;">Diagnostics</h2>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
            + "".join(diagnostics_rows)
            + "</table>"
        )
    )
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
                      <div style="color:#f8fbff;font-size:20px;font-weight:700;line-height:1.25;">{html_escape("New ChannelWatch feature request" if is_feature else "New ChannelWatch report")}</div>
                      <div style="color:#9aa9bc;font-size:13px;line-height:1.5;">{html_escape(mode)} &middot; {html_escape(submitted_at)}</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:28px;">
                <h1 style="color:#f8fbff;font-size:20px;line-height:1.3;margin:0 0 8px;">{html_escape(payload.summary)}</h1>
                <p style="color:#9aa9bc;font-size:14px;line-height:1.6;margin:0 0 18px;">{html_escape(payload.expected or ("No requested change was provided." if is_feature else "No expected behavior was provided."))}</p>
                <h2 style="color:#f8fbff;font-size:15px;margin:0 0 10px;">Next steps</h2>
                <div style="margin:0 0 14px;">{action_buttons or '<span style="color:#9aa9bc;font-size:13px;">No contact or issue links are available yet.</span>'}</div>
                <hr style="border:0;border-top:1px solid #22314f;margin:22px 0;" />
                <h2 style="color:#f8fbff;font-size:15px;margin:0 0 8px;">Reporter contact</h2>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">{''.join(contact_rows)}</table>
                {diagnostics_section}
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
    prefix = "Feature" if payload.kind == "feature" else "Bug"
    return f"[{prefix}] {summary}"


def render_issue_body(payload: ReportProblemPayload) -> str:
    summary = redact_public_text(payload.summary)
    expected = redact_public_text(payload.expected or "Not provided.")
    if payload.kind == "feature":
        area = (payload.area or "other").replace("_", " ").title()
        use_case = redact_public_text(payload.use_case or "Not provided.")
        return "\n\n".join(
            [
                "# ChannelWatch Feature Request",
                "## What should change?\n\n" + expected,
                "## Why would it help?\n\n" + use_case,
                "## Product area\n\n" + area,
                "## Short title\n\n" + summary,
                "## Reporter\n\n" + _format_public_contact(payload),
            ]
        )
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
    report_payload = payload.model_dump(exclude_none=True)
    if payload.kind == "feature":
        report_payload.pop("diagnostics", None)
    envelope = {
        "schema": 1,
        "source": "channelwatch",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "report": report_payload,
    }
    raw = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"CW-REPORT-v1-{encoded}"


def parse_schema2_support_code(support_code: str) -> tuple[ReportProblemPayload, dict[str, Any]]:
    prefix = "CW-REPORT-v2-"
    if not support_code.startswith(prefix):
        raise ReportPayloadInvalid("Offline packages require a finalized schema-2 support code.")
    encoded = support_code[len(prefix):]
    try:
        padded = encoded + ("=" * ((4 - len(encoded) % 4) % 4))
        envelope = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8"))
        if not isinstance(envelope, dict) or envelope.get("schema") != 2:
            raise ValueError("invalid schema")
        report_id = envelope.get("report_id")
        if not isinstance(report_id, str) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            report_id,
            re.I,
        ):
            raise ValueError("invalid report id")
        created_at_raw = envelope.get("created_at")
        if not isinstance(created_at_raw, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)",
            created_at_raw,
        ):
            raise ValueError("invalid UTC timestamp")
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = now - created_at
        if age.total_seconds() > 30 * 24 * 60 * 60 or age.total_seconds() < -5 * 60:
            raise ValueError("expired or future support code")
        client = envelope.get("client")
        if not isinstance(client, dict) or set(client) != {
            "channelwatch_version",
            "submission_source",
        }:
            raise ValueError("invalid client metadata")
        version = client.get("channelwatch_version")
        if not isinstance(version, str) or not re.fullmatch(
            r"(?:\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?|unknown)", version
        ):
            raise ValueError("invalid client version")
        if client.get("submission_source") != "in-app":
            raise ValueError("invalid submission source")
        report = ReportProblemPayload.model_validate(envelope.get("report"))
        expected_version = report.diagnostics.channelwatch_version or "unknown"
        if version != expected_version:
            raise ValueError("client version does not match report diagnostics")
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ReportPayloadInvalid("Support code could not be validated.") from exc
    return report, envelope


def require_support_code_matches_payload(
    support_code: str, payload: ReportProblemPayload
) -> None:
    decoded_payload, _envelope = parse_schema2_support_code(support_code)
    if decoded_payload.model_dump(mode="json") != payload.model_dump(mode="json"):
        raise ReportPayloadInvalid("Support code does not match the finalized report.")


def build_offline_report_package(
    payload: ReportProblemPayload,
    *,
    support_code: str,
    attachments: list[tuple[ReportAttachmentSummary, bytes]] | None = None,
    portal_url: str = DEFAULT_REPORT_PORTAL_URL,
) -> bytes:
    attachments = attachments or []
    created_at = datetime.now(timezone.utc).isoformat()
    summaries = [summary for summary, _content in attachments]
    require_support_code_matches_payload(support_code, payload)
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
        "attachments": attachment_entries,
    }
    if payload.kind != "feature":
        manifest["diagnostics_file"] = "diagnostics-summary.json"
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
        if payload.kind != "feature":
            package.writestr(
                "diagnostics-summary.json",
                json.dumps(payload.diagnostics.model_dump(), indent=2),
            )
        package.writestr("manifest.json", json.dumps(manifest, indent=2))
        for entry, (_summary, content) in zip(attachment_entries, attachments):
            package.writestr(entry["path"], content)
    return buffer.getvalue()
