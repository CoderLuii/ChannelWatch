"""DVR client utilities — version constants and compatibility helpers.

Channels DVR versions use a date-based format: YYYY.MM.DD or YYYY.MM.DD.HHMM.
Only the YYYY.MM.DD prefix is used for compatibility comparisons.
"""

MIN_TESTED_DVR_VERSION = "2024.01.01"
MAX_TESTED_DVR_VERSION = "2026.04.20"


def parse_dvr_version(version_str: str):
    """Parse a Channels DVR version string into a comparable (year, month, day) tuple.

    Handles YYYY.MM.DD and YYYY.MM.DD.HHMM formats.  Returns None if the
    string cannot be parsed into a valid date triple.
    """
    if not version_str or not isinstance(version_str, str):
        return None
    parts = version_str.strip().split(".")
    if len(parts) < 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


def check_version_compatibility(version_str: str) -> dict:
    """Return a compatibility assessment for a DVR version string.

    Keys in the returned dict:
      version     – the raw version string passed in
      parsed      – (year, month, day) tuple, or None if unparseable
      compatible  – True if within tested range, False if outside, None if unknown
      warning     – human-readable warning message, or None when compatible
    """
    parsed = parse_dvr_version(version_str)

    if parsed is None:
        return {
            "version": version_str,
            "parsed": None,
            "compatible": None,
            "warning": (
                f"DVR version '{version_str}' could not be parsed; "
                "compatibility is unknown."
            ),
        }

    min_parsed = parse_dvr_version(MIN_TESTED_DVR_VERSION)
    max_parsed = parse_dvr_version(MAX_TESTED_DVR_VERSION)

    if parsed < min_parsed:
        return {
            "version": version_str,
            "parsed": parsed,
            "compatible": False,
            "warning": (
                f"DVR version {version_str} is below the tested range "
                f"({MIN_TESTED_DVR_VERSION} – {MAX_TESTED_DVR_VERSION}). "
                "Some ChannelWatch features may not work correctly."
            ),
        }

    if parsed > max_parsed:
        return {
            "version": version_str,
            "parsed": parsed,
            "compatible": False,
            "warning": (
                f"DVR version {version_str} is above the tested range "
                f"({MIN_TESTED_DVR_VERSION} – {MAX_TESTED_DVR_VERSION}). "
                "ChannelWatch has not been tested with this version."
            ),
        }

    return {
        "version": version_str,
        "parsed": parsed,
        "compatible": True,
        "warning": None,
    }
