"""Install the bundled CaptchaMesh Agent Skill without overwriting user edits."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import runpy
import secrets
import shutil
import stat
import sys
import sysconfig
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


SKILL_NAME = "captchamesh-adapter"
MANAGED_METADATA = ".captchamesh-managed.json"
REQUIRED_MEMBERS = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/adapter-workflow.md",
    "references/protocol.md",
    "scripts/inspect_registration.py",
}


class SkillInstallError(RuntimeError):
    """Raised when installation would be incomplete or overwrite user work."""


@dataclass(frozen=True)
class SkillStatus:
    state: str
    target: str
    source: str
    source_digest: str
    installed_digest: str | None = None
    current_digest: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def bundled_skill_path() -> Path:
    installed = (
        Path(sysconfig.get_path("data"))
        / "share"
        / "captchamesh"
        / "skills"
        / SKILL_NAME
    )
    if installed.is_dir():
        return installed
    source_checkout = Path(__file__).resolve().parent / ".skill" / SKILL_NAME
    if source_checkout.is_dir():
        return source_checkout
    raise SkillInstallError("安装包中缺少 CaptchaMesh Agent Skill，请重新安装 CaptchaMesh")


def default_skill_target() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    root = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return root / "skills" / SKILL_NAME


def run_bundled_inspector(arguments: list[str]) -> int:
    """Run the packaged inspector without relying on the consumer's repository layout."""
    script = bundled_skill_path() / "scripts" / "inspect_registration.py"
    if script.is_symlink() or not script.is_file():
        raise SkillInstallError("安装包中缺少 CaptchaMesh Skill 检查器，请重新安装 CaptchaMesh")
    previous_argv = sys.argv
    try:
        sys.argv = [str(script), *arguments]
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exc:
            if exc.code is None:
                return 0
            return exc.code if isinstance(exc.code, int) else 1
        return 0
    finally:
        sys.argv = previous_argv


def _version() -> str:
    try:
        return importlib.metadata.version("captchamesh")
    except importlib.metadata.PackageNotFoundError:
        return "source"


def _inventory(root: Path, *, require_complete: bool) -> list[tuple[str, Path]]:
    if root.is_symlink() or not root.is_dir():
        raise SkillInstallError(f"Skill 路径必须是普通目录：{root}")
    members: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if MANAGED_METADATA in relative.parts or "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise SkillInstallError(f"Skill 不允许包含符号链接：{relative}")
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix in {".pyc", ".pyo"}:
            continue
        members.append((relative.as_posix(), path))
    names = {name for name, _ in members}
    missing = sorted(REQUIRED_MEMBERS - names)
    if require_complete and missing:
        raise SkillInstallError("Skill 文件不完整，缺少：" + ", ".join(missing))
    return members


def _content_digest(root: Path, *, require_complete: bool) -> str:
    digest = hashlib.sha256()
    for name, path in _inventory(root, require_complete=require_complete):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.S_IMODE(path.stat().st_mode)).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _read_managed_digest(target: Path) -> str | None:
    marker = target / MANAGED_METADATA
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("schemaVersion") != 1 or payload.get("skill") != SKILL_NAME:
        return None
    value = payload.get("contentDigest")
    return value if isinstance(value, str) and len(value) == 64 else None


def skill_status(
    *, source: Path | None = None, target: Path | None = None
) -> SkillStatus:
    source = (source or bundled_skill_path()).resolve()
    target = target or default_skill_target()
    source_digest = _content_digest(source, require_complete=True)
    if not target.exists() and not target.is_symlink():
        return SkillStatus("not-installed", str(target), str(source), source_digest)
    if target.is_symlink() or not target.is_dir():
        return SkillStatus("unmanaged", str(target), str(source), source_digest)
    installed_digest = _read_managed_digest(target)
    if installed_digest is None:
        return SkillStatus("unmanaged", str(target), str(source), source_digest)
    try:
        current_digest = _content_digest(target, require_complete=False)
    except SkillInstallError:
        return SkillStatus(
            "modified", str(target), str(source), source_digest, installed_digest
        )
    if current_digest != installed_digest:
        return SkillStatus(
            "modified",
            str(target),
            str(source),
            source_digest,
            installed_digest,
            current_digest,
        )
    state = "current" if installed_digest == source_digest else "update-available"
    return SkillStatus(
        state,
        str(target),
        str(source),
        source_digest,
        installed_digest,
        current_digest,
    )


def _stage_skill(source: Path, parent: Path, digest: str) -> Path:
    stage = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=parent))
    try:
        for name, path in _inventory(source, require_complete=True):
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        marker = {
            "schemaVersion": 1,
            "skill": SKILL_NAME,
            "contentDigest": digest,
            "captchameshVersion": _version(),
        }
        (stage / MANAGED_METADATA).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def install_skill(
    *, source: Path | None = None, target: Path | None = None
) -> tuple[str, SkillStatus]:
    source = (source or bundled_skill_path()).resolve()
    target = target or default_skill_target()
    status = skill_status(source=source, target=target)
    if status.state == "current":
        return "current", status
    if status.state in {"unmanaged", "modified"}:
        raise SkillInstallError(
            f"目标 Skill {status.state}，为保护现有内容未覆盖：{target}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = _stage_skill(source, target.parent, status.source_digest)
    action = "installed"
    if status.state == "not-installed":
        try:
            os.replace(stage, target)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    else:
        action = "updated"
        latest = skill_status(source=source, target=target)
        if (
            latest.state != "update-available"
            or latest.current_digest != status.current_digest
        ):
            shutil.rmtree(stage, ignore_errors=True)
            raise SkillInstallError(
                f"目标 Skill 在更新过程中发生变化，为保护现有内容未覆盖：{target}"
            )
        backup = target.with_name(f".{SKILL_NAME}.backup-{secrets.token_hex(6)}")
        os.replace(target, backup)
        try:
            os.replace(stage, target)
        except Exception:
            os.replace(backup, target)
            shutil.rmtree(stage, ignore_errors=True)
            raise
        shutil.rmtree(backup)
    return action, skill_status(source=source, target=target)


def format_status(status: SkillStatus) -> str:
    labels = {
        "not-installed": "未安装",
        "current": "已安装，版本一致",
        "update-available": "已有安全更新",
        "modified": "检测到用户修改，未覆盖",
        "unmanaged": "已有非 CaptchaMesh 管理的目录，未覆盖",
    }
    return f"CaptchaMesh Agent Skill：{labels[status.state]}\n位置：{status.target}"
