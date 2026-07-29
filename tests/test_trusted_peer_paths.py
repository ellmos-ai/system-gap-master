import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from system_gap_master.trusted_peer_paths import (
    ENTRIES_SCHEMA,
    LOCAL_CONFIG_SCHEMA,
    TrustedPeerPathError,
    TrustedPeerPathRegistry,
    main,
)


class TrustedPeerPathFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.yard = self.root / "yard"
        (self.yard / "hosts").mkdir(parents=True)
        self.keys = self.root / "keys"
        self.keys.mkdir()
        self.host_a_key = self.keys / "host-a.hmac"
        self.host_a_key.write_bytes(b"a" * 64)
        self.known_hosts = self.keys / "known_hosts"
        self.known_hosts.write_text(
            "peer-a.example ssh-ed25519 AAAATESTONLY\n", encoding="utf-8"
        )
        self.sftp_executable = self.root / "bin" / "sftp"
        self.sftp_executable.parent.mkdir()
        self.sftp_executable.write_text("test executable placeholder\n", encoding="utf-8")
        self.pull_root = self.root / "pulls"
        self.pull_root.mkdir()
        self.publisher_config = {
            "schema": LOCAL_CONFIG_SCHEMA,
            "yard_root": str(self.yard),
            "local_host_id": "HOST-A",
            "local_peer_id": "PEER-A",
            "state_dir": str(self.root / "state-a"),
            "publisher": {
                "key_id": "host-a-v1",
                "signing_key_ref": str(self.host_a_key),
                "endpoints": [
                    {
                        "endpoint_id": "tailscale-sftp",
                        "transport": "sftp",
                        "network": "tailscale",
                        "host": "peer-a.example",
                        "port": 22,
                        "username": "registry-reader",
                    }
                ],
            },
            "trusted_hosts": [],
            "pull_destination_roots": [str(self.pull_root)],
            "ssh": {
                "known_hosts_ref": str(self.known_hosts),
                "sftp_executable_ref": str(self.sftp_executable),
                "connect_timeout_seconds": 10,
            },
        }
        self.peer_config = {
            "schema": LOCAL_CONFIG_SCHEMA,
            "yard_root": str(self.yard),
            "local_host_id": "HOST-B",
            "local_peer_id": "PEER-B",
            "state_dir": str(self.root / "state-b"),
            "trusted_hosts": [
                {
                    "host_id": "HOST-A",
                    "key_id": "host-a-v1",
                    "verification_key_ref": str(self.host_a_key),
                    "min_revision": 1,
                }
            ],
            "pull_destination_roots": [str(self.pull_root)],
            "ssh": {
                "known_hosts_ref": str(self.known_hosts),
                "sftp_executable_ref": str(self.sftp_executable),
                "connect_timeout_seconds": 10,
            },
        }

    def tearDown(self):
        self.temporary.cleanup()

    def entries(self, revision=1):
        return {
            "schema": ENTRIES_SCHEMA,
            "revision": revision,
            "paths": [
                {
                    "path_id": "service-credential-file",
                    "kind": "file",
                    "local_path": r"C:\ProgramData\example\service credential.json",
                    "remote_path": "/srv/credentials/service credential.json",
                    "endpoint_id": "tailscale-sftp",
                    "allowed_peer_ids": ["PEER-B"],
                    "direct_pull": True,
                    "description": "Exact host-local location; no file content.",
                }
            ],
        }

    def publish(self, revision=1):
        return TrustedPeerPathRegistry(self.publisher_config).publish(
            self.entries(revision)
        )

    def registry_path(self):
        return (
            self.yard
            / "hosts"
            / "HOST-A"
            / "trusted-peer-paths"
            / "registry.json"
        )


class TestPublishValidateResolve(TrustedPeerPathFixture):
    def test_publish_is_signed_atomic_and_own_slot_only(self):
        result = self.publish()
        self.assertEqual(result["status"], "published")
        self.assertEqual(Path(result["registry"]), self.registry_path())
        document = json.loads(self.registry_path().read_text(encoding="utf-8"))
        self.assertEqual(document["host_id"], "HOST-A")
        self.assertEqual(document["revision"], 1)
        self.assertEqual(document["signature"]["algorithm"], "hmac-sha256")
        self.assertNotIn("a" * 64, self.registry_path().read_text(encoding="utf-8"))
        self.assertFalse(list(self.registry_path().parent.glob("*.tmp")))

    def test_validate_list_and_resolve_authorized_path(self):
        self.publish()
        peer = TrustedPeerPathRegistry(self.peer_config)
        validated = peer.validate("HOST-A")
        self.assertEqual(validated["revision"], 1)
        listed = peer.list_paths()
        self.assertEqual(listed["count"], 1)
        resolved = peer.resolve("HOST-A", "service-credential-file")
        self.assertTrue(resolved["verified"])
        self.assertEqual(
            resolved["path"]["local_path"],
            r"C:\ProgramData\example\service credential.json",
        )
        self.assertEqual(resolved["endpoint"]["transport"], "sftp")

    def test_unknown_or_unauthorized_peer_fails_closed(self):
        self.publish()
        config = dict(self.peer_config)
        config["local_peer_id"] = "PEER-C"
        peer = TrustedPeerPathRegistry(config)
        self.assertEqual(peer.list_paths()["count"], 0)
        with self.assertRaisesRegex(TrustedPeerPathError, "not authorized"):
            peer.resolve("HOST-A", "service-credential-file")
        with self.assertRaisesRegex(TrustedPeerPathError, "not trusted"):
            peer.validate("HOST-C")

    def test_tampering_and_slot_substitution_fail(self):
        self.publish()
        document = json.loads(self.registry_path().read_text(encoding="utf-8"))
        document["paths"][0]["local_path"] = "/tampered"
        self.registry_path().write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(TrustedPeerPathError, "signature"):
            TrustedPeerPathRegistry(self.peer_config).validate("HOST-A")

        document["host_id"] = "HOST-A"
        host_b = self.yard / "hosts" / "HOST-B" / "trusted-peer-paths"
        host_b.mkdir(parents=True)
        (host_b / "registry.json").write_text(json.dumps(document), encoding="utf-8")
        config = dict(self.peer_config)
        config["trusted_hosts"] = [
            {
                "host_id": "HOST-B",
                "key_id": "host-a-v1",
                "verification_key_ref": str(self.host_a_key),
                "min_revision": 1,
            }
        ]
        with self.assertRaisesRegex(TrustedPeerPathError, "yard slot"):
            TrustedPeerPathRegistry(config).validate("HOST-B")

    def test_replay_and_same_revision_equivocation_fail(self):
        self.publish(1)
        old_registry = self.registry_path().read_bytes()
        peer = TrustedPeerPathRegistry(self.peer_config)
        peer.validate("HOST-A")
        self.publish(2)
        peer.validate("HOST-A")
        self.registry_path().write_bytes(old_registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "replay"):
            peer.validate("HOST-A")

    def test_publish_rejects_foreign_identity_and_traversal_fields(self):
        entries = self.entries()
        entries["host_id"] = "HOST-B"
        with self.assertRaisesRegex(TrustedPeerPathError, "unknown keys"):
            TrustedPeerPathRegistry(self.publisher_config).publish(entries)
        entries = self.entries()
        entries["paths"][0]["remote_path"] = "/srv/credentials/../private"
        with self.assertRaisesRegex(TrustedPeerPathError, "traversal-free"):
            TrustedPeerPathRegistry(self.publisher_config).publish(entries)

    def test_unknown_transport_and_command_injection_fail(self):
        config = json.loads(json.dumps(self.publisher_config))
        config["publisher"]["endpoints"][0]["transport"] = "scp"
        with self.assertRaisesRegex(TrustedPeerPathError, "only the sftp"):
            TrustedPeerPathRegistry(config)
        entries = self.entries()
        entries["paths"][0]["remote_path"] = "/safe/file\nput /etc/passwd"
        with self.assertRaisesRegex(TrustedPeerPathError, "SFTP path"):
            TrustedPeerPathRegistry(self.publisher_config).publish(entries)


class TestDatabaseBoundary(TrustedPeerPathFixture):
    def sqlite_entries(self, *, direct_pull=False, adapter="sqlite-transit-sync"):
        entries = self.entries()
        entries["paths"] = [
            {
                "path_id": "application-state",
                "kind": "database/sqlite",
                "local_path": "/var/lib/example/state.sqlite3",
                "remote_path": "/var/lib/example/state.sqlite3",
                "endpoint_id": "tailscale-sftp",
                "allowed_peer_ids": ["PEER-B"],
                "direct_pull": direct_pull,
                "adapter": adapter,
            }
        ]
        return entries

    def test_sqlite_registry_is_visible_but_never_directly_pullable(self):
        TrustedPeerPathRegistry(self.publisher_config).publish(self.sqlite_entries())
        peer = TrustedPeerPathRegistry(self.peer_config)
        resolution = peer.resolve("HOST-A", "application-state")
        self.assertEqual(resolution["path"]["adapter"], "sqlite-transit-sync")
        plan = peer.pull_plan(
            "HOST-A", "application-state", self.pull_root / "state.sqlite3"
        )
        self.assertFalse(plan.executable)
        self.assertIn("db-transit/<namespace>", plan.blocker)
        self.assertEqual(plan.argv, ())
        self.assertEqual(plan.batch_commands, ())
        with self.assertRaisesRegex(TrustedPeerPathError, "sqlite-transit-sync"):
            peer.pull(
                "HOST-A",
                "application-state",
                self.pull_root / "state.sqlite3",
                apply=True,
            )

    def test_sqlite_requires_adapter_and_direct_pull_false(self):
        with self.assertRaisesRegex(TrustedPeerPathError, "direct_pull=false"):
            TrustedPeerPathRegistry(self.publisher_config).publish(
                self.sqlite_entries(direct_pull=True)
            )
        with self.assertRaisesRegex(TrustedPeerPathError, "sqlite-transit-sync"):
            TrustedPeerPathRegistry(self.publisher_config).publish(
                self.sqlite_entries(adapter="generic-sftp")
            )

    def test_sqlite_suffixes_cannot_be_disguised_as_files(self):
        for suffix in (".db", ".sqlite", ".sqlite3", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                entries = self.entries()
                entries["paths"][0]["remote_path"] = f"/srv/state/data{suffix}"
                with self.assertRaisesRegex(TrustedPeerPathError, "database/sqlite"):
                    TrustedPeerPathRegistry(self.publisher_config).publish(entries)


class TestPullBoundary(TrustedPeerPathFixture):
    def setUp(self):
        super().setUp()
        self.publish()

    def test_pull_plan_is_shell_free_and_pins_known_hosts(self):
        plan = TrustedPeerPathRegistry(self.peer_config).pull_plan(
            "HOST-A",
            "service-credential-file",
            self.pull_root / "credential.json",
        )
        self.assertTrue(plan.executable)
        self.assertEqual(plan.argv[0], str(self.sftp_executable))
        self.assertEqual(plan.argv[plan.argv.index("-F") + 1], "none")
        self.assertIn("-oStrictHostKeyChecking=yes", plan.argv)
        self.assertIn("-oBatchMode=yes", plan.argv)
        self.assertIn(f"-oUserKnownHostsFile={self.known_hosts}", plan.argv)
        self.assertIn("-oGlobalKnownHostsFile=none", plan.argv)
        self.assertEqual(plan.argv[-1], "registry-reader@peer-a.example")
        self.assertNotIn("shell", " ".join(plan.argv).lower())

    @mock.patch("system_gap_master.trusted_peer_paths.subprocess.run")
    def test_pull_requires_apply_and_installs_without_overwrite(self, run):
        destination = self.pull_root / "credential.json"
        peer = TrustedPeerPathRegistry(self.peer_config)
        dry_run = peer.pull(
            "HOST-A", "service-credential-file", destination, apply=False
        )
        self.assertEqual(dry_run["status"], "dry-run")
        self.assertFalse(destination.exists())

        def fake_run(argv, **kwargs):
            self.assertFalse(kwargs["shell"])
            batch = Path(argv[argv.index("-b") + 1])
            (batch.parent / "download.part").write_bytes(b"credential-content")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        run.side_effect = fake_run
        result = peer.pull(
            "HOST-A", "service-credential-file", destination, apply=True
        )
        self.assertEqual(result["status"], "pulled")
        self.assertEqual(destination.read_bytes(), b"credential-content")
        with self.assertRaisesRegex(TrustedPeerPathError, "never overwrite"):
            peer.pull_plan("HOST-A", "service-credential-file", destination)

    def test_destination_outside_allowlist_fails(self):
        with self.assertRaisesRegex(TrustedPeerPathError, "outside configured"):
            TrustedPeerPathRegistry(self.peer_config).pull_plan(
                "HOST-A",
                "service-credential-file",
                self.root / "outside.json",
            )


class TestCli(TrustedPeerPathFixture):
    def _write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_publish_validate_list_resolve_and_pull_plan_cli(self):
        publisher_config = self._write_json(
            "publisher.json", self.publisher_config
        )
        entries = self._write_json("entries.json", self.entries())
        peer_config = self._write_json("peer.json", self.peer_config)
        commands = [
            [
                "publish",
                "--config",
                str(publisher_config),
                "--entries",
                str(entries),
            ],
            ["validate", "--config", str(peer_config), "--host-id", "HOST-A"],
            ["list", "--config", str(peer_config), "--host-id", "HOST-A"],
            [
                "resolve",
                "--config",
                str(peer_config),
                "--host-id",
                "HOST-A",
                "--path-id",
                "service-credential-file",
            ],
            [
                "pull-plan",
                "--config",
                str(peer_config),
                "--host-id",
                "HOST-A",
                "--path-id",
                "service-credential-file",
                "--destination",
                str(self.pull_root / "cli.json"),
            ],
        ]
        for command in commands:
            with self.subTest(command=command[0]), contextlib.redirect_stdout(
                io.StringIO()
            ):
                self.assertEqual(main(command), 0)


class TestPublicContracts(unittest.TestCase):
    def test_schemas_and_examples_are_machine_readable_and_neutral(self):
        repository = Path(__file__).resolve().parents[1]
        files = [
            repository / "schemas" / "trusted-peer-path-registry.schema.json",
            repository / "schemas" / "trusted-peer-path-entries.schema.json",
            repository
            / "schemas"
            / "trusted-peer-path-local-config.schema.json",
            repository
            / "examples"
            / "trusted-peer-paths.local-config.example.json",
            repository / "examples" / "trusted-peer-paths.entries.example.json",
            repository / "ellmos-module.v2.json",
        ]
        for path in files:
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in files
        ).lower()
        forbidden = [
            "lu" + "kas",
            "workstation" + "-lg",
            "asus" + "-gei",
            "one" + "drive",
        ]
        for value in forbidden:
            self.assertNotIn(value, combined)

    def test_sqlite_example_declares_r9_adapter_boundary(self):
        repository = Path(__file__).resolve().parents[1]
        entries = json.loads(
            (
                repository
                / "examples"
                / "trusted-peer-paths.entries.example.json"
            ).read_text(encoding="utf-8")
        )
        sqlite = next(
            item for item in entries["paths"] if item["kind"] == "database/sqlite"
        )
        self.assertFalse(sqlite["direct_pull"])
        self.assertEqual(sqlite["adapter"], "sqlite-transit-sync")


if __name__ == "__main__":
    unittest.main()
