import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import config_snapshot


class TestConfigSnapshot(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.home = self.temp_dir / "home"
        self.home.mkdir()
        self.state = self.temp_dir / "_config-state"
        self.settings = self.home / ".agent-one" / "settings.json"
        self.settings.parent.mkdir()
        self.settings.write_text(
            json.dumps(
                {
                    "model": "small",
                    "language": "de",
                    "profiles": {"default": {}, "review": {}},
                    "api_key": "must-not-appear",
                    "home_path": str(self.home / "cache"),
                    "shape": {"private": "omitted"},
                    "unlisted_secret": "never-read",
                }
            ),
            encoding="utf-8",
        )
        self.config = {
            "_schema": config_snapshot.PROVIDER_CONFIG_SCHEMA,
            "providers": {
                "agent-one": {
                    "files": [
                        {
                            "id": "settings",
                            "path": "${HOME}/.agent-one/settings.json",
                            "allowlist": {
                                "model": "model",
                                "profile_count": {"path": "profiles", "mode": "count"},
                                "api_key": "api_key",
                                "home_path": "home_path",
                                "shape": "shape",
                            },
                        }
                    ],
                    "environment": {"token": "AGENT_TOKEN"},
                }
            },
        }

    def tearDown(self):
        for path in sorted(self.temp_dir.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.temp_dir.rmdir()

    def test_allowlist_redacts_secrets_and_normalises_home(self):
        with patch.dict(os.environ, {"AGENT_TOKEN": "token-value"}, clear=False):
            snapshot = config_snapshot.build_snapshot(self.config, home=self.home, slot="alpha")
        provider = snapshot["providers"]["agent-one"]
        settings = provider["settings"]
        self.assertEqual(settings["model"], "small")
        self.assertEqual(settings["profile_count"], 2)
        self.assertEqual(settings["api_key"], "<redacted>")
        self.assertEqual(settings["home_path"], "<HOME>/cache")
        self.assertEqual(settings["shape"], "<dict:1>")
        self.assertNotIn("unlisted_secret", settings)
        self.assertEqual(provider["env"]["token"], "<redacted>")
        self.assertEqual(snapshot["home"], "<HOME>")

    def test_check_mode_is_read_only_and_cli_writes_afterwards(self):
        config_path = self.temp_dir / "providers.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        with patch.dict(os.environ, {"AGENT_TOKEN": "token-value"}, clear=False):
            self.assertEqual(
                config_snapshot.main(
                    [
                        "all",
                        "--state-dir",
                        str(self.state),
                        "--config",
                        str(config_path),
                        "--home",
                        str(self.home),
                        "--slot",
                        "check",
                        "--check",
                    ]
                ),
                0,
            )
        self.assertFalse(self.state.exists())
        self.assertEqual(
            config_snapshot.main(
                [
                    "snapshot",
                    "--state-dir",
                    str(self.state),
                    "--config",
                    str(config_path),
                    "--home",
                    str(self.home),
                    "--slot",
                    "alpha",
                ]
            ),
            0,
        )
        self.assertTrue((self.state / "snapshots" / "alpha.json").is_file())

    def test_toml_provider_table_is_allowlisted(self):
        toml_path = self.home / ".agent-two.toml"
        toml_path.write_text(
            'model = "toml-model"\nreasoning_effort = "high"\nsecret = "omit"\n',
            encoding="utf-8",
        )
        config = {
            "providers": {
                "agent-two": {
                    "files": [
                        {
                            "path": str(toml_path),
                            "format": "toml",
                            "allowlist": ["model", "reasoning_effort"],
                        }
                    ]
                }
            }
        }
        snapshot = config_snapshot.build_snapshot(config, home=self.home, slot="toml")
        settings = snapshot["providers"]["agent-two"]["settings"]
        self.assertEqual(settings, {"model": "toml-model", "reasoning_effort": "high"})

    def test_explicit_empty_provider_table_does_not_fall_back_to_default(self):
        snapshot = config_snapshot.build_snapshot({}, home=self.home, slot="empty")
        self.assertEqual(snapshot["providers"], {})

    def test_report_does_not_need_to_parse_provider_table(self):
        self.state.mkdir(parents=True)
        (self.state / "providers.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(config_snapshot.main(["report", "--state-dir", str(self.state)]), 0)
        self.assertTrue((self.state / "CONFIG-STATE.md").is_file())

    def test_report_marks_unexplained_and_documented_differences(self):
        first = config_snapshot.build_snapshot(self.config, home=self.home, slot="alpha", generated="2026-08-08T10:00:00+02:00")
        self.settings.write_text(
            json.dumps(
                {
                    "model": "large",
                    "language": "de",
                    "profiles": {"default": {}, "review": {}},
                    "api_key": "must-not-appear",
                    "home_path": str(self.home / "cache"),
                    "shape": {"private": "omitted"},
                }
            ),
            encoding="utf-8",
        )
        second = config_snapshot.build_snapshot(self.config, home=self.home, slot="beta", generated="2026-08-08T10:01:00+02:00")
        report = config_snapshot.build_report({"alpha": first, "beta": second}, documented=set())
        self.assertIn("| `model` |", report)
        self.assertIn("| ! |", report)
        self.assertIn("`agent-one.model`", report)

        documented = config_snapshot.build_report({"alpha": first, "beta": second}, documented={"agent-one.model"})
        self.assertIn("| ~ |", documented)
        self.assertNotIn("These differences are not documented", documented)

    def test_documented_keys_ignore_fenced_examples_and_one_snapshot_is_not_all_clear(self):
        deviations = self.state / "DEVIATIONS.md"
        deviations.parent.mkdir()
        deviations.write_text(
            """```markdown\n### `agent-one.model`\n```\n### `agent-one.language`\nReason\n""",
            encoding="utf-8",
        )
        self.assertEqual(config_snapshot.documented_keys(deviations), {"agent-one.language"})
        snapshot = config_snapshot.build_snapshot(self.config, home=self.home, slot="alpha")
        report = config_snapshot.build_report({"alpha": snapshot}, documented=set())
        self.assertIn("No comparison possible yet", report)

    def test_malformed_and_foreign_snapshots_are_ignored(self):
        directory = self.state / "snapshots"
        directory.mkdir(parents=True)
        (directory / "broken.json").write_text("not json", encoding="utf-8")
        (directory / "foreign.json").write_text(json.dumps({"_schema": "other"}), encoding="utf-8")
        valid = config_snapshot.build_snapshot(self.config, home=self.home, slot="alpha")
        (directory / "alpha.json").write_text(json.dumps(valid), encoding="utf-8")
        loaded = config_snapshot.load_snapshots(self.state)
        self.assertEqual(set(loaded), {"alpha"})

    def test_script_has_no_provider_specific_path_defaults(self):
        source = Path(config_snapshot.__file__).read_text(encoding="utf-8").lower()
        for provider_name in ("anthropic", "openai", "google"):
            self.assertNotIn(provider_name, source)


if __name__ == "__main__":
    unittest.main()
