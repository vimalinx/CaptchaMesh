"""Bounded, redacted diagnostics for the local CaptchaMesh bridge."""
from __future__ import annotations

import json
import os
import re
import stat
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path


MAX_LOG_BYTES = 256 * 1024
MAX_FRAMES = 12
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_.-]")


class DiagnosticLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._lock = threading.Lock()

    def event(
        self,
        component: str,
        event: str,
        error: BaseException | None = None,
    ) -> None:
        record: dict[str, object] = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "component": self._label(component),
            "event": self._label(event),
        }
        if error is not None:
            record["errorType"] = self._label(
                f"{type(error).__module__}.{type(error).__qualname__}"
            )
            record["frames"] = [
                {
                    "file": self._label(Path(frame.filename).name),
                    "function": self._label(frame.name),
                    "line": max(frame.lineno, 0),
                }
                for frame in traceback.extract_tb(error.__traceback__)[-MAX_FRAMES:]
            ]
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        self._append(line.encode("utf-8"))

    def read(self) -> str:
        if not self.path.exists():
            return ""
        with self._lock:
            descriptor = self._open(os.O_RDONLY)
            try:
                chunks: list[bytes] = []
                remaining = MAX_LOG_BYTES
                while remaining > 0:
                    chunk = os.read(descriptor, min(8192, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace")
            finally:
                os.close(descriptor)

    def clear(self) -> None:
        with self._lock:
            descriptor = self._open(os.O_RDWR | os.O_CREAT)
            try:
                os.ftruncate(descriptor, 0)
            finally:
                os.close(descriptor)

    def _append(self, line: bytes) -> None:
        with self._lock:
            descriptor = self._open(os.O_RDWR | os.O_CREAT)
            try:
                size = os.fstat(descriptor).st_size
                if size + len(line) > MAX_LOG_BYTES:
                    keep = min(size, MAX_LOG_BYTES // 2)
                    os.lseek(descriptor, max(0, size - keep), os.SEEK_SET)
                    retained = os.read(descriptor, keep)
                    first_newline = retained.find(b"\n")
                    if first_newline >= 0:
                        retained = retained[first_newline + 1 :]
                    os.ftruncate(descriptor, 0)
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    self._write_all(descriptor, retained)
                os.lseek(descriptor, 0, os.SEEK_END)
                self._write_all(descriptor, line[: MAX_LOG_BYTES // 4])
            finally:
                os.close(descriptor)

    def _open(self, flags: int) -> int:
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor)
            raise RuntimeError("diagnostic log must be a single-link regular file")
        if flags & (os.O_WRONLY | os.O_RDWR):
            os.fchmod(descriptor, 0o600)
        return descriptor

    @staticmethod
    def _write_all(descriptor: int, value: bytes) -> None:
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written < 1:
                raise OSError("could not write diagnostic log")
            remaining = remaining[written:]

    @staticmethod
    def _label(value: str) -> str:
        cleaned = _SAFE_LABEL.sub("_", value or "unknown")
        return cleaned[:120]
