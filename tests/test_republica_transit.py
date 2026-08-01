import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from system_gap_master.republica_transit import (
    CHECK_ROOT_SCHEMA,
    ERROR_SCHEMA,
    RESOLVE_SCHEMA,
    RepublicaTransitError,
    RepublicaTransitPaths,
    assert_republica_root_outside_yard,
    config_fragment,
    main,
    resolve_republica_transit,
    sqlite_transit_sync_available,
)


class RepublicaTransitFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.yard = self.root / "yard"
        self.yard.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def tearDown(self):
        self.temporary.cleanup()


class ResolveRepublicaTransitTests(RepublicaTransitFixture):
    def test_resolves_the_r9_zone_for_a_namespace(self):
        paths = resolve_republica_transit(self.yard, "my-app")
        self.assertIsInstance(paths, RepublicaTransitPaths)
        self.assertEqual(paths.namespace, "my-app")
        self.assertEqual(paths.zone_relative, "db-transit/my-app")
        self.assertEqual(paths.transit, self.yard / "db-transit" / "my-app")
        # The rule is path arithmetic only -- the tool creates the directory
        # lazily on first publish, this function must not create it.
        self.assertFalse(paths.transit.exists())

    def test_two_namespaces_resolve_to_sibling_zones(self):
        first = resolve_republica_transit(self.yard, "app-one")
        second = resolve_republica_transit(self.yard, "app-two")
        self.assertNotEqual(first.transit, second.transit)
        self.assertEqual(first.transit.parent, second.transit.parent)

    def test_rejects_missing_yard_root(self):
        missing = self.root / "does-not-exist"
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(missing, "my-app")

    def test_rejects_yard_root_that_is_a_file(self):
        not_a_dir = self.root / "yard-file"
        not_a_dir.write_text("x", encoding="utf-8")
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(not_a_dir, "my-app")

    def test_rejects_relative_yard_root(self):
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit("relative/yard", "my-app")

    def test_rejects_unc_yard_root(self):
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(r"\\server\share\yard", "my-app")

    def test_rejects_uppercase_namespace(self):
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(self.yard, "My-App")

    def test_rejects_empty_namespace(self):
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(self.yard, "")

    def test_rejects_namespace_with_path_traversal(self):
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(self.yard, "../escape")

    def test_rejects_namespace_with_path_separator(self):
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(self.yard, "app/sub")

    def test_rejects_overlong_namespace(self):
        with self.assertRaises(RepublicaTransitError):
            resolve_republica_transit(self.yard, "a" * 65)


class ConfigFragmentTests(RepublicaTransitFixture):
    def test_only_yard_derived_keys_are_filled_in(self):
        paths = resolve_republica_transit(self.yard, "my-app")
        fragment = config_fragment(paths)
        self.assertEqual(
            fragment,
            {"transit": str(paths.transit), "namespace": "my-app"},
        )
        # host-specific keys must never be guessed by this module
        for forbidden in ("database", "node_id", "key_file", "republica_root"):
            self.assertNotIn(forbidden, fragment)


class AssertRepublicaRootOutsideYardTests(RepublicaTransitFixture):
    def test_accepts_a_sibling_directory(self):
        validated = assert_republica_root_outside_yard(self.outside, self.yard)
        self.assertEqual(validated, self.outside.resolve())

    def test_rejects_root_nested_inside_the_yard(self):
        nested = self.yard / "republica"
        with self.assertRaises(RepublicaTransitError):
            assert_republica_root_outside_yard(nested, self.yard)

    def test_rejects_root_nested_inside_the_transit_zone(self):
        nested = self.yard / "db-transit" / "my-app" / "showcases"
        with self.assertRaises(RepublicaTransitError):
            assert_republica_root_outside_yard(nested, self.yard)

    def test_rejects_root_equal_to_the_yard(self):
        with self.assertRaises(RepublicaTransitError):
            assert_republica_root_outside_yard(self.yard, self.yard)

    def test_rejects_yard_nested_inside_the_root(self):
        # the yard itself must not become a subdirectory of republica_root either
        parent = self.yard.parent
        with self.assertRaises(RepublicaTransitError):
            assert_republica_root_outside_yard(parent, self.yard)

    def test_rejects_relative_republica_root(self):
        with self.assertRaises(RepublicaTransitError):
            assert_republica_root_outside_yard("relative/republica", self.yard)


class SqliteTransitSyncAvailableTests(unittest.TestCase):
    def test_reports_true_when_importable(self):
        with patch(
            "system_gap_master.republica_transit.importlib.util.find_spec",
            return_value=object(),
        ):
            self.assertTrue(sqlite_transit_sync_available())

    def test_reports_false_when_not_importable(self):
        with patch(
            "system_gap_master.republica_transit.importlib.util.find_spec",
            return_value=None,
        ):
            self.assertFalse(sqlite_transit_sync_available())

    def test_never_imports_the_companion_package_itself(self):
        # No hard dependency: importing this module must not require
        # sqlite_transit_sync to be installed, and calling the availability
        # check must not raise even if it is absent.
        import sys

        self.assertNotIn("sqlite_transit_sync", sys.modules)
        sqlite_transit_sync_available()


class RepublicaTransitCliTests(RepublicaTransitFixture):
    def _run(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_resolve_command_emits_expected_schema(self):
        code, out, _ = self._run(
            ["resolve", "--yard-root", str(self.yard), "--namespace", "my-app"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema"], RESOLVE_SCHEMA)
        self.assertEqual(payload["namespace"], "my-app")
        self.assertEqual(payload["zone_relative"], "db-transit/my-app")
        self.assertEqual(
            payload["config_fragment"],
            {"transit": payload["transit"], "namespace": "my-app"},
        )
        self.assertIn("sqlite_transit_sync_available", payload)
        self.assertIsInstance(payload["sqlite_transit_sync_available"], bool)

    def test_resolve_command_fails_closed_on_bad_namespace(self):
        code, out, err = self._run(
            ["resolve", "--yard-root", str(self.yard), "--namespace", "Not Safe"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        payload = json.loads(err)
        self.assertEqual(payload["schema"], ERROR_SCHEMA)
        self.assertIn("namespace", payload["error"])

    def test_check_root_command_emits_expected_schema(self):
        code, out, _ = self._run(
            [
                "check-root",
                "--yard-root",
                str(self.yard),
                "--republica-root",
                str(self.outside),
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema"], CHECK_ROOT_SCHEMA)
        self.assertTrue(payload["outside_yard"])

    def test_check_root_command_fails_closed_when_nested(self):
        nested = self.yard / "republica"
        code, out, err = self._run(
            [
                "check-root",
                "--yard-root",
                str(self.yard),
                "--republica-root",
                str(nested),
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        payload = json.loads(err)
        self.assertEqual(payload["schema"], ERROR_SCHEMA)


if __name__ == "__main__":
    unittest.main()
