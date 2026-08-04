from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system_gap_master.trusted_peer_paths import (
    TrustedPeerPathError,
    TrustedPeerPathRegistry,
)
from system_gap_master.trusted_peer_sftp_executor import (
    EXECUTOR_CONFIG_SCHEMA,
    GRANT_SCHEMA,
    TrustedPeerSftpError,
    TrustedPeerSftpExecutor,
    _openssh_verify,
    _windows_acl,
    _windows_current_user_sid,
)


PIN = "SHA256:" + "A" * 43


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def signed(value, *, key_id, reference):
    result = dict(value)
    result["signature_reference"] = {
        "algorithm": "external-ssh-signature",
        "key_id": key_id,
        "ref": reference,
        "payload_sha256": hashlib.sha256(canonical(value)).hexdigest(),
    }
    return result


class ExecutorFixture(unittest.TestCase):
    @staticmethod
    def harden(path):
        if os.name != "nt":
            path.chmod(0o700 if path.is_dir() else 0o600)
            return
        sid = _windows_current_user_sid()
        inheritance = "(OI)(CI)" if path.is_dir() else ""
        commands = (
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:{inheritance}F",
                f"*S-1-5-18:{inheritance}F",
                f"*S-1-5-32-544:{inheritance}F",
            ],
        )
        for command in commands:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "cannot create a private Windows ACL fixture: "
                    + result.stderr.decode(errors="replace")
                )
        trusted = {sid, "S-1-5-18", "S-1-5-32-544"}
        _, allowed = _windows_acl(path)
        for extra_sid in allowed - trusted:
            result = subprocess.run(
                ["icacls", str(path), "/remove:g", f"*{extra_sid}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "cannot remove a broad Windows ACL fixture entry: "
                    + result.stderr.decode(errors="replace")
                )
        owner, allowed = _windows_acl(path)
        # GitHub-hosted Windows runners may create the temporary tree with the
        # built-in Administrators group as owner.  Production validation
        # deliberately accepts that owner too; keep the fixture aligned while
        # still requiring an exact trusted-only allow-list.
        if owner not in {sid, "S-1-5-32-544"} or not allowed or not allowed.issubset(
            trusted
        ):
            raise RuntimeError(
                f"private Windows ACL fixture validation failed: {owner}, {allowed}"
            )

    @staticmethod
    def loosen(path):
        if os.name != "nt":
            path.chmod(0o755 if path.is_dir() else 0o644)
            return
        result = subprocess.run(
            ["icacls", str(path), "/grant", "*S-1-1-0:R"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "cannot loosen a Windows ACL fixture: "
                + result.stderr.decode(errors="replace")
            )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.yard = self.root / "yard"
        self.destination_root = self.root / "imports"
        self.state_root = self.root / "state"
        self.receipt_root = self.root / "receipts"
        self.credential_root = self.root / "credentials"
        for directory in (
            self.destination_root,
            self.state_root,
            self.state_root / "attempts",
            self.receipt_root,
            self.credential_root,
            self.yard,
            self.yard / "hosts",
            self.yard / "hosts" / "HOST-A",
            self.yard / "hosts" / "HOST-A" / "trusted-peer-paths",
        ):
            directory.mkdir(parents=True, exist_ok=True)
            self.harden(directory)
        self.identity = self.credential_root / "identity"
        self.known_hosts = self.credential_root / "known_hosts"
        self.registry_signature = self.credential_root / "registry.sig"
        self.grant_signature = self.credential_root / "grant.sig"
        self.allowed_signers = self.credential_root / "allowed_signers"
        self.verifier_tool = self.credential_root / "ssh-keygen-fixture"
        for path in (
            self.identity,
            self.known_hosts,
            self.registry_signature,
            self.grant_signature,
            self.allowed_signers,
            self.verifier_tool,
        ):
            path.write_text("fixture", encoding="utf-8")
            self.harden(path)
        self.now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
        self.registry_ref = "urn:test:registry-signature"
        self.grant_ref = "urn:test:grant-signature"
        self.registry = signed(
            {
                "schema": "system-gap.trusted-peer-paths.registry.v2",
                "host_id": "HOST-A",
                "revision": 7,
                "published_at": self.iso(self.now - timedelta(minutes=1)),
                "expires_at": self.iso(self.now + timedelta(hours=1)),
                "endpoints": [
                    {
                        "endpoint_id": "trusted-sftp",
                        "transport": "sftp",
                        "network_label": "private-overlay",
                        "host": "100.80.66.10",
                        "port": 22,
                        "username": "registry-reader",
                        "read_only": True,
                        "known_host_pin": PIN,
                    }
                ],
                "paths": [
                    {
                        "path_id": "service-file",
                        "kind": "file",
                        "metadata_type": "path-location",
                        "content_included": False,
                        "remote_path": "/srv/private/service.json",
                        "endpoint_id": "trusted-sftp",
                        "allowed_peer_ids": ["PEER-B"],
                        "direct_pull": True,
                    }
                ],
            },
            key_id="host-a-v1",
            reference=self.registry_ref,
        )
        self.registry_path = (
            self.yard / "hosts" / "HOST-A" / "trusted-peer-paths" / "registry.json"
        )
        self.write_json(self.registry_path, self.registry)
        self.planner_config = {
            "schema": "system-gap.trusted-peer-paths.local-config.v2",
            "yard_root": str(self.yard),
            "local_host_id": "HOST-B",
            "local_peer_id": "PEER-B",
            "trusted_hosts": [
                {
                    "host_id": "HOST-A",
                    "min_revision": 7,
                    "signature_algorithm": "external-ssh-signature",
                    "key_id": "host-a-v1",
                    "signature_reference": self.registry_ref,
                    "allowed_network_labels": ["private-overlay"],
                    "allowed_remote_paths": ["/srv/private/service.json"],
                    "known_host_pins": [
                        {"endpoint_id": "trusted-sftp", "sha256": PIN}
                    ],
                }
            ],
            "pull_destination_roots": [str(self.destination_root)],
            "max_registry_age_seconds": 3600,
            "max_registry_ttl_seconds": 7200,
        }
        executable = self.verifier_tool
        self.executor_config = {
            "schema": EXECUTOR_CONFIG_SCHEMA,
            "state_root": str(self.state_root),
            "receipt_root": str(self.receipt_root),
            "credential_roots": [str(self.credential_root)],
            "ssh_keygen": {
                "path": str(executable),
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            },
            "required_paramiko_version": "5.0.0",
            "auth_profiles": [
                {
                    "host_id": "HOST-A",
                    "endpoint_id": "trusted-sftp",
                    "username": "registry-reader",
                    "identity_file": str(self.identity),
                    "known_hosts_file": str(self.known_hosts),
                    "remote_account_mode": "read-only",
                    "network_label": "private-overlay",
                    "remote_host": "100.80.66.10",
                    "source_address": "100.108.34.112",
                    "allowed_remote_cidrs": ["100.64.0.0/10"],
                    "allowed_source_cidrs": ["100.64.0.0/10"],
                }
            ],
            "signature_verifiers": [
                {
                    "purpose": "registry",
                    "algorithm": "external-ssh-signature",
                    "key_id": "host-a-v1",
                    "reference": self.registry_ref,
                    "signer_identity": "host-a",
                    "namespace": "system-gap-registry",
                    "signature_file": str(self.registry_signature),
                    "allowed_signers_file": str(self.allowed_signers),
                },
                {
                    "purpose": "grant",
                    "algorithm": "external-ssh-signature",
                    "key_id": "operator-v1",
                    "reference": self.grant_ref,
                    "signer_identity": "operator",
                    "namespace": "system-gap-transfer-grant",
                    "signature_file": str(self.grant_signature),
                    "allowed_signers_file": str(self.allowed_signers),
                },
            ],
            "max_transfer_bytes": 1024,
            "max_grant_ttl_seconds": 900,
            "connect_timeout_seconds": 5,
        }
        self.signature_calls = []
        self.transport_calls = []

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def iso(value):
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def verifier(self, payload, verifier, tool):
        self.signature_calls.append((payload, verifier["purpose"], dict(tool)))

    def transport(self, endpoint, profile, remote_path, sink, max_bytes, timeout, version):
        self.transport_calls.append(
            (dict(endpoint), dict(profile), remote_path, max_bytes, timeout, version)
        )
        payload = b'{"credential":"fixture"}'
        if len(payload) > max_bytes:
            raise TrustedPeerSftpError("fixture exceeds max")
        sink.write(payload)
        return len(payload)

    def executor(self, *, transport=None, verifier=None, config=None):
        planner = TrustedPeerPathRegistry(self.planner_config, clock=lambda: self.now)
        return TrustedPeerSftpExecutor(
            planner,
            config or self.executor_config,
            clock=lambda: self.now,
            signature_verifier=verifier or self.verifier,
            transport=transport or self.transport,
        )

    def make_grant(self, destination, **changes):
        planner = TrustedPeerPathRegistry(self.planner_config, clock=lambda: self.now)
        plan = planner.pull_plan("HOST-A", "service-file", destination).as_dict()
        unsigned = {
            "schema": GRANT_SCHEMA,
            "grant_id": "grant-001",
            "host_id": "HOST-A",
            "peer_id": "PEER-B",
            "endpoint_id": "trusted-sftp",
            "path_id": "service-file",
            "destination": str(destination),
            "network_label": "private-overlay",
            "registry_sha256": plan["registry"]["sha256"],
            "registry_revision": 7,
            "plan_id": plan["plan_id"],
            "not_before": self.iso(self.now - timedelta(minutes=1)),
            "expires_at": self.iso(self.now + timedelta(minutes=5)),
            "one_shot_id": "once-001",
            "max_bytes": 100,
        }
        unsigned.update(changes)
        return signed(unsigned, key_id="operator-v1", reference=self.grant_ref)

    def grant_file(self, destination, **changes):
        grant = self.make_grant(destination, **changes)
        path = self.root / f"grant-{len(list(self.root.glob('grant-*.json')))}.json"
        self.write_json(path, grant)
        self.harden(path)
        return path


class TestTrustedPeerSftpExecutor(ExecutorFixture):
    def test_real_openssh_detached_signature_verification(self):
        ssh_keygen = shutil.which("ssh-keygen")
        if ssh_keygen is None:
            self.skipTest("ssh-keygen unavailable")
        key = self.credential_root / "signing-key"
        generated = subprocess.run(
            [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if generated.returncode != 0:
            self.skipTest("ssh-keygen cannot generate an Ed25519 fixture")
        payload = b'{"signed":"payload"}'
        payload_path = self.credential_root / "payload.json"
        payload_path.write_bytes(payload)
        signed_result = subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "sign",
                "-f",
                str(key),
                "-n",
                "system-gap-transfer-grant",
                str(payload_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(signed_result.returncode, 0, signed_result.stderr.decode())
        signature = Path(str(payload_path) + ".sig")
        allowed = self.credential_root / "real-allowed-signers"
        allowed.write_text(
            "operator " + Path(str(key) + ".pub").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        verifier = {
            "algorithm": "external-ssh-signature",
            "allowed_signers_file": str(allowed),
            "signer_identity": "operator",
            "namespace": "system-gap-transfer-grant",
            "signature_file": str(signature),
        }
        executable = Path(ssh_keygen)
        tool = {
            "path": str(executable),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        _openssh_verify(payload, verifier, tool)
        with self.assertRaisesRegex(TrustedPeerSftpError, "verification failed"):
            _openssh_verify(payload + b"tampered", verifier, tool)

    def test_success_commits_without_overwrite_and_writes_redacted_receipt(self):
        destination = self.destination_root / "service.json"
        result = self.executor().execute(
            "HOST-A", "service-file", destination, self.grant_file(destination)
        ).as_dict()
        self.assertEqual(destination.read_bytes(), b'{"credential":"fixture"}')
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["bytes"], destination.stat().st_size)
        self.assertEqual(len(self.signature_calls), 2)
        self.assertEqual(len(self.transport_calls), 1)
        receipts = list(self.receipt_root.glob("*.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        serialized = json.dumps(receipt)
        self.assertNotIn(str(self.identity), serialized)
        self.assertNotIn(str(self.known_hosts), serialized)
        self.assertFalse(receipt["credential_paths_included"])
        self.assertFalse(receipt["content_included"])
        self.assertEqual(list(self.destination_root.glob("*.part")), [])

    def test_replay_is_rejected_before_second_network_call(self):
        first = self.destination_root / "first.json"
        grant = self.grant_file(first)
        self.executor().execute("HOST-A", "service-file", first, grant)
        first.unlink()
        with self.assertRaisesRegex(TrustedPeerSftpError, "one-shot state"):
            self.executor().execute("HOST-A", "service-file", first, grant)
        self.assertEqual(len(self.transport_calls), 1)

    def test_wrong_binding_never_contacts_transport(self):
        destination = self.destination_root / "service.json"
        grant = self.grant_file(destination, peer_id="PEER-C")
        with self.assertRaisesRegex(TrustedPeerSftpError, "does not bind peer_id"):
            self.executor().execute("HOST-A", "service-file", destination, grant)
        self.assertEqual(self.transport_calls, [])

    def test_expired_grant_never_contacts_transport(self):
        destination = self.destination_root / "service.json"
        grant = self.grant_file(
            destination,
            not_before=self.iso(self.now - timedelta(minutes=10)),
            expires_at=self.iso(self.now - timedelta(minutes=1)),
        )
        with self.assertRaisesRegex(TrustedPeerSftpError, "not currently valid"):
            self.executor().execute("HOST-A", "service-file", destination, grant)
        self.assertEqual(self.transport_calls, [])

    def test_registry_signature_failure_precedes_transport(self):
        destination = self.destination_root / "service.json"
        calls = []

        def verifier(payload, verifier_config, tool):
            calls.append(verifier_config["purpose"])
            if verifier_config["purpose"] == "registry":
                raise TrustedPeerSftpError("bad registry signature")

        with self.assertRaisesRegex(TrustedPeerSftpError, "bad registry signature"):
            self.executor(verifier=verifier).execute(
                "HOST-A", "service-file", destination, self.grant_file(destination)
            )
        self.assertEqual(calls, ["grant", "registry"])
        self.assertEqual(self.transport_calls, [])

    def test_registry_swap_between_plan_and_signature_is_rejected(self):
        destination = self.destination_root / "service.json"

        def swapping_verifier(payload, verifier_config, tool):
            if verifier_config["purpose"] == "grant":
                unsigned = dict(self.registry)
                del unsigned["signature_reference"]
                unsigned["revision"] = 8
                replacement = signed(
                    unsigned,
                    key_id="host-a-v1",
                    reference=self.registry_ref,
                )
                self.write_json(self.registry_path, replacement)

        with self.assertRaisesRegex(TrustedPeerSftpError, "does not match the pull plan"):
            self.executor(verifier=swapping_verifier).execute(
                "HOST-A", "service-file", destination, self.grant_file(destination)
            )
        self.assertEqual(self.transport_calls, [])

    def test_existing_destination_fails_before_signature_or_transport(self):
        destination = self.destination_root / "service.json"
        grant = self.grant_file(destination)
        destination.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(TrustedPeerPathError, "already exists"):
            self.executor().execute("HOST-A", "service-file", destination, grant)
        self.assertEqual(self.signature_calls, [])
        self.assertEqual(self.transport_calls, [])

    def test_missing_exact_auth_profile_fails_before_transport(self):
        destination = self.destination_root / "service.json"
        config = json.loads(json.dumps(self.executor_config))
        config["auth_profiles"][0]["username"] = "another-reader"
        with self.assertRaisesRegex(TrustedPeerSftpError, "no exact.*auth profile"):
            self.executor(config=config).execute(
                "HOST-A", "service-file", destination, self.grant_file(destination)
            )
        self.assertEqual(self.transport_calls, [])

    def test_transfer_failure_removes_staging_and_consumes_one_shot(self):
        destination = self.destination_root / "service.json"
        grant = self.grant_file(destination)

        def broken(*args):
            args[3].write(b"partial")
            raise TrustedPeerSftpError("interrupted")

        with self.assertRaisesRegex(TrustedPeerSftpError, "interrupted"):
            self.executor(transport=broken).execute(
                "HOST-A", "service-file", destination, grant
            )
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.destination_root.glob("*.part")), [])
        self.assertEqual(len(list((self.state_root / "attempts").glob("*.json"))), 1)
        receipts = list(self.receipt_root.glob("*.json"))
        self.assertEqual(len(receipts), 1)
        failed = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "transfer-failed")
        self.assertNotIn("identity_file", json.dumps(failed))

    def test_transport_cannot_claim_a_different_byte_count(self):
        destination = self.destination_root / "service.json"

        def dishonest(endpoint, profile, remote_path, sink, *rest):
            sink.write(b"abc")
            return 4

        with self.assertRaisesRegex(TrustedPeerSftpError, "byte count"):
            self.executor(transport=dishonest).execute(
                "HOST-A", "service-file", destination, self.grant_file(destination)
            )
        self.assertFalse(destination.exists())

    def test_destination_parent_swap_attempt_during_transfer_fails_closed(self):
        parent = self.destination_root / "target"
        parent.mkdir()
        self.harden(parent)
        destination = parent / "service.json"
        original = self.destination_root / "target-original"

        def swapping(endpoint, profile, remote_path, sink, *rest):
            payload = b"fixture"
            sink.write(payload)
            parent.rename(original)
            parent.mkdir()
            self.harden(parent)
            return len(payload)

        if os.name == "nt":
            # Windows refuses to rename a directory containing the open pinned
            # stage.  The transport exception still consumes the grant and
            # securely deletes the stage through its open handle.
            with self.assertRaises(PermissionError):
                self.executor(transport=swapping).execute(
                    "HOST-A", "service-file", destination, self.grant_file(destination)
                )
        else:
            with self.assertRaisesRegex(TrustedPeerSftpError, "parent changed"):
                self.executor(transport=swapping).execute(
                    "HOST-A", "service-file", destination, self.grant_file(destination)
                )
        self.assertFalse(destination.exists())
        self.assertFalse((original / "service.json").exists())
        self.assertEqual(list(parent.glob("*.part")), [])
        if original.exists():
            self.assertEqual(list(original.glob("*.part")), [])

    def test_credential_file_must_remain_under_local_credential_root(self):
        config = json.loads(json.dumps(self.executor_config))
        outside = self.root / "outside-key"
        outside.write_text("fixture", encoding="utf-8")
        self.harden(outside)
        config["auth_profiles"][0]["identity_file"] = str(outside)
        with self.assertRaisesRegex(TrustedPeerSftpError, "outside credential_roots"):
            self.executor(config=config)

    def test_post_init_acl_changes_fail_before_network_or_reservation(self):
        executor = self.executor()
        targets = (
            (self.credential_root, "credential_roots"),
            (self.allowed_signers, "allowed_signers_file"),
            (self.state_root / "attempts", "state_root/attempts"),
            (self.receipt_root, "receipt_root"),
        )
        for index, (target, expected) in enumerate(targets):
            with self.subTest(target=target):
                destination = self.destination_root / f"revalidation-{index}.json"
                grant = self.grant_file(
                    destination,
                    grant_id=f"grant-revalidation-{index}",
                    one_shot_id=f"once-revalidation-{index}",
                )
                self.loosen(target)
                try:
                    with self.assertRaisesRegex(
                        TrustedPeerSftpError, expected
                    ):
                        executor.execute(
                            "HOST-A", "service-file", destination, grant
                        )
                finally:
                    self.harden(target)
        self.assertEqual(self.transport_calls, [])
        self.assertEqual(list((self.state_root / "attempts").glob("*.json")), [])

    def test_source_contains_no_shell_or_scp_execution(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "system_gap_master"
            / "trusted_peer_sftp_executor.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("scp", source.lower())
        self.assertNotIn("exec_command", source)

    def test_new_schemas_examples_and_module_manifest_are_json_and_neutral(self):
        repository = Path(__file__).resolve().parents[1]
        files = [
            repository / "schemas" / "trusted-peer-sftp-executor-config.schema.json",
            repository / "schemas" / "trusted-peer-transfer-grant.schema.json",
            repository / "schemas" / "trusted-peer-transfer-receipt.schema.json",
            repository / "examples" / "trusted-peer-sftp-executor.config.example.json",
            repository / "examples" / "trusted-peer-transfer-grant.example.json",
            repository / "ellmos-module.v2.json",
        ]
        combined = []
        for path in files:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                json.loads(text)
                combined.append(text.lower())
        release_surface = "\n".join(combined)
        for forbidden in ("workstation-lg", "asus-gei", "onedrive", "lukas"):
            self.assertNotIn(forbidden, release_surface)


if __name__ == "__main__":
    unittest.main()
