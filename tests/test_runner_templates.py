import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


class TestRunnerTemplates(unittest.TestCase):
    def test_desktop_observer_and_owner_commands_are_separate(self):
        template = json.loads(
            (
                REPOSITORY
                / "template"
                / "runners"
                / "desktop-agent"
                / "conflict-copy-reconciler.task.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(template["default_mode"], "observer")
        self.assertIn("plan", template["commands"]["observer"])
        self.assertNotIn("reconcile", template["commands"]["observer"])
        self.assertIn("reconcile", template["commands"]["mutating-owner"])
        self.assertEqual(template["ownership"]["max_mutating_owners"], 1)
        self.assertFalse(template["registration"]["raw_private_registry_mutation"])

    def test_public_config_defaults_to_observer_and_placeholders(self):
        config = json.loads(
            (
                REPOSITORY
                / "examples"
                / "conflict-reconciler.config.example.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(config["mode"], "observer")
        self.assertEqual(config["roots"][0]["path"], "${SYNC_ROOT}")
        self.assertTrue(config["receipt_salt"].startswith("${"))

    def test_macos_launchagent_is_user_neutral_and_observer_first(self):
        plist_path = (
            REPOSITORY
            / "template"
            / "runners"
            / "macos"
            / "org.example.system-gap-master.conflict-copy-reconciler.plist"
        )
        root = ET.parse(plist_path).getroot()
        text = plist_path.read_text(encoding="utf-8")
        self.assertEqual(root.tag, "plist")
        self.assertIn("<string>observer</string>", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/Volumes/", text)
        self.assertIn("__HOST_LOCAL_CONFIG__", text)

    def test_macos_runner_keeps_observer_read_only(self):
        runner = (
            REPOSITORY
            / "template"
            / "runners"
            / "macos"
            / "run-conflict-copy-reconciler.sh"
        ).read_text(encoding="utf-8")
        observer_block = runner.split("mutating-owner)", 1)[0]
        self.assertIn('RUN_MODE="${SYSTEM_GAP_RECONCILER_MODE:-observer}"', runner)
        self.assertIn(" plan \\", observer_block)
        self.assertNotIn(" reconcile \\", observer_block)
        self.assertNotIn("/Users/", runner)
        self.assertNotIn("/Volumes/", runner)


if __name__ == "__main__":
    unittest.main()
