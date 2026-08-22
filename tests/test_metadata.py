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
        self.assertIn("sync.ticket-route-intent-adapter", data["provides"])
        adapters = {adapter["id"]: adapter for adapter in data["adapters"]}
        self.assertEqual(
            adapters["ticket-route-intent"]["optional_dependency"],
            "ticket-master>=1.11,<1.12",
        )

    def test_security_policy_exists(self):
        security_path = self.root / "SECURITY.md"
        self.assertTrue(security_path.exists(), "SECURITY.md must exist")
        content = security_path.read_text(encoding="utf-8")
        self.assertIn("Security Policy", content)
        self.assertIn("Geltungsbereich", content)
        self.assertIn("Scope", content)
        self.assertIn("security@ellmos.ai", content)
        self.assertIn("support@lukasgeiger.com", content)
        self.assertIn("github.com/ellmos-ai/system-gap-master/security/advisories", content)
        self.assertIn("Zero-Egress", content)
        self.assertIn("Non-Elevation", content)

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
        self.assertIn("Quick Navigation", en_text)
        self.assertIn("Schnellnavigation", de_text)

    def test_readme_badges_parity(self):
        en_readme = self.root / "README.md"
        de_readme = self.root / "README_de.md"
        en_text = en_readme.read_text(encoding="utf-8")
        de_text = de_readme.read_text(encoding="utf-8")

        for text in (en_text, de_text):
            self.assertIn("actions/workflows/tests.yml/badge.svg", text)
            self.assertIn("1.5.0", text)
            self.assertIn("3.10", text)
            self.assertIn("3.13", text)
            self.assertIn("Zero--Egress", text)
            self.assertIn("Fail--Closed", text)
            self.assertIn("176%20passed", text)
            self.assertIn("open--bricks", text)
            self.assertIn("MIT", text)

    def test_llms_txt_presence(self):
        llms_path = self.root / "llms.txt"
        self.assertTrue(llms_path.exists(), "llms.txt must exist")
        content = llms_path.read_text(encoding="utf-8")
        self.assertIn("system-gap-master", content)
        self.assertIn("Last-checked: 2026-08-22", content)
        self.assertIn("176 tests passed", content)
        self.assertIn("https://github.com/ellmos-ai/system-gap-master", content)

    def test_ci_workflow_integrity(self):
        ci_path = self.root / ".github" / "workflows" / "tests.yml"
        self.assertTrue(ci_path.exists(), ".github/workflows/tests.yml must exist")
        ci_content = ci_path.read_text(encoding="utf-8")
        self.assertIn("ubuntu-latest", ci_content)
        self.assertIn("windows-latest", ci_content)
        self.assertIn("macos-latest", ci_content)
        self.assertIn('"3.13"', ci_content)
        self.assertIn("ruff check .", ci_content)

    def test_pyproject_pep621_metadata(self):
        pyproject_path = self.root / "pyproject.toml"
        self.assertTrue(pyproject_path.exists(), "pyproject.toml must exist")
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        classifiers = data["project"]["classifiers"]
        self.assertIn("Programming Language :: Python :: 3.13", classifiers)
        self.assertIn("Operating System :: OS Independent", classifiers)
        self.assertIn("Topic :: System :: Distributed Computing", classifiers)
        self.assertIn("Topic :: System :: Recovery Tools", classifiers)

        urls = data["project"]["urls"]
        self.assertIn("Homepage", urls)
        self.assertIn("Documentation", urls)
        self.assertIn("Repository", urls)
        self.assertIn("Changelog", urls)
        self.assertIn("Bug Tracker", urls)
        self.assertEqual(
            data["project"]["optional-dependencies"]["ticket-routing"],
            ["ticket-master>=1.11,<1.12"],
        )

    def test_ecosystem_sibling_tools_matrix(self):
        en_readme = self.root / "README.md"
        de_readme = self.root / "README_de.md"
        en_text = en_readme.read_text(encoding="utf-8")
        de_text = de_readme.read_text(encoding="utf-8")

        required_tools = [
            "sqlite-transit-sync",
            "memoryhooker",
            "workflowhooker",
            "system-explorer",
            "policy-registry",
            "ellmos-delegation-authority",
            "ellmos-controlcenter-mcp",
            "ellmos-filecommander-mcp",
            "ellmos-codecommander-mcp",
            "n8n-manager-mcp",
            "lock-master",
            "ticket-master",
            "clutch",
            "coma",
            "safe-start-for-codex",
            "DevCenter",
            "CodeBox",
            "MethodenAnalyser",
            "PDFtoPDFocr",
            "CleanMarkdown",
            "open-bricks",
        ]

        for tool in required_tools:
            self.assertIn(tool, en_text, f"{tool} missing in README.md")
            self.assertIn(tool, de_text, f"{tool} missing in README_de.md")

    def test_mermaid_diagrams_presence(self):
        en_readme = self.root / "README.md"
        de_readme = self.root / "README_de.md"
        en_text = en_readme.read_text(encoding="utf-8")
        de_text = de_readme.read_text(encoding="utf-8")

        self.assertIn("flowchart TD", en_text)
        self.assertIn("sequenceDiagram", en_text)
        self.assertIn("flowchart TD", de_text)
        self.assertIn("sequenceDiagram", de_text)


if __name__ == "__main__":
    unittest.main()
