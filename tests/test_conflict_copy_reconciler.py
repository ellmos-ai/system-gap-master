import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from system_gap_master.conflict_copy_reconciler import (
    CONFIG_SCHEMA,
    ConflictCopyReconciler,
    LeaseBusy,
    ReconcilerError,
    RootLease,
    main,
    run_canary,
)


class ReconcilerFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "yard"
        self.state = self.base / "state"
        self.root.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content, binary=False):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def config(
        self,
        conflict,
        canonical,
        *,
        adapter="auto",
        cloud_ready=True,
        known_hosts=None,
        base=None,
    ):
        mapping = {
            "conflict": conflict,
            "canonical": canonical,
            "authority": {
                "kind": "manifest",
                "reference": "synthetic-test-manifest",
            },
            "adapter": adapter,
        }
        if base:
            mapping["base"] = base
        return {
            "schema": CONFIG_SCHEMA,
            "actor": "test-adapter",
            "mode": "mutating-owner",
            "state_dir": str(self.state),
            "receipt_salt": "test-salt-0123456789",
            "roots": [
                {
                    "id": "test-root",
                    "path": str(self.root),
                    "archive_dir": ".archive",
                    "cloud_ready": cloud_ready,
                    "known_hosts": known_hosts or [],
                    "canonical_mappings": [mapping],
                }
            ],
        }

    def reconciler(self, *args, **kwargs):
        return ConflictCopyReconciler(self.config(*args, **kwargs))


class TestSafeMergeClasses(ReconcilerFixture):
    def test_exact_copy_apply_verify_and_rollback(self):
        self.write("notes.md", "same\n")
        self.write("notes (host conflicted copy).md", "same\n")
        reconciler = self.reconciler(
            "notes (host conflicted copy).md", "notes.md"
        )
        plan = reconciler.plan()
        self.assertEqual(plan["items"][0]["merge_class"], "exact")
        applied = reconciler.apply(plan)
        self.assertEqual(applied["applied"], 1)
        self.assertFalse((self.root / "notes (host conflicted copy).md").exists())
        verified = reconciler.verify(applied["operation_id"])
        self.assertEqual(verified["status"], "verified")
        rolled_back = reconciler.rollback(applied["operation_id"])
        self.assertEqual(rolled_back["status"], "rolled-back")
        self.assertEqual((self.root / "notes.md").read_text(), "same\n")
        self.assertTrue((self.root / "notes (host conflicted copy).md").is_file())

    def test_append_only_superset(self):
        self.write("log.md", "alpha\n")
        self.write("log (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            "log (conflict).md", "log.md", adapter="append-only-text"
        )
        result = reconciler.reconcile()
        self.assertEqual(result["applied"], 1)
        self.assertEqual((self.root / "log.md").read_text(), "alpha\nbeta\n")

    def test_three_way_non_overlapping(self):
        base_path = self.write("base.md", "one\ntwo\nthree\n")
        canonical = self.write("story.md", "ONE\ntwo\nthree\n")
        conflict = self.write(
            "story (host conflicted copy).md", "one\ntwo\nTHREE\n"
        )
        base = {
            "path": base_path.name,
            "sha256": __import__("hashlib").sha256(base_path.read_bytes()).hexdigest(),
        }
        reconciler = self.reconciler(
            conflict.name,
            canonical.name,
            adapter="three-way-text",
            base=base,
        )
        plan = reconciler.plan()
        self.assertEqual(plan["items"][0]["merge_class"], "three-way-text")
        reconciler.apply(plan)
        self.assertEqual(canonical.read_text(), "ONE\ntwo\nTHREE\n")

    def test_three_way_overlap_is_blocked(self):
        base_path = self.write("base.md", "same\n")
        canonical = self.write("story.md", "ours\n")
        conflict = self.write("story (conflict).md", "theirs\n")
        base = {
            "path": base_path.name,
            "sha256": __import__("hashlib").sha256(base_path.read_bytes()).hexdigest(),
        }
        item = self.reconciler(
            conflict.name,
            canonical.name,
            adapter="three-way-text",
            base=base,
        ).plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertTrue(any(value.startswith("merge-validation:") for value in item["blockers"]))

    def test_json_object_disjoint_merge(self):
        canonical = self.write("state.json", '{"left": 1}\n')
        conflict = self.write("state (conflict).json", '{"right": 2}\n')
        reconciler = self.reconciler(
            conflict.name, canonical.name, adapter="json-object"
        )
        reconciler.reconcile()
        self.assertEqual(json.loads(canonical.read_text()), {"left": 1, "right": 2})

    def test_json_semantic_collision_is_blocked(self):
        self.write("state.json", '{"same": 1}\n')
        self.write("state (conflict).json", '{"same": 2}\n')
        item = self.reconciler(
            "state (conflict).json", "state.json", adapter="json-object"
        ).plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertTrue(any(value.startswith("merge-validation:") for value in item["blockers"]))


class TestFailClosedGates(ReconcilerFixture):
    def assert_blocked(self, reconciler, expected):
        item = reconciler.plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertIn(expected, item["blockers"])

    def test_binary_is_blocked(self):
        self.write("data.txt", b"\0abc", binary=True)
        self.write("data (conflict).txt", b"\0abc", binary=True)
        self.assert_blocked(
            self.reconciler("data (conflict).txt", "data.txt"),
            "binary-content",
        )

    def test_database_is_blocked(self):
        self.write("state.db", b"SQLite format 3\0", binary=True)
        self.write("state (conflict).db", b"SQLite format 3\0", binary=True)
        self.assert_blocked(
            self.reconciler("state (conflict).db", "state.db"),
            "binary-database-or-archive",
        )

    def test_secret_path_is_blocked(self):
        self.write(".env", "SAFE_NAME=value\n")
        self.write(".env (conflict)", "SAFE_NAME=value\n")
        self.assert_blocked(
            self.reconciler(".env (conflict)", ".env"),
            "secret-path",
        )

    def test_secret_content_is_blocked(self):
        secret_shaped = "api" + "_key=" + "abcdefghijk\n"
        self.write("config.txt", secret_shaped)
        self.write("config (conflict).txt", secret_shaped)
        self.assert_blocked(
            self.reconciler("config (conflict).txt", "config.txt"),
            "secret-content",
        )

    def test_lock_is_blocked(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        self.write("LOCK.writer.txt", "owner: another\n")
        item = self.reconciler(
            "notes (conflict).md", "notes.md"
        ).plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertTrue(any(value.startswith("active-lock:") for value in item["blockers"]))

    def test_cloud_attestation_is_required(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        self.assert_blocked(
            self.reconciler(
                "notes (conflict).md", "notes.md", cloud_ready=False
            ),
            "cloud-readiness-not-attested",
        )

    def test_file_size_limit_is_fail_closed(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        config = self.config("notes (conflict).md", "notes.md")
        config["max_file_bytes"] = 4
        item = ConflictCopyReconciler(config).plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertIn("file-size-limit", item["blockers"])

    def test_unknown_candidate_has_no_authority(self):
        self.write("known.md", "same\n")
        self.write("known (conflict).md", "same\n")
        self.write("unknown (conflict).md", "other\n")
        items = self.reconciler(
            "known (conflict).md", "known.md"
        ).plan()["items"]
        unknown = next(item for item in items if item["conflict"].startswith("unknown"))
        self.assertEqual(unknown["blockers"], ["canonical-authority-missing"])

    def test_path_escape_rejected(self):
        with self.assertRaises(ReconcilerError):
            ConflictCopyReconciler(
                self.config("../escape.txt", "safe.txt")
            )

    def test_windows_ads_and_reserved_paths_are_rejected_portably(self):
        with self.assertRaises(ReconcilerError):
            ConflictCopyReconciler(
                self.config("notes.md:stream", "safe.txt")
            )
        with self.assertRaises(ReconcilerError):
            ConflictCopyReconciler(
                self.config("CON.txt", "safe.txt")
            )

    def test_state_directory_inside_root_is_rejected(self):
        config = self.config("copy (conflict).md", "copy.md")
        config["state_dir"] = str(self.root / ".state")
        with self.assertRaises(ReconcilerError):
            ConflictCopyReconciler(config)

    def test_git_dirty_file_is_blocked(self):
        if not shutil_which("git"):
            self.skipTest("git unavailable")
        self.write("notes.md", "old\n")
        self.write("notes-HOST.md", "old\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "notes.md", "notes-HOST.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        (self.root / "notes.md").write_text("dirty\n", encoding="utf-8")
        item = self.reconciler(
            "notes-HOST.md",
            "notes.md",
            known_hosts=["HOST"],
        ).plan()["items"][0]
        self.assertIn("foreign-dirty-work", item["blockers"])


class TestSafetyLifecycle(ReconcilerFixture):
    def test_observer_mode_cannot_mutate(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        config = self.config("notes (conflict).md", "notes.md")
        config["mode"] = "observer"
        reconciler = ConflictCopyReconciler(config)
        with self.assertRaisesRegex(ReconcilerError, "observer mode"):
            reconciler.apply(reconciler.plan())

    def test_plan_tampering_is_rejected(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        reconciler = self.reconciler("notes (conflict).md", "notes.md")
        plan = reconciler.plan()
        plan["items"][0]["canonical"] = "other.md"
        with self.assertRaisesRegex(ReconcilerError, "integrity"):
            reconciler.apply(plan)

    def test_forged_scan_cannot_be_signed_as_a_plan(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        reconciler = self.reconciler("notes (conflict).md", "notes.md")
        scan = reconciler.scan()
        scan["candidates"][0]["status"] = "blocked"
        with self.assertRaisesRegex(ReconcilerError, "fresh readback"):
            reconciler.plan(scan)

    def test_manifest_tampering_blocks_rollback(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        reconciler = self.reconciler("notes (conflict).md", "notes.md")
        applied = reconciler.reconcile()
        manifest_path = (
            self.state / "operations" / f"{applied['operation_id']}.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["records"][0]["canonical"] = "other.md"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ReconcilerError, "integrity"):
            reconciler.rollback(applied["operation_id"])

    def test_changed_canonical_blocks_rollback_before_any_restore(self):
        canonical = self.write("notes.md", "alpha\n")
        conflict = self.write("notes (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            conflict.name, canonical.name, adapter="append-only-text"
        )
        applied = reconciler.reconcile()
        canonical.write_text("foreign-new-content\n", encoding="utf-8")
        with self.assertRaisesRegex(ReconcilerError, "changed canonical"):
            reconciler.rollback(applied["operation_id"])
        self.assertEqual(canonical.read_text(), "foreign-new-content\n")
        self.assertFalse(conflict.exists())

    def test_changed_conflict_blocks_rollback_before_canonical_restore(self):
        canonical = self.write("notes.md", "alpha\n")
        conflict = self.write("notes (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            conflict.name, canonical.name, adapter="append-only-text"
        )
        applied = reconciler.reconcile()
        conflict.write_text("foreign-new-conflict\n", encoding="utf-8")
        with self.assertRaisesRegex(ReconcilerError, "changed conflict"):
            reconciler.rollback(applied["operation_id"])
        self.assertEqual(canonical.read_text(), "alpha\nbeta\n")
        self.assertEqual(conflict.read_text(), "foreign-new-conflict\n")

    def test_tampered_backup_blocks_rollback(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        reconciler = self.reconciler("notes (conflict).md", "notes.md")
        applied = reconciler.reconcile()
        manifest = json.loads(
            (
                self.state / "operations" / f"{applied['operation_id']}.json"
            ).read_text(encoding="utf-8")
        )
        backup = self.state / manifest["records"][0]["canonical_backup"]
        backup.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ReconcilerError, "backup integrity"):
            reconciler.rollback(applied["operation_id"])

    def test_compare_before_swap_detects_race(self):
        canonical = self.write("notes.md", "alpha\n")
        self.write("notes (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            "notes (conflict).md", "notes.md", adapter="append-only-text"
        )
        plan = reconciler.plan()
        canonical.write_text("changed-after-plan\n", encoding="utf-8")
        with self.assertRaises(ReconcilerError):
            reconciler.apply(plan)
        self.assertEqual(canonical.read_text(), "changed-after-plan\n")

    def test_lock_appearing_after_plan_blocks_apply(self):
        canonical = self.write("notes.md", "same\n")
        conflict = self.write("notes (conflict).md", "same\n")
        reconciler = self.reconciler(conflict.name, canonical.name)
        plan = reconciler.plan()
        self.write("LOCK.new-writer.txt", "owner: another\n")
        with self.assertRaises(ReconcilerError):
            reconciler.apply(plan)
        self.assertTrue(conflict.is_file())
        self.assertEqual(canonical.read_text(), "same\n")

    def test_lease_race_has_one_owner(self):
        lease_path = self.state / "lease.lock"
        first = RootLease(lease_path, "one", 60, False)
        first.__enter__()
        try:
            with self.assertRaises(LeaseBusy):
                RootLease(lease_path, "two", 60, False).__enter__()
        finally:
            first.__exit__(None, None, None)

    def test_expired_lease_takeover(self):
        lease_path = self.state / "lease.lock"
        lease_path.parent.mkdir(parents=True)
        lease_path.write_text(
            json.dumps({"expires_epoch": 0}), encoding="utf-8"
        )
        lease = RootLease(lease_path, "new-owner", 60, True)
        lease.__enter__()
        lease.__exit__(None, None, None)
        self.assertFalse(lease_path.exists())

    def test_lost_lease_owner_does_not_delete_successor(self):
        lease_path = self.state / "lease.lock"
        first = RootLease(lease_path, "first", 60, False)
        first.__enter__()
        successor = {
            "schema": "system-gap.conflict-reconciler.lease.v1",
            "actor": "successor",
            "token": "b" * 32,
            "expires_epoch": 99999999999,
        }
        lease_path.write_text(json.dumps(successor), encoding="utf-8")
        with self.assertRaises(LeaseBusy):
            first.renew()
        first.__exit__(None, None, None)
        self.assertTrue(lease_path.exists())
        self.assertEqual(
            json.loads(lease_path.read_text(encoding="utf-8"))["actor"],
            "successor",
        )

    def test_symlinked_candidate_is_blocked(self):
        target = self.write("real.md", "same\n")
        self.write("notes.md", "same\n")
        link = self.root / "notes (conflict).md"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        item = self.reconciler(link.name, "notes.md").plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertIn("symlink-or-reparse-path", item["blockers"])

    def test_windows_junction_escape_is_rejected(self):
        if __import__("os").name != "nt":
            self.skipTest("Windows junction test")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "copy.md").write_text("same\n", encoding="utf-8")
        junction = self.root / "linked"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            self.skipTest(f"junction creation unavailable: {created.stderr}")
        config = self.config("linked/copy.md", "notes.md")
        with self.assertRaisesRegex(ReconcilerError, "outside allowlisted root"):
            ConflictCopyReconciler(config)

    def test_verify_failure_auto_rolls_back(self):
        canonical = self.write("notes.md", "alpha\n")
        conflict = self.write("notes (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            conflict.name, canonical.name, adapter="append-only-text"
        )
        with mock.patch.object(
            reconciler, "_verify_record", side_effect=ReconcilerError("injected")
        ):
            with self.assertRaises(ReconcilerError):
                reconciler.reconcile()
        self.assertEqual(canonical.read_text(), "alpha\n")
        self.assertTrue(conflict.is_file())

    def test_archive_collision_after_swap_restores_canonical_and_preserves_foreign_file(self):
        canonical = self.write("notes.md", "alpha\n")
        conflict = self.write("notes (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            conflict.name, canonical.name, adapter="append-only-text"
        )
        foreign_archives = []

        def collide(root, conflict_path, archive_path, expected):
            archive_path.write_text("foreign archive collision\n", encoding="utf-8")
            foreign_archives.append(archive_path)
            raise ReconcilerError("injected archive collision")

        with mock.patch.object(reconciler, "_archive_conflict", side_effect=collide):
            with self.assertRaises(ReconcilerError):
                reconciler.reconcile()
        self.assertEqual(canonical.read_text(), "alpha\n")
        self.assertEqual(conflict.read_text(), "alpha\nbeta\n")
        self.assertEqual(len(foreign_archives), 1)
        self.assertEqual(
            foreign_archives[0].read_text(encoding="utf-8"),
            "foreign archive collision\n",
        )

    def test_second_run_is_idempotent(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        reconciler = self.reconciler(
            "notes (conflict).md", "notes.md"
        )
        reconciler.reconcile()
        second = reconciler.reconcile()
        self.assertEqual(second["applied"], 0)

    def test_receipt_has_no_absolute_paths_or_content(self):
        self.write("notes.md", "private-looking-content\n")
        self.write("notes (conflict).md", "private-looking-content\n")
        reconciler = self.reconciler(
            "notes (conflict).md", "notes.md"
        )
        result = reconciler.reconcile()
        receipt = json.dumps(result["receipt_data"])
        self.assertNotIn(str(self.root), receipt)
        self.assertNotIn("notes.md", receipt)
        self.assertNotIn("private-looking-content", receipt)

    def test_windows_host_suffix_detector(self):
        self.write("notes.md", "same\n")
        self.write("notes-WORKSTATION.md", "same\n")
        item = self.reconciler(
            "notes-WORKSTATION.md",
            "notes.md",
            known_hosts=["WORKSTATION"],
        ).plan()["items"][0]
        self.assertEqual(item["detection"], "known-host-suffix")

    def test_macos_conflicted_copy_detector(self):
        self.write("notes.md", "same\n")
        self.write("notes (MacBook's conflicted copy 2026-07-29).md", "same\n")
        item = self.reconciler(
            "notes (MacBook's conflicted copy 2026-07-29).md",
            "notes.md",
        ).plan()["items"][0]
        self.assertTrue(item["detection"].startswith("provider-pattern:"))

    def test_temporary_canary(self):
        result = run_canary()
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["idempotent"])
        self.assertTrue(result["rollback_restored"])

    def test_cli_plan_then_apply(self):
        canonical = self.write("notes.md", "same\n")
        conflict = self.write("notes (conflict).md", "same\n")
        config_path = self.base / "config.json"
        plan_path = self.base / "plan.json"
        config_path.write_text(
            json.dumps(self.config(conflict.name, canonical.name)),
            encoding="utf-8",
        )
        self.assertEqual(
            main(
                [
                    "plan",
                    "--config",
                    str(config_path),
                    "--output",
                    str(plan_path),
                ]
            ),
            0,
        )
        self.assertEqual(
            main(
                [
                    "apply",
                    "--config",
                    str(config_path),
                    "--plan",
                    str(plan_path),
                ]
            ),
            0,
        )
        self.assertFalse(conflict.exists())


def shutil_which(command):
    import shutil

    return shutil.which(command)


if __name__ == "__main__":
    unittest.main()
