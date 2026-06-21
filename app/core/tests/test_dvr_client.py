from core.dvr_client import (
    MIN_TESTED_DVR_VERSION,
    MAX_TESTED_DVR_VERSION,
    parse_dvr_version,
    check_version_compatibility,
)


class TestConstants:
    def test_min_version_is_parseable(self):
        assert parse_dvr_version(MIN_TESTED_DVR_VERSION) is not None

    def test_max_version_is_parseable(self):
        assert parse_dvr_version(MAX_TESTED_DVR_VERSION) is not None

    def test_min_before_max(self):
        assert parse_dvr_version(MIN_TESTED_DVR_VERSION) < parse_dvr_version(
            MAX_TESTED_DVR_VERSION
        )


class TestParseDvrVersion:
    def test_standard_date_format(self):
        assert parse_dvr_version("2025.04.15") == (2025, 4, 15)

    def test_full_timestamp_format(self):
        assert parse_dvr_version("2025.04.15.2308") == (2025, 4, 15)

    def test_leading_zeros_ignored(self):
        assert parse_dvr_version("2024.01.01") == (2024, 1, 1)

    def test_returns_none_for_empty_string(self):
        assert parse_dvr_version("") is None

    def test_returns_none_for_none(self):
        assert parse_dvr_version(None) is None

    def test_returns_none_for_non_string(self):
        assert parse_dvr_version(20250415) is None

    def test_returns_none_for_too_few_parts(self):
        assert parse_dvr_version("2025.04") is None

    def test_returns_none_for_non_numeric_parts(self):
        assert parse_dvr_version("2025.Apr.15") is None

    def test_returns_none_for_empty_parts(self):
        assert parse_dvr_version("..") is None

    def test_tuple_is_comparable(self):
        older = parse_dvr_version("2024.03.01")
        newer = parse_dvr_version("2025.01.01")
        assert older < newer

    def test_same_year_month_comparison(self):
        assert parse_dvr_version("2025.04.01") < parse_dvr_version("2025.04.15")


class TestCheckVersionCompatibility:
    def test_version_within_range_is_compatible(self):
        result = check_version_compatibility("2025.06.01")
        assert result["compatible"] is True
        assert result["warning"] is None
        assert result["version"] == "2025.06.01"
        assert result["parsed"] == (2025, 6, 1)

    def test_min_boundary_is_compatible(self):
        result = check_version_compatibility(MIN_TESTED_DVR_VERSION)
        assert result["compatible"] is True
        assert result["warning"] is None

    def test_max_boundary_is_compatible(self):
        result = check_version_compatibility(MAX_TESTED_DVR_VERSION)
        assert result["compatible"] is True
        assert result["warning"] is None

    def test_version_below_range_is_incompatible(self):
        result = check_version_compatibility("2023.12.31")
        assert result["compatible"] is False
        assert result["warning"] is not None
        assert "below" in result["warning"]
        assert MIN_TESTED_DVR_VERSION in result["warning"]

    def test_version_above_range_is_incompatible(self):
        result = check_version_compatibility("2028.01.01")
        assert result["compatible"] is False
        assert result["warning"] is not None
        assert "above" in result["warning"]
        assert MAX_TESTED_DVR_VERSION in result["warning"]

    def test_full_timestamp_format_within_range(self):
        result = check_version_compatibility("2025.09.22.1430")
        assert result["compatible"] is True
        assert result["warning"] is None

    def test_full_timestamp_at_max_boundary_is_compatible(self):
        result = check_version_compatibility("2026.04.20.0213")
        assert result["compatible"] is True
        assert result["warning"] is None

    def test_unparseable_version_returns_unknown(self):
        result = check_version_compatibility("unknown")
        assert result["compatible"] is None
        assert result["warning"] is not None
        assert (
            "unknown" in result["warning"].lower()
            or "could not be parsed" in result["warning"]
        )

    def test_none_version_returns_unknown(self):
        result = check_version_compatibility(None)
        assert result["compatible"] is None
        assert result["warning"] is not None

    def test_empty_string_returns_unknown(self):
        result = check_version_compatibility("")
        assert result["compatible"] is None

    def test_incompatible_below_does_not_crash(self):
        result = check_version_compatibility("2020.01.01")
        assert isinstance(result, dict)
        assert result["compatible"] is False

    def test_incompatible_above_does_not_crash(self):
        result = check_version_compatibility("2099.12.31")
        assert isinstance(result, dict)
        assert result["compatible"] is False

    def test_warning_contains_both_boundaries(self):
        result = check_version_compatibility("2020.01.01")
        assert MIN_TESTED_DVR_VERSION in result["warning"]
        assert MAX_TESTED_DVR_VERSION in result["warning"]


class TestStartupWarningBehavior:
    def test_out_of_range_does_not_raise(self):
        for version in ("2020.01.01", "2099.12.31", "garbage", "", None):
            result = check_version_compatibility(version)
            assert isinstance(result, dict), (
                f"check_version_compatibility({version!r}) should return dict, not raise"
            )

    def test_compatible_version_has_no_warning(self):
        result = check_version_compatibility("2026.01.15")
        assert result["warning"] is None

    def test_incompatible_version_has_warning(self):
        result = check_version_compatibility("2019.06.01")
        assert result["warning"] is not None
        assert len(result["warning"]) > 0
