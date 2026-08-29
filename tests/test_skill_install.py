from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from captchamesh_cli import build_parser, main
from skill_manager import (
    MANAGED_METADATA,
    SkillInstallError,
    default_skill_target,
    install_skill,
    run_bundled_inspector,
    skill_status,
)


class SkillInstallTest(unittest.TestCase):
    def make_source(self, root: Path, label: str = "one") -> Path:
        source = root / "source" / "captchamesh-adapter"
        files = {
            "SKILL.md": f"---\nname: captchamesh-adapter\ndescription: {label}\n---\n",
            "agents/openai.yaml": "interface:\n  display_name: CaptchaMesh\n",
            "references/adapter-workflow.md": f"workflow {label}\n",
            "references/protocol.md": "protocol\n",
            "scripts/inspect_registration.py": "print('inspect')\n",
        }
        for name, value in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        return source

    def test_default_target_honors_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"CODEX_HOME": directory}, clear=False
        ):
            self.assertEqual(
                default_skill_target(), Path(directory) / "skills" / "captchamesh-adapter"
            )

    def test_first_install_is_complete_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            target = root / "codex" / "skills" / "captchamesh-adapter"
            action, status = install_skill(source=source, target=target)
            self.assertEqual(action, "installed")
            self.assertEqual(status.state, "current")
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertTrue((target / "references/protocol.md").is_file())
            self.assertTrue((target / MANAGED_METADATA).is_file())
            action, status = install_skill(source=source, target=target)
            self.assertEqual(action, "current")
            self.assertEqual(status.state, "current")

    def test_untouched_managed_copy_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            target = root / "codex" / "skills" / "captchamesh-adapter"
            install_skill(source=source, target=target)
            (source / "references/protocol.md").write_text("protocol two\n", encoding="utf-8")
            self.assertEqual(skill_status(source=source, target=target).state, "update-available")
            action, status = install_skill(source=source, target=target)
            self.assertEqual(action, "updated")
            self.assertEqual(status.state, "current")
            self.assertEqual(
                (target / "references/protocol.md").read_text(encoding="utf-8"),
                "protocol two\n",
            )

    def test_user_modified_copy_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            target = root / "codex" / "skills" / "captchamesh-adapter"
            install_skill(source=source, target=target)
            skill = target / "SKILL.md"
            skill.write_text("user customization\n", encoding="utf-8")
            with self.assertRaisesRegex(SkillInstallError, "modified"):
                install_skill(source=source, target=target)
            self.assertEqual(skill.read_text(encoding="utf-8"), "user customization\n")
            self.assertEqual(skill_status(source=source, target=target).state, "modified")

    def test_permission_change_is_treated_as_user_modification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            target = root / "codex" / "skills" / "captchamesh-adapter"
            install_skill(source=source, target=target)
            script = target / "scripts/inspect_registration.py"
            script.chmod(0o700)
            self.assertEqual(skill_status(source=source, target=target).state, "modified")
            with self.assertRaisesRegex(SkillInstallError, "modified"):
                install_skill(source=source, target=target)

    def test_unmanaged_target_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            target = root / "codex" / "skills" / "captchamesh-adapter"
            target.mkdir(parents=True)
            existing = target / "SKILL.md"
            existing.write_text("existing\n", encoding="utf-8")
            with self.assertRaisesRegex(SkillInstallError, "unmanaged"):
                install_skill(source=source, target=target)
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing\n")

    def test_cli_exposes_skill_install_and_status(self) -> None:
        parser = build_parser()
        install = parser.parse_args(["skill", "install", "--target", "/tmp/skill"])
        self.assertEqual(install.command, "skill")
        self.assertEqual(install.skill_command, "install")
        status = parser.parse_args(["skill", "status", "--json"])
        self.assertTrue(status.json)
        inspect = parser.parse_args(
            ["skill", "inspect", "/tmp/project", "--mode", "agent-api", "--json"]
        )
        self.assertEqual(
            inspect.arguments,
            ["/tmp/project", "--mode", "agent-api", "--json"],
        )

    def test_bundled_inspector_runs_outside_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "consumer"
            target.mkdir()
            (target / "captcha.py").write_text(
                "from twocaptcha import TwoCaptcha\nsolver = TwoCaptcha('placeholder')\n",
                encoding="utf-8",
            )
            with patch("skill_manager.bundled_skill_path", return_value=Path(
                ".skill/captchamesh-adapter"
            ).resolve()):
                self.assertEqual(
                    run_bundled_inspector(
                        [str(target), "--mode", "agent-api", "--json"]
                    ),
                    0,
                )

    def test_cli_without_subcommand_starts_the_bridge(self) -> None:
        with patch("sys.argv", ["captchamesh"]), patch(
            "captchamesh_cli.run_start", new_callable=AsyncMock
        ) as run_start:
            self.assertEqual(main(), 0)
        run_start.assert_awaited_once()
        self.assertEqual(run_start.await_args.args[0].command, "start")

    def test_readme_installer_installs_the_skill(self) -> None:
        install_script = Path("install.sh").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn('"$venv_dir/bin/captchamesh" skill install', install_script)
        self.assertIn("captchamesh skill status", readme)
        self.assertIn(
            "$captchamesh-adapter 检查当前项目的 2captcha-python 接入", readme
        )
        self.assertIn("CaptchaMesh Skill ready", readme)
        skill = Path(".skill/captchamesh-adapter/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("captchamesh skill inspect", skill)
        self.assertNotIn(
            "python3 .skill/captchamesh-adapter/scripts/inspect_registration.py",
            skill,
        )

    def test_status_json_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            target = root / "missing"
            payload = json.loads(skill_status(source=source, target=target).to_json())
            self.assertEqual(payload["state"], "not-installed")
            self.assertEqual(payload["target"], str(target))


if __name__ == "__main__":
    unittest.main()
