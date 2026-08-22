import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from system_gap_master.instance_manager import (
    INSTANCE_STATE,
    InstanceManagerError,
    apply_plan,
    build_plan,
    doctor_yard,
    inventory_yard,
    load_template,
    main,
    retention_plan,
    rollback_operation,
)


class InstanceManagerFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.template = self.root / "template"
        self.yard = self.root / "yard"
        self.state = self.root / "state"
        self.template.mkdir()
        self.yard.mkdir()
        self._write_template()

    def tearDown(self):
        self.temporary.cleanup()

    def _write_template(self):
        (self.template / "README.md").write_text("template v1\n", encoding="utf-8")
        (self.template / "BOOTSTRAP.md").write_text("bootstrap v1\n", encoding="utf-8")
        manifest = {
            "schema": "system-gap.yard-template.v1",
            "template_version": "test-1",
            "directories": [
                {"path": "hosts", "ownership": "instance", "required": True},
                {"path": "messages", "ownership": "instance", "required": True},
                {"path": "_archive", "ownership": "instance", "required": True},
                {"path": "db-transit", "ownership": "tool", "required": False},
            ],
            "files": [
                {"path": "README.md", "mode": "managed"},
                {"path": "BOOTSTRAP.md", "mode": "seed-once"},
            ],
            "protected_patterns": ["hosts/**", "messages/**", "db-transit/**"],
        }
        (self.template / "YARD_TEMPLATE.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def _save_plan(self, plan=None):
        path = self.root / "plan.json"
        path.write_text(
            json.dumps(plan or build_plan(self.yard, self.template)), encoding="utf-8"
        )
        return path

    def _bootstrap(self):
        return apply_plan(self._save_plan(), self.state)


class TemplateValidationTests(InstanceManagerFixture):
    def test_loads_neutral_manifest(self):
        template = load_template(self.template)
        self.assertEqual(template.version, "test-1")
        self.assertEqual(len(template.files), 2)
        self.assertEqual(template.directories[-1]["ownership"], "tool")

    def test_rejects_parent_traversal(self):
        manifest_path = self.template / "YARD_TEMPLATE.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append({"path": "../escape", "mode": "managed"})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(InstanceManagerError):
            load_template(self.template)


class LifecycleTests(InstanceManagerFixture):
    def test_fresh_plan_upgrade_second_plan_and_rollback(self):
        plan = build_plan(self.yard, self.template)
        actions = {item["action"] for item in plan["actions"]}
        self.assertIn("create-directory", actions)
        self.assertIn("create-file", actions)
        self.assertFalse(plan["blockers"])

        applied = apply_plan(self._save_plan(plan), self.state)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual((self.yard / "README.md").read_text(encoding="utf-8"), "template v1\n")
        self.assertTrue((self.yard / INSTANCE_STATE).is_file())
        self.assertFalse((self.yard / "db-transit").exists())

        second = build_plan(self.yard, self.template)
        mutating = {
            "create-directory",
            "create-file",
            "update-file",
        }
        self.assertFalse(any(item["action"] in mutating for item in second["actions"]))
        self.assertFalse(second["blockers"])

        rolled_back = rollback_operation(
            applied["operation_id"], self.yard, self.state
        )
        self.assertEqual(rolled_back["status"], "rolled-back")
        self.assertFalse((self.yard / "README.md").exists())
        self.assertFalse((self.yard / INSTANCE_STATE).exists())
        self.assertFalse((self.yard / "hosts").exists())

    def test_managed_file_updates_only_from_recorded_hash(self):
        first = self._bootstrap()
        self.assertEqual(first["status"], "applied")
        (self.template / "README.md").write_text("template v2\n", encoding="utf-8")

        plan = build_plan(self.yard, self.template)
        update = [item for item in plan["actions"] if item["action"] == "update-file"]
        self.assertEqual([item["path"] for item in update], ["README.md"])
        second = apply_plan(self._save_plan(plan), self.state)
        self.assertEqual((self.yard / "README.md").read_text(encoding="utf-8"), "template v2\n")

        rollback_operation(second["operation_id"], self.yard, self.state)
        self.assertEqual((self.yard / "README.md").read_text(encoding="utf-8"), "template v1\n")

    def test_modified_managed_file_blocks_upgrade(self):
        self._bootstrap()
        (self.yard / "README.md").write_text("local edit\n", encoding="utf-8")
        (self.template / "README.md").write_text("template v2\n", encoding="utf-8")
        plan = build_plan(self.yard, self.template)
        self.assertEqual(plan["blockers"][0]["reason"], "untracked-or-modified-managed-file")
        with self.assertRaises(InstanceManagerError):
            apply_plan(self._save_plan(plan), self.state)

    def test_seed_once_file_is_preserved(self):
        self._bootstrap()
        (self.yard / "BOOTSTRAP.md").write_text("instance-specific\n", encoding="utf-8")
        (self.template / "BOOTSTRAP.md").write_text("bootstrap v2\n", encoding="utf-8")
        plan = build_plan(self.yard, self.template)
        action = next(item for item in plan["actions"] if item["path"] == "BOOTSTRAP.md")
        self.assertEqual(action["action"], "preserve-instance-file")
        self.assertFalse(plan["blockers"])

    def test_target_change_after_plan_fails_closed(self):
        plan_path = self._save_plan()
        (self.yard / "README.md").write_text("late writer\n", encoding="utf-8")
        with self.assertRaises(InstanceManagerError):
            apply_plan(plan_path, self.state)
        self.assertEqual((self.yard / "README.md").read_text(encoding="utf-8"), "late writer\n")

    def test_tampered_plan_fails_closed(self):
        plan = build_plan(self.yard, self.template)
        plan["yard_root"] = str(self.root / "other")
        with self.assertRaises(InstanceManagerError):
            apply_plan(self._save_plan(plan), self.state)

    def test_changed_post_upgrade_file_blocks_rollback(self):
        applied = self._bootstrap()
        (self.yard / "README.md").write_text("late writer\n", encoding="utf-8")
        with self.assertRaises(InstanceManagerError):
            rollback_operation(applied["operation_id"], self.yard, self.state)
        self.assertEqual((self.yard / "README.md").read_text(encoding="utf-8"), "late writer\n")

    def test_state_dir_inside_yard_is_forbidden(self):
        with self.assertRaises(InstanceManagerError):
            apply_plan(self._save_plan(), self.yard / "state")


class ReadOnlyAnalysisTests(InstanceManagerFixture):
    def test_doctor_reports_legacy_transit_without_mutation(self):
        (self.yard / "_transit").mkdir()
        before = sorted(path.name for path in self.yard.iterdir())
        result = doctor_yard(self.yard, self.template)
        after = sorted(path.name for path in self.yard.iterdir())
        self.assertEqual(before, after)
        self.assertEqual(result["mutation_performed"], False)
        self.assertIn(
            "legacy-transit-review-required",
            {item["kind"] for item in result["findings"]},
        )

    def test_inventory_marks_unknown_root_as_unmanaged(self):
        (self.yard / "private-instance-data").mkdir()
        result = inventory_yard(self.yard, self.template)
        item = next(entry for entry in result["items"] if entry["path"] == "private-instance-data")
        self.assertEqual(item["ownership"], "unmanaged")

    def test_retention_plan_never_authorizes_mutation(self):
        host = self.yard / "hosts" / "HOST-A"
        host.mkdir(parents=True)
        legacy = self.yard / "legacy-laptop"
        legacy.mkdir()
        for index in range(101):
            file = host / f"receipt-{index:03}.md"
            file.write_text("x", encoding="utf-8")
            old = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp()
            os.utime(file, (old, old))
        legacy_receipt = legacy / "old-receipt.md"
        legacy_receipt.write_text("x", encoding="utf-8")
        os.utime(legacy_receipt, (old, old))
        result = retention_plan(
            self.yard,
            now=dt.datetime(2026, 8, 22, tzinfo=dt.timezone.utc),
            legacy_host_slots=["legacy-laptop"],
        )
        self.assertEqual(result["policy"]["automatic_mutation"], False)
        self.assertTrue(result["candidates"])
        self.assertTrue(all(not item["safe_to_apply"] for item in result["candidates"]))
        self.assertIn(
            "legacy-laptop/old-receipt.md",
            {item["path"] for item in result["candidates"]},
        )

    def test_cli_doctor_is_json_and_read_only(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "doctor",
                    "--yard-root",
                    str(self.yard),
                    "--template-root",
                    str(self.template),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue())["schema"], "system-gap.yard-doctor.v1")


class RepositoryTemplateSmokeTests(unittest.TestCase):
    def test_real_template_bootstrap_is_idempotent_and_rolls_back_cleanly(self):
        repository_root = Path(__file__).resolve().parent.parent
        template = repository_root / "template"
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            yard = root / "yard"
            state = root / "state"
            yard.mkdir()
            plan_path = root / "plan.json"
            plan_path.write_text(
                json.dumps(build_plan(yard, template)), encoding="utf-8"
            )

            applied = apply_plan(plan_path, state)
            shared_state = json.loads(
                (yard / INSTANCE_STATE).read_text(encoding="utf-8")
            )
            self.assertTrue(shared_state["instance_id"])
            self.assertNotIn(str(yard), json.dumps(shared_state))
            second = build_plan(yard, template)
            mutating = {"create-directory", "create-file", "update-file"}
            self.assertFalse(
                any(item["action"] in mutating for item in second["actions"])
            )

            rollback_operation(applied["operation_id"], yard, state)
            self.assertEqual(list(yard.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
