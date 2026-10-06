#!/usr/bin/env python3
"""Validate the release-specific OpenVEX runtime disposition."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


EXPECTED_CVES = {
    "CVE-2025-15367",
    "CVE-2026-15806",
    "CVE-2026-17084",
    "CVE-2026-19672",
}
EXPECTED_ARCHES = {"aarch64", "x86_64"}
EXPECTED_PYTHON_PACKAGE_VERSION = "3.14.7-r1"
PRODUCT_PATTERN = re.compile(
    r"^pkg:apk/wolfi/python-3\.14@(?P<version>[^?]+)\?"
    r"arch=(?P<arch>aarch64|x86_64)&distro=wolfi-20230201$"
)
FUTURE_VEX_BASELINE = (1, 2, 0)


class VexValidationError(ValueError):
    """Raised when the release VEX contract is incomplete or inconsistent."""


def _release_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise VexValidationError("Expected release version must use X.Y.Z")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _validate_reviewed_future_statements(statements: list[Any]) -> None:
    vulnerability_names: set[str] = set()
    for statement in statements:
        if not isinstance(statement, dict):
            raise VexValidationError("OpenVEX statements must be objects")
        vulnerability = statement.get("vulnerability")
        if not isinstance(vulnerability, dict):
            raise VexValidationError("OpenVEX vulnerability must be an object")
        name = str(vulnerability.get("name") or "").strip()
        identifier = str(vulnerability.get("@id") or "").strip()
        if not name or not identifier:
            raise VexValidationError("OpenVEX vulnerability identity is required")
        if name in vulnerability_names:
            raise VexValidationError(f"{name} must be dispositioned at most once")
        vulnerability_names.add(name)

        if statement.get("status") != "affected":
            raise VexValidationError(
                "stale VEX statements using not_affected are forbidden"
            )
        if not str(statement.get("action_statement") or "").strip():
            raise VexValidationError(
                f"{name} affected status requires an action statement"
            )

        products = statement.get("products")
        if not isinstance(products, list) or not products:
            raise VexValidationError(f"{name} must identify at least one affected product")
        product_ids = [
            str(product.get("@id") or "").strip()
            if isinstance(product, dict)
            else ""
            for product in products
        ]
        if any(not product_id for product_id in product_ids):
            raise VexValidationError(f"{name} has an invalid product identifier")
        if len(set(product_ids)) != len(product_ids):
            raise VexValidationError(f"{name} has duplicate product identifiers")


def validate_vex(document: dict[str, Any], *, expected_version: str) -> None:
    if document.get("@context") != "https://openvex.dev/ns/v0.2.0":
        raise VexValidationError("OpenVEX context must be v0.2.0")
    if f"v{expected_version}" not in str(document.get("@id") or ""):
        raise VexValidationError("OpenVEX document ID does not match release version")
    if not str(document.get("author") or "").strip():
        raise VexValidationError("OpenVEX author is required")
    if document.get("version") != 1:
        raise VexValidationError("OpenVEX document version must be integer 1")
    try:
        datetime.fromisoformat(str(document.get("timestamp") or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise VexValidationError("OpenVEX timestamp must be ISO-8601") from exc

    statements = document.get("statements")
    if not isinstance(statements, list):
        raise VexValidationError("OpenVEX statements must be an array")
    if _release_version(expected_version) >= FUTURE_VEX_BASELINE:
        _validate_reviewed_future_statements(statements)
        return
    names = [
        str(statement.get("vulnerability", {}).get("name") or "")
        for statement in statements
        if isinstance(statement, dict)
    ]
    if set(names) != EXPECTED_CVES or len(names) != len(EXPECTED_CVES):
        raise VexValidationError("OpenVEX must disposition each expected CVE exactly once")

    for statement in statements:
        cve = str(statement["vulnerability"]["name"])
        if statement.get("status") != "not_affected":
            raise VexValidationError(f"{cve} status must be not_affected")
        if statement.get("justification") != "vulnerable_code_not_in_execute_path":
            raise VexValidationError(f"{cve} requires execute-path justification")
        if len(str(statement.get("impact_statement") or "").strip()) < 80:
            raise VexValidationError(f"{cve} impact statement is incomplete")

        products = statement.get("products")
        if not isinstance(products, list) or len(products) != 2:
            raise VexValidationError(f"{cve} must cover both image architectures")
        arches: set[str] = set()
        for product in products:
            product_id = str(product.get("@id") or "") if isinstance(product, dict) else ""
            match = PRODUCT_PATTERN.fullmatch(product_id)
            if match is None:
                raise VexValidationError(f"{cve} has an unexpected product identifier")
            if match.group("version") != EXPECTED_PYTHON_PACKAGE_VERSION:
                raise VexValidationError(f"{cve} targets the wrong Python package version")
            arches.add(match.group("arch"))
        if arches != EXPECTED_ARCHES:
            raise VexValidationError(f"{cve} does not cover amd64 and arm64")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args(argv)
    try:
        document = json.loads(Path(args.document).read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise VexValidationError("OpenVEX root must be an object")
        validate_vex(document, expected_version=args.expected_version)
    except (OSError, json.JSONDecodeError, VexValidationError) as exc:
        print(f"release VEX verification failed: {exc}", file=sys.stderr)
        return 1
    print("release VEX verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
