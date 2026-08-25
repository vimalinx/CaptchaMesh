import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from tools.build_hub_bundle import FILES, build, project_version, verify


ROOT = Path(__file__).resolve().parents[1]


class HubReleaseTests(unittest.TestCase):
    def test_installer_shell_is_valid(self):
        subprocess.run(
            ["bash", "-n", str(ROOT / "deploy/hub/install.sh")], check=True
        )

    def test_bundle_is_complete_and_link_free(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = build(Path(directory) / "new-output-directory")
            verify(archive)
            with tarfile.open(archive, "r:gz") as bundle:
                names = {member.name for member in bundle.getmembers() if member.isfile()}
            prefix = f"captchamesh-hub-v{project_version()}/"
            self.assertEqual(names, {prefix + name for name in FILES.values()})

    def test_installer_preserves_existing_keys(self):
        installer = (ROOT / "deploy/hub/install.sh").read_text()
        self.assertIn('if [[ ! -s "${key_path}" ]]', installer)
        self.assertIn('if [[ ! -e "${opt_root}/registrations.json" ]]', installer)


if __name__ == "__main__":
    unittest.main()
