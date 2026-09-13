"""Metadata and manifest parity tests for system-gap-master."""

import json
import re
import unittest
from pathlib import Path

import system_gap_master

try:
    import tomllib
except ImportError:
    import tomli as tomllib


# `ticket-master` is an ellmos module, so it is pulled from a pinned git source
# instead of a bare package name -- a bare name resolves against PyPI, which does not
# know our namespaces (Plan D 10.7, T-20260913-598571635). v1.11.3 is the last tag
# inside the previous `>=1.11,<1.12` bound; the bound itself is NOT raised here.
TICKET_MASTER_REQUIREMENT = (
    "ticket-master @ git+https://github.com/ellmos-ai/ticket-master@v1.11.3"
)


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
            TICKET_MASTER_REQUIREMENT,
            "the manifest must declare the same requirement as pyproject.toml",
        )

    def test_security_policy_exists(self):
        security_path = self.root / "SECURITY.md"
        self.assertTrue(security_path.exists(), "SECURITY.md must exist")
        content = security_path.read_text(encoding="utf-8")
        self.assertIn("Security Policy", content)
        self.assertIn("Geltungsbereich", content)
        self.assertIn("Scope", content)
        self.assertIn("security@open-bricks.org", content)
        self.assertIn("security@ellmos.ai", content)
        self.assertIn("support@lukasgeiger.com", content)
        self.assertIn("github.com/ellmos-ai/system-gap-master/security/advisories", content)
        self.assertIn("48 Stunden", content)
        self.assertIn("48 hours", content)
        self.assertIn("Unterstützte Versionen", content)
        self.assertIn("Supported Versions", content)
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
            self.assertIn("1.6.1", text)
            self.assertIn("3.10", text)
            self.assertIn("3.13", text)
            self.assertIn("Zero--Egress", text)
            self.assertIn("Fail--Closed", text)
            self.assertIn("215%20passed", text)
            self.assertIn("open--bricks", text)
            self.assertIn("MIT", text)
            self.assertIn("ruff", text)
            self.assertIn("THIRD_PARTY_LICENSES.md", text)
            self.assertIn("MARKETING-LOG.txt", text)

    def test_llms_txt_presence(self):
        llms_path = self.root / "llms.txt"
        self.assertTrue(llms_path.exists(), "llms.txt must exist")
        content = llms_path.read_text(encoding="utf-8")
        self.assertIn("system-gap-master", content)
        self.assertIn("Last-checked: 2026-09-12", content)
        self.assertIn("215 tests passed", content)
        self.assertIn("https://github.com/ellmos-ai/system-gap-master", content)

    def test_ci_workflow_integrity(self):
        ci_path = self.root / ".github" / "workflows" / "tests.yml"
        self.assertTrue(ci_path.exists(), ".github/workflows/tests.yml must exist")
        ci_content = ci_path.read_text(encoding="utf-8")
        self.assertIn("ubuntu-latest", ci_content)
        self.assertIn("windows-latest", ci_content)
        self.assertIn("macos-latest", ci_content)
        self.assertIn('"3.13"', ci_content)
        self.assertIn("concurrency:", ci_content)
        self.assertIn("cancel-in-progress: true", ci_content)
        self.assertIn("timeout-minutes: 15", ci_content)
        self.assertIn("ruff check .", ci_content)
        self.assertIn("compileall", ci_content)
        self.assertIn("pytest -ra -v", ci_content)

    def test_gitignore_hygiene(self):
        gitignore_path = self.root / ".gitignore"
        self.assertTrue(gitignore_path.exists(), ".gitignore must exist")
        gi_content = gitignore_path.read_text(encoding="utf-8")
        self.assertIn("*.sync-conflict-*", gi_content)
        self.assertIn("*-CONFLIT-*", gi_content)
        self.assertIn("*-WORKSTATION*", gi_content)
        self.assertIn("*-ASUS-GEI*", gi_content)
        self.assertIn("* (kopie)*", gi_content)
        self.assertIn("LOCK.*", gi_content)
        self.assertIn("*.lock", gi_content)
        self.assertIn("LOCK.permissions.json", gi_content)
        self.assertIn(".pytest_cache/", gi_content)
        self.assertIn(".ruff_cache/", gi_content)

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
        self.assertIn("Security", urls)
        self.assertIn("Parent Organization", urls)
        self.assertIn("Umbrella Ecosystem", urls)
        self.assertEqual(
            data["project"]["optional-dependencies"]["ticket-routing"],
            [TICKET_MASTER_REQUIREMENT],
        )
        # The point of this assertion is the BOUND, not the spelling. Before the pin it
        # read `ticket-master>=1.11,<1.12`; a git tag carries the same promise as long
        # as it stays on a 1.11 tag. Raising it to 1.12 is AU-2026-09-12-C and belongs
        # to the user, so it has to keep failing here.
        self.assertIn("@v1.11.", TICKET_MASTER_REQUIREMENT,
                      "the 1.11 bound must not be raised without AU-2026-09-12-C")
        self.assertIn("git+https://github.com/ellmos-ai/ticket-master",
                      TICKET_MASTER_REQUIREMENT,
                      "own modules are pulled from a pinned source, never by bare name")


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

    def test_readme_15_point_navigation_parity(self):
        en_readme = self.root / "README.md"
        de_readme = self.root / "README_de.md"
        en_text = en_readme.read_text(encoding="utf-8")
        de_text = de_readme.read_text(encoding="utf-8")

        en_nav_items = re.findall(r"^(\d+)\.\s+\[([^\]]+)\]\(#([^\)]+)\)", en_text, re.MULTILINE)
        de_nav_items = re.findall(r"^(\d+)\.\s+\[([^\]]+)\]\(#([^\)]+)\)", de_text, re.MULTILINE)

        self.assertEqual(len(en_nav_items), 15, f"Expected 15 items in README.md navigation, got {len(en_nav_items)}")
        self.assertEqual(len(de_nav_items), 15, f"Expected 15 items in README_de.md navigation, got {len(de_nav_items)}")

        for i, (num, title, _anchor) in enumerate(en_nav_items, start=1):
            self.assertEqual(int(num), i)
            first_keyword = title.split("&")[0].strip()
            self.assertTrue(
                re.search(rf"^#+\s+.*{re.escape(first_keyword)}", en_text, re.MULTILINE | re.IGNORECASE),
                f"Heading for '{title}' not found in README.md",
            )

        for i, (num, title, _anchor) in enumerate(de_nav_items, start=1):
            self.assertEqual(int(num), i)
            first_keyword = title.split("&")[0].strip()
            self.assertTrue(
                re.search(rf"^#+\s+.*{re.escape(first_keyword)}", de_text, re.MULTILINE | re.IGNORECASE),
                f"Heading for '{title}' not found in README_de.md",
            )

    def test_governance_invariants_parity(self):
        en_readme = (self.root / "README.md").read_text(encoding="utf-8")
        de_readme = (self.root / "README_de.md").read_text(encoding="utf-8")
        marketing_log = (self.root / "MARKETING-LOG.txt").read_text(encoding="utf-8")
        llms_txt = (self.root / "llms.txt").read_text(encoding="utf-8")

        invariants = [
            "INV-LOCAL-01",
            "INV-SEC-02",
            "INV-SLOT-03",
            "INV-MSG-04",
            "INV-FAIL-05",
            "INV-MERGE-06",
            "INV-GATE-07",
            "INV-PEER-08",
            "INV-LIC-09",
            "INV-SLA-10",
        ]
        for inv in invariants:
            self.assertIn(inv, en_readme, f"{inv} missing in README.md")
            self.assertIn(inv, de_readme, f"{inv} missing in README_de.md")
            self.assertIn(inv, marketing_log, f"{inv} missing in MARKETING-LOG.txt")
        self.assertIn("INV-LOCAL-01", llms_txt)
        self.assertIn("INV-SLA-10", llms_txt)

    def test_third_party_licenses_contract(self):
        tpl_path = self.root / "THIRD_PARTY_LICENSES.md"
        self.assertTrue(tpl_path.exists(), "THIRD_PARTY_LICENSES.md must exist")
        text = tpl_path.read_text(encoding="utf-8")

        self.assertIn("Runtime Dependency Matrix", text)
        self.assertIn("Optional Execution Adapter Dependencies", text)
        self.assertIn("Development & Quality Assurance Tooling", text)
        self.assertIn("100% Permissive", text)
        self.assertIn("zero AGPL", text)
        self.assertIn("RunAsInvoker", text)
        self.assertIn("tomli", text)
        self.assertIn("pytest", text)
        self.assertIn("ruff", text)

    def test_marketing_log_contract(self):
        ml_path = self.root / "MARKETING-LOG.txt"
        self.assertTrue(ml_path.exists(), "MARKETING-LOG.txt must exist")
        text = ml_path.read_text(encoding="utf-8")

        self.assertIn("TARGET PERSONAS", text)
        self.assertIn("HIGH-INTENT SEARCH QUERIES", text)
        self.assertIn("COMPETITIVE DIFFERENTIATION MATRIX", text)
        self.assertIn("GOVERNANCE & RUNTIME INVARIANTS", text)
        self.assertIn("Multi-Device Developers", text)

    def test_pyproject_urls_contract(self):
        pyproject_path = self.root / "pyproject.toml"
        self.assertTrue(pyproject_path.exists(), "pyproject.toml must exist")
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        urls = data.get("project", {}).get("urls", {})

        self.assertIn("Third-Party Licenses", urls)
        self.assertIn("THIRD_PARTY_LICENSES.md", urls["Third-Party Licenses"])
        self.assertIn("Marketing Log", urls)
        self.assertIn("MARKETING-LOG.txt", urls["Marketing Log"])
        self.assertIn("LLM Ready", urls)
        self.assertIn("llms.txt", urls["LLM Ready"])

    def test_ci_timeout_guardrail(self):
        ci_path = self.root / ".github" / "workflows" / "tests.yml"
        self.assertTrue(ci_path.exists(), ".github/workflows/tests.yml must exist")
        ci_content = ci_path.read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 15", ci_content)

    def test_pyproject_llm_ready_contract(self):
        pyproject_path = self.root / "pyproject.toml"
        self.assertTrue(pyproject_path.exists(), "pyproject.toml must exist")
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        urls = data.get("project", {}).get("urls", {})
        self.assertIn("LLM Ready", urls)
        self.assertEqual(urls["LLM Ready"], "https://github.com/ellmos-ai/system-gap-master/blob/main/llms.txt")


if __name__ == "__main__":
    unittest.main()
