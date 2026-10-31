import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import rulefile_mirror as rm


def run(*argv):
    return rm.main(list(argv))


class TestRulefileMirror(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.yard = self.tmp / "yard"
        (self.yard / "agents" / "registry").mkdir(parents=True)
        self.central = self.tmp / "home" / "AGENTS.md"
        self.central.parent.mkdir()
        self.central.write_text("# Rules\nline one\n", encoding="utf-8")
        self.other = self.tmp / "home" / "OTHER.md"
        self.other.write_text("other rules\n", encoding="utf-8")
        self.files = [
            {"id": "CENTRAL", "actor": "a", "role": "central", "source": str(self.central),
             "mirror": "agents/CENTRAL_snapshot.md"},
            {"id": "OTHER", "actor": "b", "role": "actor-rules", "source": str(self.other)},
        ]
        self.write_registry(self.files)

    def write_registry(self, files, **extra):
        data = {"schema": rm.REGISTRY_SCHEMA, "slot": "s1", "host": "h", "files": files, **extra}
        (self.yard / "agents" / "registry" / "s1.json").write_text(json.dumps(data), encoding="utf-8")

    def args(self, *more):
        return ["--yard-root", str(self.yard), "--slot", "s1", *more]

    def state(self):
        path = self.yard / "agents" / "registry" / "s1.state.json"
        return {f["id"]: f["state"] for f in json.loads(path.read_text(encoding="utf-8"))["files"]}

    def test_missing_then_mirrored_then_current(self):
        self.assertEqual(run("check", *self.args()), 0)
        self.assertEqual(self.state(), {"CENTRAL": "mirror-missing", "OTHER": "no-mirror-wanted"})
        self.assertEqual(run("mirror", *self.args()), 0)
        snapshot = self.yard / "agents" / "CENTRAL_snapshot.md"
        self.assertEqual(snapshot.read_bytes(), self.central.read_bytes())
        self.assertEqual(self.state()["CENTRAL"], "current")

    def test_drift_is_detected_backed_up_and_fixed(self):
        run("mirror", *self.args())
        snapshot = self.yard / "agents" / "CENTRAL_snapshot.md"
        snapshot.write_text("# Rules\nlive message from elsewhere\n", encoding="utf-8")
        self.assertEqual(run("check", *self.args("--fail-on-drift")), 2)
        run("mirror", *self.args())
        self.assertEqual(snapshot.read_bytes(), self.central.read_bytes())
        saved = list((self.yard / "agents" / "_previous").glob("CENTRAL_snapshot.*"))
        self.assertEqual(len(saved), 1)
        self.assertIn("live message from elsewhere", saved[0].read_text(encoding="utf-8"))

    def test_dry_run_writes_nothing(self):
        self.assertEqual(run("mirror", "--dry-run", *self.args()), 0)
        self.assertFalse((self.yard / "agents" / "CENTRAL_snapshot.md").exists())

    def test_secret_source_is_never_mirrored(self):
        self.central.write_text("token ghp_" + "a" * 36 + "\n", encoding="utf-8")
        run("mirror", *self.args())
        self.assertFalse((self.yard / "agents" / "CENTRAL_snapshot.md").exists())

    def test_annotated_keeps_head_and_needs_marker(self):
        files = [dict(self.files[0], mode="annotated")]
        self.write_registry(files)
        snapshot = self.yard / "agents" / "CENTRAL_snapshot.md"
        snapshot.write_text("hand head\n", encoding="utf-8")
        run("mirror", *self.args())
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "hand head\n")  # no marker -> untouched
        snapshot.write_text("hand head\n" + rm.MARKER + "\nold body\n", encoding="utf-8")
        run("mirror", *self.args())
        text = snapshot.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("hand head\n" + rm.MARKER + "\n"))
        self.assertIn("line one", text)
        self.assertNotIn("old body", text)
        self.assertEqual(self.state()["CENTRAL"], "current")

    def test_exactly_one_central_rule(self):
        self.write_registry([dict(self.files[0]), dict(self.files[1], role="central")])
        self.assertEqual(run("check", *self.args()), 1)
        self.write_registry([dict(self.files[0], role="loader")])
        self.assertEqual(run("check", *self.args()), 1)

    def test_mirror_path_cannot_leave_agents_dir(self):
        for bad in ("../escape.md", "agents/../escape.md", "elsewhere/x.md", "/abs/x.md", "agents"):
            self.write_registry([dict(self.files[0], mirror=bad)])
            self.assertEqual(run("check", *self.args()), 1, bad)
        self.assertFalse((self.tmp / "escape.md").exists())

    def test_bad_slot_and_schema_are_refused(self):
        self.assertEqual(run("check", "--yard-root", str(self.yard), "--slot", "../s1"), 1)
        path = self.yard / "agents" / "registry" / "s1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["schema"] = "other.v1"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(run("check", *self.args()), 1)

    def test_report_covers_all_states_and_is_ascii_labelled(self):
        run("all", *self.args())
        report = (self.yard / "agents" / "RULEFILES.md").read_text(encoding="utf-8")
        self.assertIn("`CENTRAL`", report)
        self.assertIn("Central rule file: **CENTRAL**", report)
        self.assertIn("none wanted", report)

    def test_source_missing_is_reported_not_mirrored(self):
        self.central.unlink()
        self.assertEqual(run("check", *self.args("--fail-on-drift")), 2)
        self.assertEqual(self.state()["CENTRAL"], "source-missing")


if __name__ == "__main__":
    unittest.main()
