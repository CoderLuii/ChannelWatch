import json
from pathlib import Path

import pytest

from core.helpers.dvr_id import canonical_dvr_id, _normalize_host

VECTORS_FILE = (
    Path(__file__).parent.parent.parent / "ui" / "__tests__" / "dvr-id-vectors.json"
)


class TestNormalizeHost:
    def test_strips_brackets_from_ipv6(self):
        assert _normalize_host("[::1]") == "::1"

    def test_lowercases_ipv6(self):
        assert _normalize_host("2001:DB8::1") == "2001:db8::1"

    def test_strips_and_lowercases_bracketed_ipv6(self):
        assert _normalize_host("[2001:DB8::1]") == "2001:db8::1"

    def test_ipv4_unchanged(self):
        assert _normalize_host("192.168.1.1") == "192.168.1.1"

    def test_hostname_unchanged(self):
        assert _normalize_host("dvr.local") == "dvr.local"

    def test_uppercase_hostname_unchanged(self):
        assert _normalize_host("CHANNELSDVR.LOCAL") == "CHANNELSDVR.LOCAL"

    def test_ipv6_zone_id_lowercased(self):
        assert _normalize_host("fe80::1%ETH0") == "fe80::1%eth0"

    def test_brackets_stripped_zone_id_lowercased(self):
        assert _normalize_host("[fe80::1%ETH0]") == "fe80::1%eth0"


class TestCanonicalDvrId:
    @pytest.mark.parametrize(
        "host,port,expected",
        [
            ("192.168.1.1", 8089, "dvr_aef6d698"),
            ("192.168.1.2", 8089, "dvr_56a5e213"),
            ("10.0.0.1", 8089, "dvr_e6785710"),
            ("dvr.local", 8089, "dvr_e1b92638"),
            ("dvr.example.com", 8089, "dvr_89cc9b63"),
            ("localhost", 8089, "dvr_e6f9a23f"),
            ("127.0.0.1", 8089, "dvr_db3313ef"),
            ("0.0.0.0", 8089, "dvr_c7d9568a"),
            ("192.168.100.200", 8089, "dvr_493f41ca"),
        ],
    )
    def test_ipv4_and_hostname(self, host, port, expected):
        assert canonical_dvr_id(host, port) == expected

    @pytest.mark.parametrize(
        "host,port,expected",
        [
            ("::1", 8089, "dvr_6244d64f"),
            ("[::1]", 8089, "dvr_6244d64f"),
            ("2001:db8::1", 8089, "dvr_2e679e72"),
            ("[2001:db8::1]", 8089, "dvr_2e679e72"),
            ("2001:DB8::1", 8089, "dvr_2e679e72"),
            ("[2001:DB8::1]", 8089, "dvr_2e679e72"),
            ("fe80::1%eth0", 8089, "dvr_f356cadc"),
            ("[fe80::1%eth0]", 8089, "dvr_f356cadc"),
            ("2001:db8:85a3::8a2e:370:7334", 8089, "dvr_e5a54470"),
            ("[2001:db8:85a3::8a2e:370:7334]", 8089, "dvr_e5a54470"),
        ],
    )
    def test_ipv6_normalization(self, host, port, expected):
        assert canonical_dvr_id(host, port) == expected

    def test_port_variation_produces_different_id(self):
        id_8089 = canonical_dvr_id("192.168.1.1", 8089)
        id_9090 = canonical_dvr_id("192.168.1.1", 9090)
        assert id_8089 != id_9090
        assert id_8089 == "dvr_aef6d698"
        assert id_9090 == "dvr_b3033482"

    def test_different_hosts_same_port_differ(self):
        assert canonical_dvr_id("192.168.1.1", 8089) != canonical_dvr_id(
            "192.168.1.2", 8089
        )

    def test_output_starts_with_prefix(self):
        result = canonical_dvr_id("192.168.1.1", 8089)
        assert result.startswith("dvr_")

    def test_output_length(self):
        result = canonical_dvr_id("192.168.1.1", 8089)
        assert len(result) == 12

    def test_deterministic(self):
        assert canonical_dvr_id("dvr.local", 8089) == canonical_dvr_id(
            "dvr.local", 8089
        )

    def test_uppercase_hostname_not_normalized(self):
        upper = canonical_dvr_id("CHANNELSDVR.LOCAL", 8089)
        lower = canonical_dvr_id("channelsdvr.local", 8089)
        assert upper != lower
        assert upper == "dvr_6ed91433"

    def test_ipv6_loopback_bracket_equivalence(self):
        assert canonical_dvr_id("::1", 8089) == canonical_dvr_id("[::1]", 8089)

    def test_ipv6_case_equivalence(self):
        assert canonical_dvr_id("2001:DB8::1", 8089) == canonical_dvr_id(
            "2001:db8::1", 8089
        )

    def test_ipv6_bracket_and_case_equivalence(self):
        assert canonical_dvr_id("[2001:DB8::1]", 8089) == canonical_dvr_id(
            "2001:db8::1", 8089
        )

    def test_ipv6_zone_bracket_equivalence(self):
        assert canonical_dvr_id("fe80::1%eth0", 8089) == canonical_dvr_id(
            "[fe80::1%eth0]", 8089
        )

    def test_non_standard_port(self):
        assert canonical_dvr_id("dvr.local", 57000) == "dvr_836678c7"

    def test_ipv6_non_standard_port(self):
        assert canonical_dvr_id("::1", 1234) == "dvr_4b60e4e7"


class TestCrossLanguageVectors:
    def test_vectors_file_exists(self):
        assert VECTORS_FILE.exists(), f"Fixture not found: {VECTORS_FILE}"

    def test_all_vectors_match(self):
        vectors = json.loads(VECTORS_FILE.read_text())
        assert len(vectors) >= 20, f"Expected 20+ vectors, got {len(vectors)}"
        for entry in vectors:
            host = entry["input"]["host"]
            port = entry["input"]["port"]
            expected = entry["expected_id"]
            result = canonical_dvr_id(host, port)
            assert result == expected, (
                f"canonical_dvr_id({host!r}, {port}) = {result!r}, expected {expected!r}"
            )
