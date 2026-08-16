"""Metadata and manifest parity tests for system-gap-master."""

import json
import unittest
from pathlib import Path

import system_gap_master

try:
    import tomllib
except ImportError:
    import tomli as tomllib


class MetadataParityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent

    def test_version_consistency(self):
        pyproject_path = self.root / "pyproject.toml"
        self.assertTrue(pyproject_path.exists(), "pyproject.toml must exist")
        pyproject_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        pyproject_version = pyproject_data["project"]["version"]

        self.assertEqual(
            system_gap_master.__version__,
            pyproject_version,
            f"Package version {system_gap_master.__version__} does not match pyproject.toml {pyproject_version}",
        )

    def test_module_manifest_validity(self):
        manifest_path = self.root / "ellmos-module.v2.json"
        self.assertTrue(manifest_path.exists(), "ellmos-module.v2.json must exist")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema"), "ellmos.module.v2")
        self.assertEqual(data.get("id"), "system-gap-master")
        self.assertIn("provides", data)

    def test_security_policy_exists(self):
        security_path = self.root / "SECURITY.md"
        self.assertTrue(security_path.exists(), "SECURITY.md must exist")
        content = security_path.read_text(encoding="utf-8")
        self.assertIn("Security Policy", content)
        self.assertIn("Geltungsbereich", content)
        self.assertIn("Scope", content)

    def test_readme_bilingual_presence(self):
        en_readme = self.root / "README.md"
        de_readme = self.root / "README_de.md"
        self.assertTrue(en_readme.exists(), "README.md must exist")
        self.assertTrue(de_readme.exists(), "README_de.md must exist")

        en_text = en_readme.read_text(encoding="utf-8")
        de_text = de_readme.read_text(encoding="utf-8")
        self.assertIn("system-gap-master", en_text)
        self.assertIn("system-gap-master", de_text)
        self.assertIn("sqlite-transit-sync", en_text)
        self.assertIn("sqlite-transit-sync", de_text)

    def test_llms_txt_presence(self):
        llms_path = self.root / "llms.txt"
        self.assertTrue(llms_path.exists(), "llms.txt must exist")
        content = llms_path.read_text(encoding="utf-8")
        self.assertIn("system-gap-master", content)
        self.assertIn("Last-checked:", content)


if __name__ == "__main__":
    unittest.main()
