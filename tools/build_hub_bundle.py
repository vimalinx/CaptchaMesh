#!/usr/bin/env python3
"""Build the self-contained, public CaptchaMesh Hub release archive."""
from __future__ import annotations

import argparse
import gzip
import io
import tarfile
import tomllib
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "LICENSE": "LICENSE",
    "CHANGELOG.md": "CHANGELOG.md",
    "broker.py": "broker.py",
    "broker_asgi.py": "broker_asgi.py",
    "challenge_protocol.py": "challenge_protocol.py",
    "relay_protocol.py": "relay_protocol.py",
    "twocaptcha_compat.py": "twocaptcha_compat.py",
    "requirements.txt": "requirements.txt",
    "tests/fixtures/empty_registrations.json": "tests/fixtures/empty_registrations.json",
    "deploy/hub/README.md": "README.md",
    "deploy/hub/install.sh": "deploy/hub/install.sh",
    "deploy/hub/captchamesh-hub.service": "deploy/hub/captchamesh-hub.service",
    "deploy/hub/cloudflared.service.in": "deploy/hub/cloudflared.service.in",
    "deploy/hub/captchamesh-backup": "deploy/hub/captchamesh-backup",
    "deploy/hub/captchamesh-backup.service": "deploy/hub/captchamesh-backup.service",
    "deploy/hub/captchamesh-backup.timer": "deploy/hub/captchamesh-backup.timer",
}
EXECUTABLES = {"deploy/hub/install.sh", "deploy/hub/captchamesh-backup"}
FORBIDDEN_BYTES = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"/home/",
    b"\\Users\\",
    b".secrets",
)


def project_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as source:
        return str(tomllib.load(source)["project"]["version"])


def collect() -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for source_name, archive_name in FILES.items():
        source = PROJECT_ROOT / source_name
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"required regular file is missing: {source_name}")
        data = source.read_bytes()
        for marker in FORBIDDEN_BYTES:
            if marker.lower() in data.lower():
                raise RuntimeError(f"private marker in public Hub file: {source_name}")
        members[archive_name] = data
    return members


def build(output: Path) -> Path:
    version = project_version()
    if not output.name.endswith(".tar.gz"):
        output = output / f"captchamesh-hub-v{version}.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    root = PurePosixPath(f"captchamesh-hub-v{version}")
    members = collect()
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in sorted(members):
                    data = members[name]
                    info = tarfile.TarInfo(str(root / name))
                    info.size = len(data)
                    info.mode = 0o755 if name in EXECUTABLES else 0o644
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    archive.addfile(info, io.BytesIO(data))
    return output


def verify(path: Path) -> None:
    expected_root = f"captchamesh-hub-v{project_version()}"
    expected = {f"{expected_root}/{name}" for name in FILES.values()}
    with tarfile.open(path, "r:gz") as archive:
        actual = {member.name for member in archive.getmembers() if member.isfile()}
        links = [member.name for member in archive.getmembers() if member.issym() or member.islnk()]
    if links:
        raise RuntimeError("Hub archive contains links: " + ", ".join(links))
    if actual != expected:
        raise RuntimeError(f"Hub archive members differ: expected {expected}, got {actual}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist"),
        help="output directory or explicit .tar.gz path",
    )
    args = parser.parse_args()
    output = build(args.output)
    verify(output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
