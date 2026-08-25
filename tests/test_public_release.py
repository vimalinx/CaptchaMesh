from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.verify_public_release import verify


class PublicReleaseBoundaryTest(unittest.TestCase):
    def make_sdist(self, path: Path, members: dict[str, bytes]) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def test_accepts_one_clean_wheel_and_sdist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            with zipfile.ZipFile(dist / "captchamesh-1-py3-none-any.whl", "w") as wheel:
                wheel.writestr("captchamesh/__init__.py", b"__version__ = '1'\n")
            self.make_sdist(
                dist / "captchamesh-1.tar.gz",
                {"captchamesh-1/captchamesh/__init__.py": b"__version__ = '1'\n"},
            )
            self.assertEqual(verify(dist), [])

    def test_rejects_operator_only_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            with zipfile.ZipFile(dist / "captchamesh-1-py3-none-any.whl", "w") as wheel:
                wheel.writestr("captchamesh/__init__.py", b"")
            self.make_sdist(
                dist / "captchamesh-1.tar.gz",
                {"captchamesh-1/webshare_register.py": b"print('local only')\n"},
            )
            self.assertTrue(
                any("private member" in problem for problem in verify(dist))
            )


if __name__ == "__main__":
    unittest.main()
