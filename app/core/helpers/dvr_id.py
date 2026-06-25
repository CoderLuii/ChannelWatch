"""Canonical DVR id utility.

Contract: "dvr_" + md5(normalized_host + ":" + str(port))[:8]

IPv6 normalization: strip surrounding brackets, lowercase.
IPv4 and hostname inputs are hashed as-is (case-preserving).
"""

import hashlib


def _normalize_host(host: str) -> str:
    stripped = host.strip("[]")
    if ":" in stripped:
        return stripped.lower()
    return stripped


def canonical_dvr_id(host: str, port: int) -> str:
    """Return "dvr_" + MD5(normalized_host:port)[:8].

    Bracketed and unbracketed IPv6 forms produce identical output.
    IPv6 is case-insensitive; hostnames are not normalized.
    """
    normalized = _normalize_host(host)
    digest = hashlib.md5(f"{normalized}:{port}".encode("utf-8")).hexdigest()
    return "dvr_" + digest[:8]


def dvr_display_name(name: object, host: object, fallback: str = "Channels DVR") -> str:
    """Return a stable display name, falling back to host when the name is blank."""
    display = str(name or "").strip()
    if display:
        return display
    host_text = str(host or "").strip()
    return host_text or fallback


if __name__ == "__main__":
    import json
    import sys

    if "--gen-test-data" not in sys.argv:
        print("Usage: python -m core.helpers.dvr_id --gen-test-data", file=sys.stderr)
        sys.exit(1)

    vectors = [
        {"host": "192.168.1.1", "port": 8089},
        {"host": "192.168.1.2", "port": 8089},
        {"host": "10.0.0.1", "port": 8089},
        {"host": "192.168.1.1", "port": 9090},
        {"host": "dvr.local", "port": 8089},
        {"host": "dvr.example.com", "port": 8089},
        {"host": "localhost", "port": 8089},
        {"host": "127.0.0.1", "port": 8089},
        {"host": "::1", "port": 8089},
        {"host": "[::1]", "port": 8089},
        {"host": "2001:db8::1", "port": 8089},
        {"host": "[2001:db8::1]", "port": 8089},
        {"host": "2001:DB8::1", "port": 8089},
        {"host": "[2001:DB8::1]", "port": 8089},
        {"host": "fe80::1%eth0", "port": 8089},
        {"host": "[fe80::1%eth0]", "port": 8089},
        {"host": "2001:db8:85a3::8a2e:370:7334", "port": 8089},
        {"host": "[2001:db8:85a3::8a2e:370:7334]", "port": 8089},
        {"host": "CHANNELSDVR.LOCAL", "port": 8089},
        {"host": "192.168.100.200", "port": 8089},
        {"host": "dvr.local", "port": 57000},
        {"host": "::1", "port": 1234},
        {"host": "0.0.0.0", "port": 8089},
    ]
    result = [
        {"input": v, "expected_id": canonical_dvr_id(v["host"], v["port"])}
        for v in vectors
    ]
    print(json.dumps(result, indent=2))
