import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import rulefile_mirror as rm


def run(*argv):
    """Run main() and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = rm.main(list(argv))
        except SystemExit as exc:  # argparse
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
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
        self.snapshot = self.yard / "agents" / "CENTRAL_snapshot.md"

    def write_registry(self, files, **extra):
        data = {"schema": rm.REGISTRY_SCHEMA, "slot": "s1", "host": "h", "files": files, **extra}
        (self.yard / "agents" / "registry" / "s1.json").write_text(json.dumps(data), encoding="utf-8")

    def args(self, *more):
        return ["--yard-root", str(self.yard), "--slot", "s1", *more]

    def state(self):
        path = self.yard / "agents" / "registry" / "s1.state.json"
        return {f["id"]: f["state"] for f in json.loads(path.read_text(encoding="utf-8"))["files"]}


class TestBehaviour(Base):
    def test_missing_then_mirrored_then_current(self):
        self.assertEqual(run("check", *self.args())[0], 0)
        self.assertEqual(self.state(), {"CENTRAL": "mirror-missing", "OTHER": "no-mirror-wanted"})
        self.assertEqual(run("mirror", *self.args())[0], 0)
        self.assertEqual(self.snapshot.read_bytes(), self.central.read_bytes())
        self.assertEqual(self.state()["CENTRAL"], "current")

    def test_drift_is_detected_backed_up_and_fixed(self):
        run("mirror", *self.args())
        self.snapshot.write_text("# Rules\nlive message from elsewhere\n", encoding="utf-8")
        self.assertEqual(run("check", *self.args("--fail-on-drift"))[0], 2)
        run("mirror", *self.args())
        self.assertEqual(self.snapshot.read_bytes(), self.central.read_bytes())
        saved = list((self.yard / "agents" / "_previous").glob("CENTRAL_snapshot.*"))
        self.assertEqual(len(saved), 1)
        self.assertIn("live message from elsewhere", saved[0].read_text(encoding="utf-8"))

    def test_two_drifts_in_the_same_second_keep_both_backups(self):
        fixed = rm.dt.datetime(2026, 1, 2, 3, 4, 5)
        with patch.object(rm.dt, "datetime", wraps=rm.dt.datetime) as fake:
            fake.now.return_value = fixed
            run("mirror", *self.args())
            for message in ("message ONE", "message TWO"):
                self.snapshot.write_text("# Rules\n" + message + "\n", encoding="utf-8")
                run("mirror", *self.args())
        texts = sorted(p.read_text(encoding="utf-8") for p in (self.yard / "agents" / "_previous").iterdir())
        self.assertEqual(len(texts), 2)
        self.assertTrue(any("message ONE" in t for t in texts))
        self.assertTrue(any("message TWO" in t for t in texts))

    def test_dry_run_writes_nothing_at_all(self):
        code, out, _ = run("all", "--dry-run", *self.args())
        self.assertEqual(code, 0)
        self.assertIn("would mirror", out)
        self.assertFalse(self.snapshot.exists())
        self.assertFalse((self.yard / "agents" / "registry" / "s1.state.json").exists())
        self.assertFalse((self.yard / "agents" / "RULEFILES.md").exists())

    def test_secret_source_is_never_mirrored_for_many_formats(self):
        samples = ["ghp_" + "a" * 36, "github_pat_" + "A1b2" * 8, "sk_live_" + "a" * 20,
                   "AIza" + "B" * 35, "hf_" + "c" * 34,
                   "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + ".sig_" + "c" * 8,
                   "Authorization: Bearer " + "d" * 40, "sk-ant-api03-" + "e" * 30]
        for sample in samples:
            self.central.write_text("token " + sample + "\n", encoding="utf-8")
            code, out, _ = run("mirror", *self.args())
            self.assertEqual(code, 3, sample)
            self.assertFalse(self.snapshot.exists(), sample)
            self.assertNotIn(sample, out)

    def test_annotated_keeps_head_and_needs_marker(self):
        self.write_registry([dict(self.files[0], mode="annotated")])
        self.snapshot.write_text("hand head\n", encoding="utf-8")
        code, _, _ = run("mirror", *self.args())
        self.assertEqual(code, 3)  # refused, reported
        self.assertEqual(self.snapshot.read_text(encoding="utf-8"), "hand head\n")
        self.assertFalse((self.yard / "agents" / "_previous").exists())
        self.snapshot.write_text("hand head\n" + rm.MARKER + "\nold body\n", encoding="utf-8")
        self.assertEqual(run("mirror", *self.args())[0], 0)
        text = self.snapshot.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("hand head\n" + rm.MARKER + "\n"))
        self.assertIn("line one", text)
        self.assertNotIn("old body", text)
        self.assertEqual(self.state()["CENTRAL"], "current")

    def test_unknown_mode_is_refused_not_treated_as_copy(self):
        self.write_registry([dict(self.files[0], mode="Annotated")])
        code, _, err = run("check", *self.args())
        self.assertEqual(code, 1)
        self.assertIn("unknown mode", err)

    def test_source_missing_oversized_and_binary_are_reported_not_mirrored(self):
        self.central.write_bytes(b"x" * (rm.MAX_SOURCE_BYTES + 1))
        self.assertEqual(run("mirror", *self.args("--fail-on-drift"))[0], 2)
        self.assertEqual(self.state()["CENTRAL"], "source-invalid")
        self.central.write_bytes(b"text\x00binary")
        run("check", *self.args())
        self.assertEqual(self.state()["CENTRAL"], "source-invalid")
        self.central.unlink()
        self.assertEqual(run("check", *self.args("--fail-on-drift"))[0], 2)
        self.assertEqual(self.state()["CENTRAL"], "source-missing")
        self.assertFalse(self.snapshot.exists())

    def test_report_covers_all_states_and_survives_foreign_garbage(self):
        run("all", *self.args())
        registry = self.yard / "agents" / "registry"
        (registry / "evil.state.json").write_text('{"slot": "x|y", "files": [1, {"id": "a`b"}]}', encoding="utf-8")
        (registry / "broken.state.json").write_text("{not json", encoding="utf-8")
        (registry / "list.state.json").write_text("[]", encoding="utf-8")
        self.assertEqual(run("report", "--yard-root", str(self.yard))[0], 0)
        report = (self.yard / "agents" / "RULEFILES.md").read_text(encoding="utf-8")
        self.assertIn("Central rule file: **CENTRAL**", report)
        self.assertIn("none wanted", report)
        self.assertIn("broken.state.json", report)
        self.assertNotIn("x|y", report)


class TestRegistryAndPaths(Base):
    def test_exactly_one_central_rule(self):
        self.write_registry([dict(self.files[0]), dict(self.files[1], role="central")])
        self.assertEqual(run("check", *self.args())[0], 1)
        self.write_registry([dict(self.files[0], role="loader")])
        self.assertEqual(run("check", *self.args())[0], 1)

    def test_mirror_path_cannot_leave_agents_or_hit_reserved_names(self):
        bad_paths = ["../escape.md", "agents/../escape.md", "elsewhere/x.md", "/abs/x.md", "agents",
                     "agents/registry/s1.json", "agents/_previous/x.md", "agents/RULEFILES.md", "agents/README.md"]
        for bad in bad_paths:
            self.write_registry([dict(self.files[0], mirror=bad)])
            code, _, err = run("check", *self.args())
            self.assertEqual(code, 1, bad)
            self.assertIn("mirror path", err, bad)
        self.assertFalse((self.tmp / "escape.md").exists())
        registry = json.loads((self.yard / "agents" / "registry" / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(registry["files"][0]["mirror"], "agents/README.md")  # untouched

    def test_two_entries_may_not_share_a_mirror(self):
        second = dict(self.files[1], id="SECOND", mirror="agents/CENTRAL_snapshot.md")
        self.write_registry([self.files[0], second])
        code, _, err = run("check", *self.args())
        self.assertEqual(code, 1)
        self.assertIn("share the same mirror", err)

    @unittest.skipUnless(os.name == "nt", "junction creation is Windows-only here")
    def test_agents_junction_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        real = self.yard / "agents"
        moved = self.tmp / "agents-real"
        real.rename(moved)
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(real), str(moved)], capture_output=True)
        if made.returncode != 0:
            self.skipTest("junction could not be created")
        code, _, err = run("check", *self.args())
        self.assertEqual(code, 1)
        self.assertIn("not a link or junction", err)
        self.assertEqual(list(outside.iterdir()), [])

    def test_bad_slot_schema_json_and_arguments_exit_one(self):
        self.assertEqual(run("check", "--yard-root", str(self.yard), "--slot", "../s1")[0], 1)
        path = self.yard / "agents" / "registry" / "s1.json"
        path.write_text("{broken", encoding="utf-8")
        code, _, err = run("check", *self.args())
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)
        path.write_text("[]", encoding="utf-8")
        self.assertEqual(run("check", *self.args())[0], 1)
        path.write_text(json.dumps({"schema": "other.v1", "files": []}), encoding="utf-8")
        self.assertEqual(run("check", *self.args())[0], 1)
        # missing arguments must not collide with the drift code 2
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(rm.ENV_YARD, None)
            os.environ.pop(rm.ENV_SLOT, None)
            self.assertEqual(run("check")[0], 1)
            self.assertEqual(run("check", "--yard-root", str(self.yard))[0], 1)
            self.assertEqual(run("nonsense")[0], 1)


if __name__ == "__main__":
    unittest.main()
