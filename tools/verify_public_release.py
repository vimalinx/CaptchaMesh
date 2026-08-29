#!/usr/bin/env python3
"""Fail closed when a Python release archive crosses the public boundary."""
from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_PARTS = {".ai", ".git", ".secrets", "pwa", "tests"}
FORBIDDEN_NAMES = {
    "AGENTS.md",
    "accounts.jsonl",
    "daemon.py",
    "phone_connect_proxy.py",
    "registrations.json",
    "termux_setup.sh",
    "webshare_register.py",
    "worker.py",
    "worker_in_proot.py",
}
FORBIDDEN_BYTES = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"/home/",
    b"\\Users\\",
    b"webshare_register",
)
REQUIRED_SKILL_MEMBERS = {
    "captchamesh-adapter/SKILL.md",
    "captchamesh-adapter/agents/openai.yaml",
    "captchamesh-adapter/references/adapter-workflow.md",
    "captchamesh-adapter/references/protocol.md",
    "captchamesh-adapter/scripts/inspect_registration.py",
}


def check_member(name: str, data: bytes) -> list[str]:
    problems: list[str] = []
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        problems.append(f"unsafe archive path: {name}")
    private_deploy = any(
        left == "deploy" and right == "local"
        for left, right in zip(path.parts, path.parts[1:])
    )
    if (
        FORBIDDEN_PARTS.intersection(path.parts)
        or path.name in FORBIDDEN_NAMES
        or private_deploy
    ):
        problems.append(f"private member: {name}")
    for marker in FORBIDDEN_BYTES:
        if marker.lower() in data.lower():
            problems.append(f"private marker in: {name}")
    return problems


def check_skill_bundle(names: list[str]) -> list[str]:
    normalized = {PurePosixPath(name).as_posix() for name in names}
    missing = sorted(
        member
        for member in REQUIRED_SKILL_MEMBERS
        if not any(name.endswith("/" + member) for name in normalized)
    )
    return ["missing Agent Skill member: " + member for member in missing]


def inspect_wheel(path: Path) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names: list[str] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            names.append(info.filename)
            problems.extend(check_member(info.filename, archive.read(info)))
        problems.extend(check_skill_bundle(names))
    return problems


def inspect_sdist(path: Path) -> list[str]:
    problems: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        names: list[str] = []
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                problems.append(f"archive link is not allowed: {member.name}")
                continue
            if not member.isfile():
                continue
            names.append(member.name)
            source = archive.extractfile(member)
            problems.extend(check_member(member.name, source.read() if source else b""))
        problems.extend(check_skill_bundle(names))
    return problems


def verify(dist: Path) -> list[str]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    expected = {*wheels, *sdists}
    unexpected = sorted(
        path.name for path in dist.iterdir() if path.is_file() and path not in expected
    )
    problems: list[str] = []
    if len(wheels) != 1 or len(sdists) != 1:
        problems.append(
            "expected exactly one wheel and one sdist, got "
            f"{len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    if unexpected:
        problems.append("unexpected dist files: " + ", ".join(unexpected))
    for path in wheels:
        problems.extend(inspect_wheel(path))
    for path in sdists:
        problems.extend(inspect_sdist(path))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args()
    if not args.dist.is_dir():
        parser.error(f"distribution directory does not exist: {args.dist}")
    problems = verify(args.dist)
    if problems:
        for problem in problems:
            print(f"release boundary violation: {problem}", file=sys.stderr)
        return 1
    print("release boundary verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
