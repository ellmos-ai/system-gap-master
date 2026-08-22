import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import system_gap_master.conflict_copy_reconciler as reconciler_module
from system_gap_master.conflict_copy_reconciler import (
    CONFIG_SCHEMA,
    ConflictCopyReconciler,
    LeaseBusy,
    ReconcilerError,
    RootLease,
    main,
    run_canary,
)


class PlatformAliasTests(unittest.TestCase):
    def test_only_fixed_macos_var_alias_is_platform_allowed(self):
        with mock.patch.object(reconciler_module.sys, "platform", "darwin"):
            self.assertTrue(
                reconciler_module._is_allowed_platform_alias(
                    Path("/var"), Path("/private/var")
                )
            )
            self.assertFalse(
                reconciler_module._is_allowed_platform_alias(
                    Path("/tmp"), Path("/private/tmp")
                )
            )
            self.assertFalse(
                reconciler_module._is_allowed_platform_alias(
                    Path("/var"), Path("/attacker-controlled")
                )
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
        exempt_name_patterns=None,
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
                    "exempt_name_patterns": exempt_name_patterns or [],
                }
            ],
        }

    def reconciler(self, *args, **kwargs):
        return ConflictCopyReconciler(self.config(*args, **kwargs))


class TestSafeMergeClasses(ReconcilerFixture):
    def test_exact_copy_apply_verify_and_rollback(self):
        self.write("notes.md", "same\n")
        self.write("notes (host conflicted copy).md", "same\n")
        reconciler = self.reconciler("notes (host conflicted copy).md", "notes.md")
        plan = reconciler.plan()
        self.assertEqual(plan["items"][0]["merge_class"], "exact")
        applied = reconciler.apply(plan)
        self.assertEqual(applied["applied"], 1)
        self.assertFalse((self.root / "notes (host conflicted copy).md").exists())
        manifest = json.loads(
            (self.state / "operations" / f"{applied['operation_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        archive = self.root / manifest["records"][0]["archive"]
        verified = reconciler.verify(applied["operation_id"])
        self.assertEqual(verified["status"], "verified")
        rolled_back = reconciler.rollback(applied["operation_id"])
        self.assertEqual(rolled_back["status"], "rolled-back")
        self.assertEqual((self.root / "notes.md").read_text(), "same\n")
        self.assertTrue((self.root / "notes (host conflicted copy).md").is_file())
        self.assertTrue(archive.is_file())

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
        conflict = self.write("story (host conflicted copy).md", "one\ntwo\nTHREE\n")
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
        self.assertTrue(
            any(value.startswith("merge-validation:") for value in item["blockers"])
        )

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
        self.assertTrue(
            any(value.startswith("merge-validation:") for value in item["blockers"])
        )

    def test_independent_json_registries_are_not_merged_despite_host_suffix_name(
        self,
    ):
        # Real case found while working ticket T-20260729-04: a skills registry
        # ``components.json`` (schema_version "public-catalog-v1", 129 entries,
        # generated by build_public_registry.py) and
        # ``components-HOST-A.json`` (schema_version "skill-v1", 80 entries,
        # generated by a different tool, with a provenance block the other
        # lacks). The filename pattern looks exactly like a canonical file plus
        # its OneDrive conflict copy; it is two independent generators' output
        # that happen to collide on a naming convention. Merging them would
        # silently destroy whichever side is not currently being read.
        self.write(
            "components.json",
            json.dumps(
                {
                    "schema_version": "public-catalog-v1",
                    "generated_by": "build_public_registry.py",
                    "components": list(range(129)),
                }
            ),
        )
        self.write(
            "components-HOST-A.json",
            json.dumps(
                {
                    "schema_version": "skill-v1",
                    "generated_by": "versionctl registry-generate",
                    "components": list(range(80)),
                    "provenance": {"host": "HOST-A"},
                }
            ),
        )
        item = self.reconciler(
            "components-HOST-A.json",
            "components.json",
            adapter="json-object",
            known_hosts=["HOST-A"],
        ).plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertTrue(
            any(
                "structural-schema-mismatch:schema_version" in value
                for value in item["blockers"]
            ),
            item["blockers"],
        )

    def test_independent_json_registries_blocked_under_default_adapter_too(self):
        # Same pair as above with no explicit adapter (the mapping default):
        # blocked as an unproven conflict without ever reaching the
        # schema-marker check, proving the default path is already safe on
        # its own.
        self.write(
            "components.json",
            json.dumps({"schema_version": "public-catalog-v1", "components": [1]}),
        )
        self.write(
            "components-HOST-A.json",
            json.dumps({"schema_version": "skill-v1", "components": [1, 2]}),
        )
        item = self.reconciler(
            "components-HOST-A.json", "components.json", known_hosts=["HOST-A"]
        ).plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertIn("semantic-or-unproven-conflict", item["blockers"])

    def test_matching_schema_version_with_disjoint_keys_still_merges(self):
        # Control case: the schema-marker check must not over-block the
        # adapter's documented safe case -- two sides of the SAME schema that
        # merely added different, non-overlapping keys.
        self.write(
            "state.json", json.dumps({"schema_version": "v1", "left": 1})
        )
        self.write(
            "state (conflict).json",
            json.dumps({"schema_version": "v1", "right": 2}),
        )
        reconciler = self.reconciler(
            "state (conflict).json", "state.json", adapter="json-object"
        )
        reconciler.reconcile()
        self.assertEqual(
            json.loads((self.root / "state.json").read_text()),
            {"schema_version": "v1", "left": 1, "right": 2},
        )

    def test_generated_by_mismatch_alone_blocks_json_merge(self):
        self.write(
            "state.json", json.dumps({"generated_by": "tool-a", "left": 1})
        )
        self.write(
            "state (conflict).json",
            json.dumps({"generated_by": "tool-b", "right": 2}),
        )
        item = self.reconciler(
            "state (conflict).json", "state.json", adapter="json-object"
        ).plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertTrue(
            any(
                "structural-schema-mismatch:generated_by" in value
                for value in item["blockers"]
            ),
            item["blockers"],
        )


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
        item = self.reconciler("notes (conflict).md", "notes.md").plan()["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertTrue(
            any(value.startswith("active-lock:") for value in item["blockers"])
        )

    def test_cloud_attestation_is_required(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        self.assert_blocked(
            self.reconciler("notes (conflict).md", "notes.md", cloud_ready=False),
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
        items = self.reconciler("known (conflict).md", "known.md").plan()["items"]
        unknown = next(item for item in items if item["conflict"].startswith("unknown"))
        self.assertEqual(unknown["blockers"], ["canonical-authority-missing"])

    def test_path_escape_rejected(self):
        with self.assertRaises(ReconcilerError):
            ConflictCopyReconciler(self.config("../escape.txt", "safe.txt"))

    def test_windows_ads_and_reserved_paths_are_rejected_portably(self):
        with self.assertRaises(ReconcilerError):
            ConflictCopyReconciler(self.config("notes.md:stream", "safe.txt"))
        with self.assertRaises(ReconcilerError):
            ConflictCopyReconciler(self.config("CON.txt", "safe.txt"))

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
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=self.root, check=True
        )
        subprocess.run(
            ["git", "add", "notes.md", "notes-HOST.md"], cwd=self.root, check=True
        )
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        (self.root / "notes.md").write_text("dirty\n", encoding="utf-8")
        item = self.reconciler(
            "notes-HOST.md",
            "notes.md",
            known_hosts=["HOST"],
        ).plan()["items"][0]
        self.assertIn("foreign-dirty-work", item["blockers"])


class TestSafetyLifecycle(ReconcilerFixture):
    def test_windows_short_path_alias_is_not_treated_as_reparse(self):
        if os.name != "nt":
            self.skipTest("Windows 8.3 path test")
        buffer = __import__("ctypes").create_unicode_buffer(32768)
        length = __import__("ctypes").windll.kernel32.GetShortPathNameW(
            str(self.base), buffer, len(buffer)
        )
        if not length or length >= len(buffer):
            self.skipTest("Windows short-path alias unavailable")
        short_base = Path(buffer.value)
        if os.path.normcase(str(short_base)) == os.path.normcase(str(self.base)):
            self.skipTest("Windows short-path alias is identical")

        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        config = self.config("notes (conflict).md", "notes.md")
        config["state_dir"] = str(short_base / "state")
        config["roots"][0]["path"] = str(short_base / "yard")

        plan = ConflictCopyReconciler(config).plan()
        self.assertEqual(plan["items"][0]["merge_class"], "exact")

    def test_windows_root_junction_is_rejected_before_canonicalization(self):
        if os.name != "nt":
            self.skipTest("Windows junction test")
        target = self.base / "outside-root"
        target.mkdir()
        junction = self.base / "root-junction"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if created.returncode != 0:
            self.skipTest(f"junction creation unavailable: {created.stderr}")
        config = self.config("notes (conflict).md", "notes.md")
        config["roots"][0]["path"] = str(junction)
        with self.assertRaisesRegex(ReconcilerError, "symlink-or-reparse"):
            ConflictCopyReconciler(config)

    def test_windows_state_junction_is_rejected_before_canonicalization(self):
        if os.name != "nt":
            self.skipTest("Windows junction test")
        target = self.base / "outside-state"
        target.mkdir()
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(self.state), str(target)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if created.returncode != 0:
            self.skipTest(f"junction creation unavailable: {created.stderr}")
        with self.assertRaisesRegex(ReconcilerError, "symlink-or-reparse"):
            self.reconciler("notes (conflict).md", "notes.md")

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
        manifest_path = self.state / "operations" / f"{applied['operation_id']}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["records"][0]["canonical"] = "other.md"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ReconcilerError, "integrity"):
            reconciler.rollback(applied["operation_id"])

    def test_signed_manifest_cannot_substitute_requested_operation(self):
        self.write("notes.md", "same\n")
        self.write("notes (conflict).md", "same\n")
        reconciler = self.reconciler("notes (conflict).md", "notes.md")
        applied = reconciler.reconcile()
        operation_path = self.state / "operations" / f"{applied['operation_id']}.json"
        substituted_id = "f" * 32
        if substituted_id == applied["operation_id"]:
            substituted_id = "e" * 32
        substituted_path = self.state / "operations" / f"{substituted_id}.json"
        substituted_path.write_bytes(operation_path.read_bytes())
        with self.assertRaisesRegex(
            ReconcilerError, "identity does not match requested operation"
        ):
            reconciler.verify(substituted_id)

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
            (self.state / "operations" / f"{applied['operation_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        backup = self.state / manifest["records"][0]["canonical_backup"]
        backup.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ReconcilerError, "backup integrity"):
            reconciler.rollback(applied["operation_id"])

    def test_rollback_rechecks_canonical_after_preflight(self):
        canonical = self.write("notes.md", "alpha\n")
        conflict = self.write("notes (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            conflict.name, canonical.name, adapter="append-only-text"
        )
        applied = reconciler.reconcile()
        original_renew = RootLease.renew
        renew_calls = 0

        def race_after_preflight(lease):
            nonlocal renew_calls
            original_renew(lease)
            renew_calls += 1
            if renew_calls == 2:
                canonical.write_text("foreign-after-preflight\n", encoding="utf-8")

        with mock.patch.object(RootLease, "renew", new=race_after_preflight):
            with self.assertRaises(ReconcilerError):
                reconciler.rollback(applied["operation_id"])
        self.assertEqual(canonical.read_text(), "foreign-after-preflight\n")
        self.assertFalse(conflict.exists())

    def test_rollback_never_removes_archive_changed_after_preflight(self):
        canonical = self.write("notes.md", "alpha\n")
        conflict = self.write("notes (conflict).md", "alpha\nbeta\n")
        reconciler = self.reconciler(
            conflict.name, canonical.name, adapter="append-only-text"
        )
        applied = reconciler.reconcile()
        manifest = json.loads(
            (self.state / "operations" / f"{applied['operation_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        archive = self.root / manifest["records"][0]["archive"]
        original_renew = RootLease.renew
        renew_calls = 0

        def race_after_preflight(lease):
            nonlocal renew_calls
            original_renew(lease)
            renew_calls += 1
            if renew_calls == 2:
                archive.write_text("foreign-archive\n", encoding="utf-8")

        with mock.patch.object(RootLease, "renew", new=race_after_preflight):
            with self.assertRaises(ReconcilerError):
                reconciler.rollback(applied["operation_id"])
        self.assertEqual(canonical.read_text(), "alpha\nbeta\n")
        self.assertFalse(conflict.exists())
        self.assertEqual(archive.read_text(), "foreign-archive\n")

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
        lease_path.write_text(json.dumps({"expires_epoch": 0}), encoding="utf-8")
        lease = RootLease(lease_path, "new-owner", 60, True)
        lease.__enter__()
        lease.__exit__(None, None, None)
        self.assertFalse(lease_path.exists())

    def test_expired_takeover_cannot_cross_lease_mutation_guard(self):
        lease_path = self.state / "lease.lock"
        lease_path.parent.mkdir(parents=True)
        lease_path.write_text(json.dumps({"expires_epoch": 0}), encoding="utf-8")
        guard_holder = RootLease(lease_path, "guard-holder", 60, True)
        with guard_holder._mutation_guard():
            with self.assertRaisesRegex(
                LeaseBusy, "lease mutation already in progress"
            ):
                RootLease(lease_path, "racing-owner", 60, True).__enter__()
        self.assertEqual(
            json.loads(lease_path.read_text(encoding="utf-8"))["expires_epoch"],
            0,
        )

    def test_windows_guard_reparse_check_precedes_first_write(self):
        if reconciler_module.os.name != "nt":
            self.skipTest("Windows O_NOFOLLOW=0 regression")
        self.assertEqual(getattr(reconciler_module.os, "O_NOFOLLOW", 0), 0)
        self.state.mkdir(parents=True)
        lease = RootLease(self.state / "lease.lock", "owner", 60, False)
        real_write = reconciler_module.os.write
        with (
            mock.patch.object(
                reconciler_module,
                "_is_link_or_reparse",
                return_value=True,
            ),
            mock.patch.object(
                reconciler_module.os,
                "write",
                wraps=real_write,
            ) as write,
        ):
            with self.assertRaisesRegex(LeaseBusy, "guard became unsafe"):
                with lease._mutation_guard():
                    pass
        write.assert_not_called()

    def test_expired_takeover_restores_displaced_fresh_lease(self):
        lease_path = self.state / "lease.lock"
        lease_path.parent.mkdir(parents=True)
        lease_path.write_text(
            json.dumps({"token": "expired", "expires_epoch": 0}),
            encoding="utf-8",
        )
        fresh = {
            "schema": "system-gap.conflict-reconciler.lease.v1",
            "actor": "fresh-owner",
            "token": "f" * 32,
            "expires_epoch": 99999999999,
        }
        real_replace = reconciler_module.os.replace

        def replace_after_foreign_takeover(source, destination):
            if Path(source) == lease_path:
                lease_path.write_text(json.dumps(fresh), encoding="utf-8")
            return real_replace(source, destination)

        with mock.patch.object(
            reconciler_module.os,
            "replace",
            side_effect=replace_after_foreign_takeover,
        ):
            with self.assertRaisesRegex(LeaseBusy, "identity check failed"):
                RootLease(lease_path, "racing-owner", 60, True).__enter__()
        self.assertEqual(
            json.loads(lease_path.read_text(encoding="utf-8"))["token"],
            fresh["token"],
        )

    def test_renew_cannot_cross_lease_mutation_guard(self):
        lease_path = self.state / "lease.lock"
        lease = RootLease(lease_path, "owner", 60, False)
        lease.__enter__()
        try:
            with lease._mutation_guard():
                with self.assertRaisesRegex(
                    LeaseBusy, "lease mutation already in progress"
                ):
                    lease.renew()
            lease.renew()
        finally:
            lease.__exit__(None, None, None)

    def test_partial_temp_renewal_preserves_lease_and_allows_later_takeover(self):
        lease_path = self.state / "lease.lock"
        lease = RootLease(lease_path, "owner", 60, False)
        lease.__enter__()
        original = lease_path.read_bytes()
        real_fdopen = reconciler_module.os.fdopen

        class PartialWriter:
            def __init__(self, descriptor, *args, **kwargs):
                self.stream = real_fdopen(descriptor, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.stream.close()

            def write(self, data):
                self.stream.write(data[: max(1, len(data) // 2)])
                self.stream.flush()
                raise OSError("injected partial temp write")

            def flush(self):
                self.stream.flush()

            def fileno(self):
                return self.stream.fileno()

        with mock.patch.object(
            reconciler_module.os, "fdopen", side_effect=PartialWriter
        ):
            with self.assertRaises(LeaseBusy):
                lease.renew()
        self.assertEqual(lease_path.read_bytes(), original)
        self.assertFalse(list(lease_path.parent.glob(f".{lease_path.name}.*.renew")))
        crashed_temp = lease_path.with_name(f".{lease_path.name}.crashed.partial.renew")
        crashed_temp.write_bytes(b'{"token":"partial"')
        future = reconciler_module.time.time() + 120
        with mock.patch.object(reconciler_module.time, "time", return_value=future):
            successor = RootLease(lease_path, "successor", 60, True)
            successor.__enter__()
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8"))["actor"],
                "successor",
            )
            successor.__exit__(None, None, None)
        self.assertEqual(crashed_temp.read_bytes(), b'{"token":"partial"')

    def test_malformed_lease_requires_age_and_takeover_policy_for_quarantine(self):
        lease_path = self.state / "lease.lock"
        lease_path.parent.mkdir(parents=True)
        malformed = b'{"token":"truncated"'
        lease_path.write_bytes(malformed)
        with self.assertRaisesRegex(LeaseBusy, "too recent"):
            RootLease(lease_path, "new-owner", 30, True).__enter__()
        self.assertEqual(lease_path.read_bytes(), malformed)

        old = reconciler_module.time.time() - 60
        reconciler_module.os.utime(lease_path, (old, old))
        with self.assertRaises(LeaseBusy):
            RootLease(lease_path, "disabled", 30, False).__enter__()
        self.assertEqual(lease_path.read_bytes(), malformed)

        recovered = RootLease(lease_path, "new-owner", 30, True)
        recovered.__enter__()
        quarantine = list(lease_path.parent.glob("lease.malformed-*.lock"))
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(quarantine[0].read_bytes(), malformed)
        recovered.__exit__(None, None, None)

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
            encoding="utf-8",
            errors="replace",
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

    def test_archive_collision_after_swap_restores_canonical_and_preserves_foreign_file(
        self,
    ):
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
        reconciler = self.reconciler("notes (conflict).md", "notes.md")
        reconciler.reconcile()
        second = reconciler.reconcile()
        self.assertEqual(second["applied"], 0)

    def test_receipt_has_no_absolute_paths_or_content(self):
        self.write("notes.md", "private-looking-content\n")
        self.write("notes (conflict).md", "private-looking-content\n")
        reconciler = self.reconciler("notes (conflict).md", "notes.md")
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

    def test_host_suffixed_by_design_artifacts_are_never_reported(self):
        # These filenames are the kind of by-design, per-host artifact that a
        # bare "-HOST" suffix detector would otherwise flag as a conflict
        # copy (ticket T-20260729-04, SS4b): a per-host status log, a
        # per-host registry snapshot and a per-host scan manifest that itself
        # carries a trailing host token. A yard's ``exempt_name_patterns``
        # must keep them out of the scan entirely -- reporting them as
        # candidates is the failure mode, independent of whether they would
        # ever pass the (separate) canonical-mapping gate.
        self.write("notes.md", "same\n")
        self.write("notes-HOST-A.md", "same\n")
        self.write("CROSS_SYSTEM_SYNC_STATUS-HOST-A.md", "status\n")
        self.write("repos-HOST-A.json", "{}\n")
        self.write(
            "konflikt-wartung/scan_HOST-A_manifest-HOST-A.txt", "manifest\n"
        )
        reconciler = self.reconciler(
            "notes-HOST-A.md",
            "notes.md",
            known_hosts=["HOST-A"],
            exempt_name_patterns=[
                r"(?:^|/)CROSS_SYSTEM_SYNC_STATUS-[^/]+$",
                r"(?:^|/)repos-[^/]+\.json$",
                r"(?:^|/)konflikt-wartung/scan_[^/]+$",
            ],
        )
        scan = reconciler.scan()
        reported = {item["conflict"] for item in scan["candidates"]}
        self.assertIn("notes-HOST-A.md", reported)
        self.assertNotIn("CROSS_SYSTEM_SYNC_STATUS-HOST-A.md", reported)
        self.assertNotIn("repos-HOST-A.json", reported)
        self.assertNotIn(
            "konflikt-wartung/scan_HOST-A_manifest-HOST-A.txt", reported
        )
        # Suppression must stay auditable: the scan reports what it excluded,
        # not just what it kept, so a fail-open regex mistake is discoverable.
        exempted = set(scan["exempted_by_policy"]["test-root"])
        self.assertEqual(
            exempted,
            {
                "CROSS_SYSTEM_SYNC_STATUS-HOST-A.md",
                "repos-HOST-A.json",
                "konflikt-wartung/scan_HOST-A_manifest-HOST-A.txt",
            },
        )

    def test_archive_directory_is_never_walked(self):
        self.write("notes.md", "same\n")
        self.write("notes-HOST-A.md", "same\n")
        self.write("_archive/old-notes-HOST-A.md", "same\n")
        self.write("_Archive/other-notes-HOST-A.md", "same\n")
        reconciler = self.reconciler(
            "notes-HOST-A.md", "notes.md", known_hosts=["HOST-A"]
        )
        scan = reconciler.scan()
        reported = {item["conflict"] for item in scan["candidates"]}
        self.assertIn("notes-HOST-A.md", reported)
        self.assertNotIn("_archive/old-notes-HOST-A.md", reported)
        self.assertNotIn("_Archive/other-notes-HOST-A.md", reported)

    def test_invalid_exempt_name_pattern_fails_closed(self):
        with self.assertRaises(ReconcilerError):
            self.reconciler(
                "notes-HOST-A.md",
                "notes.md",
                known_hosts=["HOST-A"],
                exempt_name_patterns=["("],
            )

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


class RegenerableAndHostSpecificTests(unittest.TestCase):
    """Guards added 2026-08-01 after a review queue filled up with pure noise.

    Thirteen of thirteen "undecidable" candidates in a real run were bytecode
    caches and VCS internals. A queue like that trains reviewers to ignore it,
    which is worse than having no queue at all.
    """

    def test_bytecode_and_cache_dirs_are_regenerable(self):
        from system_gap_master.conflict_copy_reconciler import is_regenerable

        for name in (
            "pkg/__pycache__/mod.cpython-312-HOSTNAME.pyc",
            "mod-HOSTNAME.pyc",
            ".pytest_cache/v/cache/nodeids-HOSTNAME",
            "build/Main-HOSTNAME.class",
        ):
            self.assertTrue(is_regenerable(Path(name)), name)

    def test_source_and_documents_are_not_regenerable(self):
        from system_gap_master.conflict_copy_reconciler import is_regenerable

        for name in ("src/mod-HOSTNAME.py", "README-HOSTNAME.md", "data-HOSTNAME.json"):
            self.assertFalse(is_regenerable(Path(name)), name)

    def test_regenerable_candidates_are_not_surfaced(self):
        """End-to-end: a bytecode conflict copy must never reach the queue."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "mod.cpython-312.pyc").write_bytes(b"canonical")
            (cache / "mod.cpython-312-BOX.pyc").write_bytes(b"copy")
            (root / "README.md").write_text("canonical\n", encoding="utf-8")
            (root / "README-BOX.md").write_text("copy\n", encoding="utf-8")

            policy = reconciler_module.RootPolicy(
                root_id="r1", path=root, known_hosts=("BOX",), mappings={},
                archive_dir="_archive", cloud_ready=True,
            )
            engine = ConflictCopyReconciler.__new__(ConflictCopyReconciler)
            engine.max_files = 1000
            found = [rel for _, rel, _ in engine._iter_candidates(policy)]

            self.assertIn("README-BOX.md", found)
            self.assertFalse(
                [f for f in found if f.endswith(".pyc")],
                f"bytecode must be filtered, got {found}",
            )

    def test_host_specific_content_is_reported(self):
        from system_gap_master.conflict_copy_reconciler import host_specific_markers

        self.assertTrue(host_specific_markers(r"path = C:\Users\alice\data"))
        self.assertTrue(host_specific_markers("home = /Users/bob/cfg"))
        self.assertTrue(host_specific_markers("runs on BOX-01", known_hosts=("BOX-01",)))
        # Portable references must NOT be flagged -- they are the desired form.
        self.assertEqual(host_specific_markers("path = %USERPROFILE%/data"), [])
        self.assertEqual(host_specific_markers("path = ~/data"), [])

    def test_excerpt_reports_start_middle_end(self):
        from system_gap_master.conflict_copy_reconciler import excerpt

        text = "\n".join(f"line {i}." for i in range(1, 21))
        lines = excerpt(text)
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].startswith("START:"))
        self.assertTrue(lines[2].startswith("MIDDLE:"))
        self.assertTrue(lines[-1].startswith("END:"))
        self.assertIn("line 1.", lines[0])
        self.assertIn("line 20.", lines[-1])

    def test_excerpt_handles_short_and_empty_input(self):
        from system_gap_master.conflict_copy_reconciler import excerpt

        self.assertEqual(excerpt(""), ["(empty)"])
        self.assertEqual(excerpt("only one line"), ["only one line"])


class JsonSchemaMismatchTests(unittest.TestCase):
    """Guard added 2026-08-08 (ticket T-20260729-04): a filename pattern is
    not evidence that two files are the same document. Two independently
    generated JSON registries were found sharing a "-HOST" naming convention
    while carrying different schema_version/generated_by values -- exactly
    the shape a naive canonical/conflict mapping would misidentify.
    """

    def test_differing_schema_version_on_both_sides_is_a_mismatch(self):
        from system_gap_master.conflict_copy_reconciler import json_schema_mismatch

        reason = json_schema_mismatch(
            {"schema_version": "public-catalog-v1", "components": [1]},
            {"schema_version": "skill-v1", "components": [1, 2]},
        )
        self.assertEqual(reason, "structural-schema-mismatch:schema_version")

    def test_differing_generated_by_on_both_sides_is_a_mismatch(self):
        from system_gap_master.conflict_copy_reconciler import json_schema_mismatch

        reason = json_schema_mismatch(
            {"generated_by": "build_public_registry.py"},
            {"generated_by": "versionctl registry-generate"},
        )
        self.assertEqual(reason, "structural-schema-mismatch:generated_by")

    def test_matching_marker_value_is_not_a_mismatch(self):
        from system_gap_master.conflict_copy_reconciler import json_schema_mismatch

        self.assertIsNone(
            json_schema_mismatch(
                {"schema_version": "v1", "left": 1}, {"schema_version": "v1", "right": 2}
            )
        )

    def test_marker_present_on_only_one_side_is_not_a_mismatch(self):
        # A newer generator adding a schema_version/generated_by field that an
        # older, not-yet-regenerated side lacks is the ordinary case of a
        # genuine conflict copy -- it must not fail closed by this rule.
        from system_gap_master.conflict_copy_reconciler import json_schema_mismatch

        self.assertIsNone(
            json_schema_mismatch({"schema_version": "v1", "left": 1}, {"left": 1})
        )
        self.assertIsNone(
            json_schema_mismatch({"left": 1}, {"generated_by": "tool-a", "left": 1})
        )

    def test_disjoint_keys_without_any_marker_is_not_a_mismatch(self):
        # The adapter's documented safe case (test_json_object_disjoint_merge)
        # must stay unaffected: no schema_version/generated_by field anywhere
        # means this check has nothing to say.
        from system_gap_master.conflict_copy_reconciler import json_schema_mismatch

        self.assertIsNone(json_schema_mismatch({"left": 1}, {"right": 2}))

    def test_non_object_json_is_not_a_mismatch(self):
        from system_gap_master.conflict_copy_reconciler import json_schema_mismatch

        self.assertIsNone(json_schema_mismatch([1, 2, 3], [1, 2]))
        self.assertIsNone(json_schema_mismatch("a", "b"))
