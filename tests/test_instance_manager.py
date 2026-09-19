import contextlib
import datetime as dt
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_rejects_managed_file_in_protected_zone_without_exception(self):
        manifest_path = self.template / "YARD_TEMPLATE.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        (self.template / "hosts" / "managed.txt").parent.mkdir()
        (self.template / "hosts" / "managed.txt").write_text("unsafe\n", encoding="utf-8")
        manifest["files"].append({"path": "hosts/managed.txt", "mode": "managed"})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(InstanceManagerError, "explicit exception"):
            load_template(self.template)

    def test_template_binding_changes_when_declared_mode_changes(self):
        before = load_template(self.template).manifest_sha256
        manifest_path = self.template / "YARD_TEMPLATE.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["posix_mode"] = "0755"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self.assertNotEqual(load_template(self.template).manifest_sha256, before)


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

    def test_tampered_operation_manifest_fails_closed_on_rollback(self):
        applied = self._bootstrap()
        operation_path = self.state / "operations" / applied["operation_id"] / "operation.json"
        operation = json.loads(operation_path.read_text(encoding="utf-8"))
        # Add a field without recomputing integrity_sha256 -- the same shape
        # as a manipulated or corrupted manifest. Deliberately not one of the
        # fields checked separately (schema/operation_id/status/yard_root),
        # so only the digest check itself can catch this.
        operation["tampered"] = True
        operation_path.write_text(json.dumps(operation), encoding="utf-8")
        with self.assertRaisesRegex(InstanceManagerError, "integrity check failed"):
            rollback_operation(applied["operation_id"], self.yard, self.state)

    def test_missing_backup_blocks_rollback(self):
        self._bootstrap()
        (self.template / "README.md").write_text("template v2\n", encoding="utf-8")
        second = apply_plan(self._save_plan(build_plan(self.yard, self.template)), self.state)
        backup = self.state / "operations" / second["operation_id"] / "backups" / "README.md"
        self.assertTrue(backup.is_file())
        backup.unlink()
        with self.assertRaisesRegex(InstanceManagerError, "backup is missing or changed"):
            rollback_operation(second["operation_id"], self.yard, self.state)
        self.assertEqual((self.yard / "README.md").read_text(encoding="utf-8"), "template v2\n")

    def test_link_boundary_appearing_after_planning_blocks_apply(self):
        plan_path = self._save_plan()
        external = self.root / "external-hosts"
        external.mkdir()
        junction = self.yard / "hosts"
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            junction.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(InstanceManagerError, "link boundary appeared after planning"):
            apply_plan(plan_path, self.state)

    def test_state_dir_inside_yard_is_forbidden(self):
        with self.assertRaises(InstanceManagerError):
            apply_plan(self._save_plan(), self.yard / "state")

    def test_second_apply_is_a_true_noop(self):
        self._bootstrap()
        second_plan = self._save_plan(build_plan(self.yard, self.template))
        state_before = (self.yard / INSTANCE_STATE).read_bytes()
        operations_before = sorted((self.state / "operations").iterdir())

        result = apply_plan(second_plan, self.state)

        self.assertEqual(result["status"], "noop")
        self.assertIsNone(result["operation_id"])
        self.assertEqual((self.yard / INSTANCE_STATE).read_bytes(), state_before)
        self.assertEqual(sorted((self.state / "operations").iterdir()), operations_before)

    def test_late_foreign_first_run_state_is_preserved(self):
        plan_path = self._save_plan()
        foreign_state = b'{"owner":"foreign"}\n'

        from system_gap_master import instance_manager

        original = instance_manager._build_instance_state

        def introduce_foreign_state(*args, **kwargs):
            (self.yard / INSTANCE_STATE).write_bytes(foreign_state)
            return original(*args, **kwargs)

        with mock.patch.object(
            instance_manager,
            "_build_instance_state",
            side_effect=introduce_foreign_state,
        ):
            with self.assertRaises(InstanceManagerError):
                apply_plan(plan_path, self.state)

        self.assertEqual((self.yard / INSTANCE_STATE).read_bytes(), foreign_state)
        self.assertFalse((self.yard / "README.md").exists())

    def test_write_ahead_manifest_exists_before_first_yard_file_write(self):
        plan_path = self._save_plan()
        observed_statuses = []

        from system_gap_master import instance_manager

        original = instance_manager._copy_verified

        def inspect_journal(*args, **kwargs):
            manifests = list((self.state / "operations").glob("*/operation.json"))
            self.assertEqual(len(manifests), 1)
            observed_statuses.append(json.loads(manifests[0].read_text(encoding="utf-8"))["status"])
            return original(*args, **kwargs)

        with mock.patch.object(instance_manager, "_copy_verified", side_effect=inspect_journal):
            apply_plan(plan_path, self.state)

        self.assertTrue(observed_statuses)
        self.assertEqual(set(observed_statuses), {"applying"})

    def test_rollback_resumes_after_file_restored_before_journal_update(self):
        first = self._bootstrap()
        (self.template / "README.md").write_text("template v2\n", encoding="utf-8")
        second = apply_plan(self._save_plan(build_plan(self.yard, self.template)), self.state)

        from system_gap_master import instance_manager

        original = instance_manager._atomic_write
        failed_once = False

        def fail_after_restore(path, data):
            nonlocal failed_once
            original(path, data)
            if (
                path.resolve(strict=False)
                == (self.yard / "README.md").resolve(strict=False)
                and not failed_once
            ):
                failed_once = True
                raise OSError("simulated power loss after restore")

        with mock.patch.object(instance_manager, "_atomic_write", side_effect=fail_after_restore):
            with self.assertRaises(OSError):
                rollback_operation(second["operation_id"], self.yard, self.state)

        operation_path = self.state / "operations" / second["operation_id"] / "operation.json"
        self.assertEqual(json.loads(operation_path.read_text(encoding="utf-8"))["status"], "rolling-back")
        resumed = rollback_operation(second["operation_id"], self.yard, self.state)
        self.assertEqual(resumed["status"], "rolled-back")
        self.assertEqual((self.yard / "README.md").read_text(encoding="utf-8"), "template v1\n")
        self.assertEqual(first["operation_id"], json.loads((self.yard / INSTANCE_STATE).read_text())["last_operation_id"])

    @unittest.skipIf(os.name == "nt", "POSIX executable modes are not portable to Windows")
    def test_update_and_rollback_preserve_posix_modes(self):
        self._bootstrap()
        self.assertEqual((self.yard / "README.md").stat().st_mode & 0o777, 0o644)

        manifest_path = self.template / "YARD_TEMPLATE.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["posix_mode"] = "0755"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        second = apply_plan(self._save_plan(build_plan(self.yard, self.template)), self.state)
        self.assertEqual((self.yard / "README.md").stat().st_mode & 0o777, 0o755)

        rollback_operation(second["operation_id"], self.yard, self.state)
        self.assertEqual((self.yard / "README.md").stat().st_mode & 0o777, 0o644)

    @unittest.skipIf(os.name == "nt", "POSIX executable modes are not portable to Windows")
    def test_chmod_only_managed_drift_blocks_upgrade(self):
        self._bootstrap()
        target = self.yard / "README.md"
        target.chmod(0o600)

        plan = build_plan(self.yard, self.template)

        self.assertEqual(
            plan["blockers"][0]["reason"],
            "untracked-or-modified-managed-mode",
        )


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

    def test_inventory_classifies_declared_root_files_and_counts_top_level_only(self):
        (self.yard / "README.md").write_text("template v1\n", encoding="utf-8")
        (self.yard / "BOOTSTRAP.md").write_text("instance copy\n", encoding="utf-8")
        result = inventory_yard(self.yard, self.template)
        items = {entry["path"]: entry for entry in result["items"]}

        self.assertEqual(items["README.md"]["ownership"], "repo")
        self.assertEqual(items["README.md"]["mode"], "managed")
        self.assertEqual(items["BOOTSTRAP.md"]["ownership"], "instance")
        self.assertEqual(items["BOOTSTRAP.md"]["mode"], "seed-once")
        self.assertEqual(result["summary"]["declared_top_level_items"], 6)

    def test_inventory_classifies_generated_instance_state_as_tool_owned(self):
        self._bootstrap()

        result = inventory_yard(self.yard, self.template)
        items = {entry["path"]: entry for entry in result["items"]}

        self.assertEqual(items[INSTANCE_STATE]["ownership"], "tool")
        self.assertEqual(items[INSTANCE_STATE]["state_role"], "instance-manager")
        self.assertEqual(result["summary"]["generated_top_level_items"], 1)
        self.assertNotIn(
            INSTANCE_STATE,
            {item["path"] for item in result["items"] if item["ownership"] == "unmanaged"},
        )

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

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_retention_does_not_traverse_a_host_junction(self):
        hosts = self.yard / "hosts"
        external = self.root / "external-host"
        junction = hosts / "HOST-JUNCTION"
        hosts.mkdir()
        external.mkdir()
        (external / "old-secret.md").write_text("outside\n", encoding="utf-8")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
            check=True,
            capture_output=True,
            text=True,
        )

        result = retention_plan(self.yard, host_file_days=0)

        self.assertNotIn(
            "hosts/HOST-JUNCTION/old-secret.md",
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

    def test_cli_plan_output_inside_yard_is_rejected(self):
        target = self.yard / "plan.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "plan",
                    "--yard-root",
                    str(self.yard),
                    "--template-root",
                    str(self.template),
                    "--output",
                    str(target),
                ]
            )
        self.assertEqual(code, 2)
        self.assertFalse(target.exists())
        self.assertIn("must stay outside yard_root", json.loads(stderr.getvalue())["error"])

    def test_cli_plan_output_outside_yard_still_works(self):
        target = self.root / "review" / "plan.json"
        code = main(
            [
                "plan",
                "--yard-root",
                str(self.yard),
                "--template-root",
                str(self.template),
                "--output",
                str(target),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())


class RepositoryTemplateSmokeTests(unittest.TestCase):
    def test_real_template_bootstrap_is_idempotent_and_rolls_back_cleanly(self):
        repository_root = Path(__file__).resolve().parent.parent
        template = repository_root / "system_gap_master" / "yard_template"
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

    def test_real_template_seed_once_readme_files(self):
        repository_root = Path(__file__).resolve().parent.parent
        template = repository_root / "system_gap_master" / "yard_template"
        loaded = load_template(template)
        file_modes = {entry["path"]: entry["mode"] for entry in loaded.files}
        self.assertEqual(file_modes["_config-state/README.md"], "seed-once")
        self.assertEqual(file_modes["agents/README.md"], "seed-once")
        self.assertNotIn("_config-state/README.md", loaded.managed_path_exceptions)
        self.assertNotIn("agents/README.md", loaded.managed_path_exceptions)

        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            yard = root / "yard"
            yard.mkdir()
            cfg_dir = yard / "_config-state"
            cfg_dir.mkdir()
            (cfg_dir / "README.md").write_text("# Custom local config doc\n", encoding="utf-8")
            agents_dir = yard / "agents"
            agents_dir.mkdir()
            (agents_dir / "README.md").write_text("# Custom local agents doc\n", encoding="utf-8")

            report = doctor_yard(yard, template)
            self.assertEqual(report["blockers"], [])
            plan = build_plan(yard, template)
            preserved = {
                action["path"]: action["action"]
                for action in plan["actions"]
                if action["path"] in {"_config-state/README.md", "agents/README.md"}
            }
            self.assertEqual(
                preserved,
                {
                    "_config-state/README.md": "preserve-instance-file",
                    "agents/README.md": "preserve-instance-file",
                },
            )


if __name__ == "__main__":
    unittest.main()
