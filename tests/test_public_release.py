from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.verify_public_release import verify
from tools.verify_sbom import verify as verify_sbom


SKILL_FILES = {
    "captchamesh-adapter/SKILL.md": b"---\nname: captchamesh-adapter\ndescription: test\n---\n",
    "captchamesh-adapter/agents/openai.yaml": b"interface: {}\n",
    "captchamesh-adapter/references/adapter-workflow.md": b"workflow\n",
    "captchamesh-adapter/references/protocol.md": b"protocol\n",
    "captchamesh-adapter/scripts/inspect_registration.py": b"pass\n",
}


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
                for name, data in SKILL_FILES.items():
                    wheel.writestr("share/captchamesh/skills/" + name, data)
            self.make_sdist(
                dist / "captchamesh-1.tar.gz",
                {
                    "captchamesh-1/captchamesh/__init__.py": b"__version__ = '1'\n",
                    **{
                        "captchamesh-1/.skill/" + name: data
                        for name, data in SKILL_FILES.items()
                    },
                },
            )
            self.assertEqual(verify(dist), [])

    def test_rejects_operator_only_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            with zipfile.ZipFile(dist / "captchamesh-1-py3-none-any.whl", "w") as wheel:
                wheel.writestr("captchamesh/__init__.py", b"")
                for name, data in SKILL_FILES.items():
                    wheel.writestr("share/captchamesh/skills/" + name, data)
            self.make_sdist(
                dist / "captchamesh-1.tar.gz",
                {
                    "captchamesh-1/webshare_register.py": b"print('local only')\n",
                    **{
                        "captchamesh-1/.skill/" + name: data
                        for name, data in SKILL_FILES.items()
                    },
                },
            )
            self.assertTrue(
                any("private member" in problem for problem in verify(dist))
            )

    def test_rejects_archive_without_agent_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            with zipfile.ZipFile(dist / "captchamesh-1-py3-none-any.whl", "w") as wheel:
                wheel.writestr("captchamesh/__init__.py", b"")
            self.make_sdist(
                dist / "captchamesh-1.tar.gz",
                {"captchamesh-1/captchamesh/__init__.py": b""},
            )
            problems = verify(dist)
            self.assertTrue(any("missing Agent Skill member" in item for item in problems))

    def test_release_checksums_use_downloadable_basenames(self) -> None:
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn(
            "(cd dist && sha256sum *.whl *.tar.gz *.apk *.spdx.json) > dist/SHA256SUMS",
            workflow,
        )
        self.assertNotIn("sha256sum dist/*", workflow)

    def test_release_waits_for_full_ci_and_attests_artifacts(self) -> None:
        release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_call:", ci)
        self.assertIn("uses: ./.github/workflows/ci.yml", release)
        self.assertIn("needs: quality", release)
        self.assertIn("anchore/sbom-action@", release)
        self.assertIn("actions/attest-build-provenance@", release)
        self.assertIn("attestations: write", release)

    def test_actions_are_sha_pinned_and_security_scans_history(self) -> None:
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path(".github/workflows").glob("*.yml")
        )
        for line in workflows.splitlines():
            if "uses:" not in line or "./.github/" in line:
                continue
            reference = line.split("uses:", 1)[1].strip().split()[0]
            self.assertRegex(reference, r"@[a-f0-9]{40}$")
        self.assertIn("fetch-depth: 0", workflows)
        self.assertIn("gitleaks/gitleaks-action@", workflows)

    def test_open_source_community_files_are_complete(self) -> None:
        required = {
            "CODE_OF_CONDUCT.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "README.md",
            "SECURITY.md",
            "SUPPORT.md",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/pull_request_template.md",
            ".github/dependabot.yml",
        }
        self.assertFalse([path for path in required if not Path(path).is_file()])
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("[社区行为准则](CODE_OF_CONDUCT.md)", readme)
        self.assertIn("[支持说明](SUPPORT.md)", readme)
        dependabot = Path(".github/dependabot.yml").read_text(encoding="utf-8")
        for ecosystem in ("pip", "gradle", "github-actions"):
            self.assertIn(f"package-ecosystem: {ecosystem}", dependabot)

    def test_sbom_validation_requires_inventory_and_public_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sbom = Path(directory) / "sbom.json"
            sbom.write_text(
                json.dumps(
                    {
                        "spdxVersion": "SPDX-2.3",
                        "packages": [{"name": "captchamesh"}],
                        "relationships": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(verify_sbom(sbom), [])
            sbom.write_text(
                json.dumps(
                    {
                        "spdxVersion": "SPDX-2.3",
                        "packages": [{"name": "/home/user/private"}],
                        "relationships": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(any("private boundary" in item for item in verify_sbom(sbom)))


if __name__ == "__main__":
    unittest.main()
