"""Installer regression checks: preserve unrelated files and back up replacements."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("sidebar_installer", Path(__file__).parents[1] / "tools" / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_preview_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = installer.install(root)
            self.assertFalse(result["applied"])
            self.assertEqual(list(root.iterdir()), [])

    def test_replacement_preserves_backup_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "Mod" / "ArthurCodexCompanion"
            target.mkdir(parents=True)
            (target / "InitGui.py").write_text("original entry point")
            (target / "keep.txt").write_text("unrelated")
            result = installer.install(root, apply=True)
            self.assertEqual((Path(result["backup"]) / "InitGui.py").read_text(), "original entry point")
            self.assertEqual((target / "keep.txt").read_text(), "unrelated")
            self.assertEqual(set(result["files"]), set(result["sha256"]))
            self.assertTrue((Path(result["backup"]) / "install-manifest.json").is_file())
            self.assertNotIn("provider.token", result["files"])

    def test_invalid_later_target_fails_before_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "Mod" / "ArthurCodexCompanion"
            target.mkdir(parents=True)
            (target / "InitGui.py").write_text("original entry point")
            (target / "provider_proxy.py").mkdir()
            with self.assertRaises(ValueError):
                installer.install(root, apply=True)
            self.assertEqual((target / "InitGui.py").read_text(), "original entry point")


if __name__ == "__main__":
    unittest.main()
