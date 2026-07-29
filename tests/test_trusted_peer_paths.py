import contextlib
import io
import json
import os
import stat
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
                "max_download_bytes": 1048576,
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
                "max_download_bytes": 1048576,
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

    def publisher_state_path(self):
        return (
            Path(self.publisher_config["state_dir"])
            / "trusted-peer-paths"
            / "HOST-A.json"
        )

    def windows_short_path(self, path):
        if os.name != "nt":
            self.skipTest("Windows 8.3 path test")
        buffer = __import__("ctypes").create_unicode_buffer(32768)
        length = __import__("ctypes").windll.kernel32.GetShortPathNameW(
            str(path), buffer, len(buffer)
        )
        if not length or length >= len(buffer):
            self.skipTest("Windows short-path alias unavailable")
        short_path = Path(buffer.value)
        if os.path.normcase(str(short_path)) == os.path.normcase(str(path)):
            self.skipTest("Windows short-path alias is identical")
        return short_path


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

    def test_publish_checks_strict_highest_seen_state_before_yard_write(self):
        self.publish(1)
        original = self.registry_path().read_bytes()
        state_path = self.publisher_state_path()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["revision"] = 3
        state["registry_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")

        with self.assertRaisesRegex(TrustedPeerPathError, "highest-seen"):
            self.publish(2)
        self.assertEqual(self.registry_path().read_bytes(), original)

    def test_revision_state_tampering_fails_before_yard_write(self):
        for revision, digest in (
            (0, "0" * 64),
            (True, "0" * 64),
            (1, "A" * 64),
            (1, 123),
        ):
            with self.subTest(revision=revision, digest=digest):
                if self.registry_path().exists():
                    self.registry_path().unlink()
                self.publish(1)
                original = self.registry_path().read_bytes()
                state_path = self.publisher_state_path()
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["revision"] = revision
                state["registry_sha256"] = digest
                state_path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaisesRegex(
                    TrustedPeerPathError, "validation state"
                ):
                    self.publish(2)
                self.assertEqual(self.registry_path().read_bytes(), original)
                state_path.unlink()

    def test_ids_are_strictly_typed_canonical_and_filesystem_safe(self):
        for value in (123, "Mixed-Case", "con", "lpt1.json", "path-id."):
            with self.subTest(path_id=value):
                entries = self.entries()
                entries["paths"][0]["path_id"] = value
                with self.assertRaises(TrustedPeerPathError):
                    TrustedPeerPathRegistry(self.publisher_config).publish(entries)
        entries = self.entries()
        entries["paths"][0]["allowed_peer_ids"] = [7]
        with self.assertRaisesRegex(TrustedPeerPathError, "must be a string"):
            TrustedPeerPathRegistry(self.publisher_config).publish(entries)

    def test_host_ids_require_uppercase_and_reject_alias_names_before_guard(self):
        for host_id in ("host-a", "HOST-A.", "HOST-A...", "CON", "LPT1.JSON"):
            with self.subTest(host_id=host_id):
                config = json.loads(json.dumps(self.publisher_config))
                config["local_host_id"] = host_id
                state_dir = Path(config["state_dir"])
                with self.assertRaises(TrustedPeerPathError):
                    TrustedPeerPathRegistry(config)
                self.assertFalse(state_dir.exists())
                self.assertFalse((self.yard / "hosts" / host_id).exists())


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

    def test_ntfs_aliases_ads_and_devices_fail_before_sqlite_classification(self):
        unsafe_paths = (
            r"C:\data\state.sqlite3:shadow",
            r"C:\data\state.sqlite3.",
            r"C:\data\state.sqlite3 ",
            r"C:\data\CON.txt",
            r"C:\data\LPT1.json",
        )
        for path in unsafe_paths:
            with self.subTest(path=path):
                entries = self.entries()
                entries["paths"][0]["local_path"] = path
                with self.assertRaisesRegex(TrustedPeerPathError, "NTFS alias"):
                    TrustedPeerPathRegistry(self.publisher_config).publish(entries)

    def test_existing_windows_sqlite_short_alias_cannot_enable_direct_pull(self):
        sqlite_path = self.root / "state database.sqlite3"
        sqlite_path.write_bytes(b"SQLite format 3\0")
        short_path = self.windows_short_path(sqlite_path)
        entries = self.entries()
        entries["paths"][0]["local_path"] = str(short_path)
        entries["paths"][0]["remote_path"] = "/srv/state/application.bin"
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
        self.assertIn(
            f"-oUserKnownHostsFile={self.known_hosts.as_posix()}", plan.argv
        )
        self.assertIn("-oGlobalKnownHostsFile=none", plan.argv)
        self.assertEqual(plan.argv[-1], "registry-reader@peer-a.example")
        self.assertNotIn("shell", " ".join(plan.argv).lower())

    @mock.patch("system_gap_master.trusted_peer_paths.subprocess.Popen")
    def test_pull_requires_apply_and_installs_without_overwrite(self, popen):
        destination = self.pull_root / "credential.json"
        peer = TrustedPeerPathRegistry(self.peer_config)
        dry_run = peer.pull(
            "HOST-A", "service-credential-file", destination, apply=False
        )
        self.assertEqual(dry_run["status"], "dry-run")
        self.assertFalse(destination.exists())

        def fake_popen(argv, **kwargs):
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertTrue(Path(kwargs["cwd"]).is_dir())
            batch = Path(argv[argv.index("-b") + 1])
            (batch.parent / "download.part").write_bytes(b"credential-content")
            process = mock.Mock()
            process.poll.return_value = 0
            return process

        popen.side_effect = fake_popen
        result = peer.pull(
            "HOST-A", "service-credential-file", destination, apply=True
        )
        self.assertEqual(result["status"], "pulled")
        self.assertEqual(destination.read_bytes(), b"credential-content")
        self.assertEqual(
            result["sha256"],
            __import__("hashlib").sha256(b"credential-content").hexdigest(),
        )
        with self.assertRaisesRegex(TrustedPeerPathError, "never overwrite"):
            peer.pull_plan("HOST-A", "service-credential-file", destination)

    def test_hardlink_unavailable_fails_without_visible_destination(self):
        source = self.pull_root / "source.part"
        source.write_bytes(b"credential-content")
        destination = self.pull_root / "atomic.json"
        peer = TrustedPeerPathRegistry(self.peer_config)
        with (
            mock.patch(
                "system_gap_master.trusted_peer_paths.os.link",
                side_effect=OSError("unsupported"),
            ),
            self.assertRaisesRegex(TrustedPeerPathError, "atomic no-replace"),
        ):
            peer._install_no_overwrite(source, destination)
        self.assertFalse(destination.exists())

    def test_posix_install_restricts_staging_and_destination_to_0600(self):
        if os.name == "nt":
            self.skipTest("POSIX mode-bit regression")
        source = self.pull_root / "mode.part"
        source.write_bytes(b"credential-content")
        source.chmod(0o644)
        destination = self.pull_root / "mode.json"
        TrustedPeerPathRegistry(self.peer_config)._install_no_overwrite(
            source, destination
        )
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    @mock.patch("system_gap_master.trusted_peer_paths.subprocess.Popen")
    def test_pull_binds_remote_path_and_endpoint_to_one_resolution(self, popen):
        peer = TrustedPeerPathRegistry(self.peer_config)
        revision_one = peer.resolve("HOST-A", "service-credential-file")
        revision_two = json.loads(json.dumps(revision_one))
        revision_two["revision"] = 2
        revision_two["endpoint"]["host"] = "changed.example"
        destination = self.pull_root / "race.json"
        observed = {}

        def fake_popen(argv, **kwargs):
            observed["argv"] = argv
            batch = Path(argv[argv.index("-b") + 1])
            (batch.parent / "download.part").write_bytes(b"credential-content")
            process = mock.Mock()
            process.poll.return_value = 0
            return process

        popen.side_effect = fake_popen
        with mock.patch.object(
            peer, "resolve", side_effect=[revision_one, revision_two]
        ) as resolve:
            peer.pull(
                "HOST-A",
                "service-credential-file",
                destination,
                apply=True,
            )
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(observed["argv"][-1], "registry-reader@peer-a.example")

    @mock.patch("system_gap_master.trusted_peer_paths.subprocess.Popen")
    def test_download_size_cap_fails_before_install(self, popen):
        config = json.loads(json.dumps(self.peer_config))
        config["ssh"]["max_download_bytes"] = 4
        peer = TrustedPeerPathRegistry(config)
        destination = self.pull_root / "oversize.json"

        def fake_popen(argv, **kwargs):
            batch = Path(argv[argv.index("-b") + 1])
            (batch.parent / "download.part").write_bytes(b"12345")
            process = mock.Mock()
            process.poll.return_value = 0
            return process

        popen.side_effect = fake_popen
        with self.assertRaisesRegex(TrustedPeerPathError, "max_download_bytes"):
            peer.pull(
                "HOST-A",
                "service-credential-file",
                destination,
                apply=True,
            )
        self.assertFalse(destination.exists())

    def test_known_hosts_open_ssh_tokens_and_whitespace_fail_closed(self):
        for name in ("known hosts", "%h-known-hosts", "${HOME}-known-hosts"):
            with self.subTest(name=name):
                unsafe = self.keys / name
                unsafe.write_text(
                    "peer-a.example ssh-ed25519 AAAATESTONLY\n",
                    encoding="utf-8",
                )
                config = json.loads(json.dumps(self.peer_config))
                config["ssh"]["known_hosts_ref"] = str(unsafe)
                with self.assertRaisesRegex(
                    TrustedPeerPathError, "OpenSSH tokens"
                ):
                    TrustedPeerPathRegistry(config)

    def test_windows_short_aliases_work_without_weakening_reparse_checks(self):
        short_base = self.windows_short_path(self.root)
        publisher_config = json.loads(json.dumps(self.publisher_config))
        publisher_config["yard_root"] = str(short_base / "yard")
        publisher_config["state_dir"] = str(short_base / "state-short-publisher")
        publisher_config["pull_destination_roots"] = [str(short_base / "pulls")]
        publisher_config["publisher"]["signing_key_ref"] = str(
            short_base / "keys" / "host-a.hmac"
        )
        publisher_config["ssh"]["known_hosts_ref"] = str(
            short_base / "keys" / "known_hosts"
        )
        publisher_config["ssh"]["sftp_executable_ref"] = str(
            short_base / "bin" / "sftp"
        )
        result = TrustedPeerPathRegistry(publisher_config).publish(self.entries(2))
        self.assertEqual(result["revision"], 2)

        peer_config = json.loads(json.dumps(self.peer_config))
        peer_config["yard_root"] = str(short_base / "yard")
        peer_config["state_dir"] = str(short_base / "state-short-peer")
        peer_config["pull_destination_roots"] = [str(short_base / "pulls")]
        peer_config["trusted_hosts"][0]["verification_key_ref"] = str(
            short_base / "keys" / "host-a.hmac"
        )
        peer_config["ssh"]["known_hosts_ref"] = str(
            short_base / "keys" / "known_hosts"
        )
        peer_config["ssh"]["sftp_executable_ref"] = str(
            short_base / "bin" / "sftp"
        )
        plan = TrustedPeerPathRegistry(peer_config).pull_plan(
            "HOST-A",
            "service-credential-file",
            short_base / "pulls" / "short.json",
        )
        self.assertTrue(plan.executable)

    def test_windows_yard_junction_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows junction test")
        junction = self.root / "yard-junction"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(self.yard)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            self.skipTest(f"junction creation unavailable: {created.stderr}")
        config = json.loads(json.dumps(self.publisher_config))
        config["yard_root"] = str(junction)
        with self.assertRaisesRegex(
            TrustedPeerPathError, "symlink, junction or reparse"
        ):
            TrustedPeerPathRegistry(config)

    def test_destination_outside_allowlist_fails(self):
        with self.assertRaisesRegex(TrustedPeerPathError, "outside configured"):
            TrustedPeerPathRegistry(self.peer_config).pull_plan(
                "HOST-A",
                "service-credential-file",
                self.root / "outside.json",
            )

    def test_symlink_destination_parent_fails(self):
        real = self.pull_root / "real"
        real.mkdir()
        linked = self.pull_root / "linked"
        try:
            linked.symlink_to(real, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with self.assertRaisesRegex(
            TrustedPeerPathError, "symlink, junction or reparse"
        ):
            TrustedPeerPathRegistry(self.peer_config).pull_plan(
                "HOST-A",
                "service-credential-file",
                linked / "credential.json",
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

    def test_output_is_host_local_and_never_overwritten(self):
        self.publish()
        peer_config = self._write_json("peer.json", self.peer_config)
        output = self.root / "validation-result.json"
        self.assertEqual(
            main(
                [
                    "validate",
                    "--config",
                    str(peer_config),
                    "--host-id",
                    "HOST-A",
                    "--output",
                    str(output),
                ]
            ),
            0,
        )
        original = output.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "validate",
                        "--config",
                        str(peer_config),
                        "--host-id",
                        "HOST-A",
                        "--output",
                        str(output),
                    ]
                ),
                2,
            )
        self.assertEqual(output.read_bytes(), original)

    def test_publish_output_cannot_overwrite_or_enter_any_yard_slot(self):
        publisher_config = self._write_json(
            "publisher.json", self.publisher_config
        )
        entries = self._write_json("entries.json", self.entries())
        foreign = self.yard / "hosts" / "HOST-B" / "trusted-peer-paths"
        foreign.mkdir(parents=True)
        victim = foreign / "victim.json"
        victim.write_text("foreign-content", encoding="utf-8")
        for output in (victim, foreign / "new-result.json"):
            with self.subTest(output=output), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    main(
                        [
                            "publish",
                            "--config",
                            str(publisher_config),
                            "--entries",
                            str(entries),
                            "--output",
                            str(output),
                        ]
                    ),
                    2,
                )
        self.assertEqual(victim.read_text(encoding="utf-8"), "foreign-content")
        self.assertFalse((foreign / "new-result.json").exists())
        self.assertFalse(self.registry_path().exists())

    @mock.patch("system_gap_master.trusted_peer_paths.subprocess.Popen")
    def test_pull_output_cannot_alias_destination_or_sensitive_files(self, popen):
        self.publish()
        peer_config = self._write_json("peer.json", self.peer_config)
        destination = self.pull_root / "same.json"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "pull",
                        "--config",
                        str(peer_config),
                        "--host-id",
                        "HOST-A",
                        "--path-id",
                        "service-credential-file",
                        "--destination",
                        str(destination),
                        "--apply",
                        "--output",
                        str(destination),
                    ]
                ),
                2,
            )
        popen.assert_not_called()
        self.assertFalse(destination.exists())

        for protected in (peer_config, self.host_a_key, self.known_hosts):
            before = protected.read_bytes()
            with self.subTest(protected=protected), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    main(
                        [
                            "validate",
                            "--config",
                            str(peer_config),
                            "--host-id",
                            "HOST-A",
                            "--output",
                            str(protected),
                        ]
                    ),
                    2,
                )
            self.assertEqual(protected.read_bytes(), before)

    def test_duplicate_json_keys_and_nonfinite_numbers_fail_closed(self):
        publisher_config = self._write_json(
            "publisher.json", self.publisher_config
        )
        entries = self.entries()
        entries_text = json.dumps(entries)
        remote = '"remote_path": "/srv/credentials/service credential.json"'
        duplicate_remote = entries_text.replace(
            remote,
            remote + ', "remote_path": "/srv/credentials/evil.json"',
            1,
        )
        duplicate_entries = self.root / "duplicate-entries.json"
        duplicate_entries.write_text(duplicate_remote, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "publish",
                        "--config",
                        str(publisher_config),
                        "--entries",
                        str(duplicate_entries),
                    ]
                ),
                2,
            )
        self.assertFalse(self.registry_path().exists())

        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                nonfinite = self.root / f"{constant.replace('-', 'minus')}.json"
                nonfinite.write_text(
                    json.dumps(self.entries()).replace(
                        '"revision": 1', f'"revision": {constant}', 1
                    ),
                    encoding="utf-8",
                )
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        main(
                            [
                                "publish",
                                "--config",
                                str(publisher_config),
                                "--entries",
                                str(nonfinite),
                            ]
                        ),
                        2,
                    )
                self.assertFalse(self.registry_path().exists())

        self.publish()
        peer_config = self._write_json("peer.json", self.peer_config)
        registry_text = self.registry_path().read_text(encoding="utf-8")
        registry_text = registry_text.replace(
            '"host_id": "HOST-A",',
            '"host_id": "HOST-A",\n  "host_id": "HOST-B",',
            1,
        )
        self.registry_path().write_text(registry_text, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "validate",
                        "--config",
                        str(peer_config),
                        "--host-id",
                        "HOST-A",
                    ]
                ),
                2,
            )


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

    def test_schema_matches_runtime_id_sqlite_and_download_boundaries(self):
        repository = Path(__file__).resolve().parents[1]
        registry_schema = json.loads(
            (
                repository
                / "schemas"
                / "trusted-peer-path-registry.schema.json"
            ).read_text(encoding="utf-8")
        )
        local_schema = json.loads(
            (
                repository
                / "schemas"
                / "trusted-peer-path-local-config.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            registry_schema["properties"]["host_id"]["$ref"],
            "#/$defs/hostId",
        )
        self.assertIn(
            "peerId",
            registry_schema["$defs"],
        )
        sqlite_rule = registry_schema["$defs"]["path"]["allOf"][0]["then"]
        self.assertFalse(sqlite_rule["properties"]["direct_pull"]["const"])
        self.assertEqual(
            sqlite_rule["properties"]["adapter"]["const"],
            "sqlite-transit-sync",
        )
        self.assertIn(
            "max_download_bytes",
            local_schema["properties"]["ssh"]["required"],
        )


if __name__ == "__main__":
    unittest.main()
