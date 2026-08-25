#!/usr/bin/env python3
"""Verify that a release tag matches Python and Android version metadata."""
from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def versions() -> tuple[str, str]:
    with (ROOT / "pyproject.toml").open("rb") as source:
        python_version = str(tomllib.load(source)["project"]["version"])
    gradle = (ROOT / "app-src/app/build.gradle.kts").read_text()
    match = re.search(r'versionName\s*=\s*"([^"]+)"', gradle)
    if match is None:
        raise RuntimeError("Android versionName is missing")
    return python_version, match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    args = parser.parse_args()
    python_version, android_version = versions()
    expected = f"v{python_version}"
    if args.tag != expected or android_version != python_version:
        parser.error(
            f"release mismatch: tag={args.tag}, Python={python_version}, "
            f"Android={android_version}; expected tag {expected}"
        )
    print(f"release metadata verified: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
