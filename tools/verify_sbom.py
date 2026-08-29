#!/usr/bin/env python3
"""Validate the public SPDX SBOM without printing its potentially large content."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


FORBIDDEN_MARKERS = (b"/home/", b".secrets/", b".ai/", b"registrations.json")


def verify(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"unreadable SPDX JSON: {exc}"]
    if document.get("spdxVersion") != "SPDX-2.3":
        problems.append("expected SPDX-2.3")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        problems.append("SBOM package inventory is empty")
    relationships = document.get("relationships")
    if not isinstance(relationships, list):
        problems.append("SBOM relationships must be a list")
    lowered = raw.lower()
    for marker in FORBIDDEN_MARKERS:
        if marker.lower() in lowered:
            problems.append(f"private boundary marker present: {marker.decode()}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sbom", type=Path)
    args = parser.parse_args()
    problems = verify(args.sbom)
    if problems:
        for problem in problems:
            print(f"SBOM validation failed: {problem}")
        return 1
    document = json.loads(args.sbom.read_text(encoding="utf-8"))
    print(
        "SBOM verified: "
        f"packages={len(document['packages'])} "
        f"relationships={len(document['relationships'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
