import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system_gap_master.trusted_peer_paths import (
    LOCAL_CONFIG_SCHEMA,
    RECEIPT_SCHEMA,
    REGISTRY_SCHEMA,
    TrustedPeerPathError,
    TrustedPeerPathRegistry,
    main,
)


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
TEST_PIN = "SHA256:" + ("A" * 43)
TEST_SIGNATURE_REF = "urn:system-gap:signature-key:host-a:revision-3"


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class TrustedPeerPathFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.yard = self.root / "yard"
        self.registry_dir = self.yard / "hosts" / "HOST-A" / "trusted-peer-paths"
        self.registry_dir.mkdir(parents=True)
        self.pull_root = self.root / "imports"
        self.pull_root.mkdir()
        self.credential_path = "/srv/private/example/credentials.json"
        self.sqlite_path = "/var/lib/example/state.sqlite3"
        self.config = {
            "schema": LOCAL_CONFIG_SCHEMA,
            "yard_root": str(self.yard),
            "local_host_id": "HOST-B",
            "local_peer_id": "PEER-B",
            "trusted_hosts": [
                {
                    "host_id": "HOST-A",
                    "min_revision": 3,
                    "signature_algorithm": "external-ed25519",
                    "key_id": "host-a-v1",
                    "signature_reference": TEST_SIGNATURE_REF,
                    "allowed_network_labels": ["direct", "tailscale"],
                    "allowed_remote_paths": [
                        self.credential_path,
                        self.sqlite_path,
                    ],
                    "known_host_pins": [
                        {
                            "endpoint_id": "tailscale-sftp",
                            "sha256": TEST_PIN,
                        }
                    ],
                }
            ],
            "pull_destination_roots": [str(self.pull_root)],
            "max_registry_age_seconds": 7200,
            "max_registry_ttl_seconds": 7200,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def registry(self, *, network_label="tailscale", direct_pull=True):
        unsigned = {
            "schema": REGISTRY_SCHEMA,
            "host_id": "HOST-A",
            "revision": 3,
            "published_at": "2026-07-30T11:00:00Z",
            "expires_at": "2026-07-30T13:00:00Z",
            "endpoints": [
                {
                    "endpoint_id": "tailscale-sftp",
                    "transport": "sftp",
                    "network_label": network_label,
                    "host": "host-a.internal",
                    "port": 22,
                    "username": "registry-reader",
                    "read_only": True,
                    "known_host_pin": TEST_PIN,
                }
            ],
            "paths": [
                {
                    "path_id": "service-credential-file",
                    "kind": "file",
                    "metadata_type": "path-location",
                    "content_included": False,
                    "remote_path": self.credential_path,
                    "endpoint_id": "tailscale-sftp",
                    "allowed_peer_ids": ["PEER-B"],
                    "direct_pull": direct_pull,
                },
                {
                    "path_id": "application-state",
                    "kind": "database/sqlite",
                    "metadata_type": "path-location",
                    "content_included": False,
                    "remote_path": self.sqlite_path,
                    "endpoint_id": "tailscale-sftp",
                    "allowed_peer_ids": ["PEER-B"],
                    "direct_pull": False,
                    "adapter": "sqlite-transit-sync",
                },
            ],
        }
        payload_sha256 = hashlib.sha256(canonical(unsigned)).hexdigest()
        return {
            **unsigned,
            "signature_reference": {
                "algorithm": "external-ed25519",
                "key_id": "host-a-v1",
                "ref": TEST_SIGNATURE_REF,
                "payload_sha256": payload_sha256,
            },
        }

    def write_registry(self, value=None):
        path = self.registry_dir / "registry.json"
        path.write_text(
            json.dumps(value or self.registry(), indent=2),
            encoding="utf-8",
        )
        return path

    def resign(self, registry):
        unsigned = dict(registry)
        del unsigned["signature_reference"]
        registry["signature_reference"]["payload_sha256"] = hashlib.sha256(
            canonical(unsigned)
        ).hexdigest()
        return registry

    def runtime(self, config=None):
        return TrustedPeerPathRegistry(
            config or self.config,
            clock=lambda: NOW,
        )


class TestRegistryValidation(TrustedPeerPathFixture):
    def test_validate_list_and_resolve_metadata_without_content(self):
        self.write_registry()
        runtime = self.runtime()
        validated = runtime.validate("HOST-A")
        self.assertEqual(validated["host_id"], "HOST-A")
        self.assertTrue(validated["signature_reference"]["reference_pinned"])
        self.assertFalse(
            validated["signature_reference"]["cryptographic_signature_verified"]
        )
        self.assertFalse(validated["network_contacted"])
        self.assertFalse(validated["referenced_files_read"])

        listed = runtime.list_paths("HOST-A")
        self.assertEqual(len(listed["paths"]), 2)
        self.assertEqual(runtime.list_paths()["count"], 2)
        self.assertEqual(runtime.validate("HOST-A", record=True)["revision"], 3)
        resolved = runtime.resolve("HOST-A", "service-credential-file")
        self.assertEqual(resolved["path"]["remote_path"], self.credential_path)
        self.assertFalse(resolved["path"]["content_included"])

    def test_registry_must_be_in_matching_host_owned_slot(self):
        registry = self.registry()
        wrong = self.yard / "hosts" / "HOST-C" / "trusted-peer-paths"
        wrong.mkdir(parents=True)
        (wrong / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(TrustedPeerPathError, "missing or unsafe"):
            self.runtime().validate("HOST-A")

        self.write_registry({**registry, "host_id": "HOST-C"})
        with self.assertRaisesRegex(TrustedPeerPathError, "owner slot"):
            self.runtime().validate("HOST-A")

    def test_schema_revision_expiry_and_staleness_fail_closed(self):
        cases = []
        bad_schema = self.registry()
        bad_schema["schema"] = "system-gap.trusted-peer-paths.registry.v1"
        cases.append((bad_schema, "schema"))
        low_revision = self.registry()
        low_revision["revision"] = 2
        cases.append((low_revision, "revision"))
        expired = self.registry()
        expired["expires_at"] = "2026-07-30T11:59:59Z"
        cases.append((expired, "expired"))
        stale = self.registry()
        stale["published_at"] = "2026-07-30T09:59:59Z"
        stale["expires_at"] = "2026-07-30T12:30:00Z"
        cases.append((stale, "stale"))
        for registry, error in cases:
            with self.subTest(error=error):
                self.write_registry(registry)
                with self.assertRaisesRegex(TrustedPeerPathError, error):
                    self.runtime().validate("HOST-A")

    def test_signature_reference_and_payload_digest_are_pinned(self):
        registry = self.registry()
        registry["signature_reference"]["ref"] = (
            "urn:system-gap:signature-key:host-a:other"
        )
        self.write_registry(registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "not pinned"):
            self.runtime().validate("HOST-A")

        registry = self.registry()
        registry["paths"][0]["description"] = "tampered metadata"
        self.write_registry(registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "digest"):
            self.runtime().validate("HOST-A")

    def test_known_host_pin_missing_or_mismatched_fails_closed(self):
        for pins, error in (
            ([], "known_host_pins"),
            (
                [
                    {
                        "endpoint_id": "tailscale-sftp",
                        "sha256": "SHA256:" + ("B" * 43),
                    }
                ],
                "mismatch",
            ),
        ):
            config = json.loads(json.dumps(self.config))
            config["trusted_hosts"][0]["known_host_pins"] = pins
            self.write_registry()
            with (
                self.subTest(error=error),
                self.assertRaisesRegex(TrustedPeerPathError, error),
            ):
                self.runtime(config).validate("HOST-A")

    def test_every_published_path_must_be_concretely_allowlisted(self):
        registry = self.registry()
        registry["paths"][0]["remote_path"] = "/srv/private/example/other.json"
        unsigned = dict(registry)
        del unsigned["signature_reference"]
        registry["signature_reference"]["payload_sha256"] = hashlib.sha256(
            canonical(unsigned)
        ).hexdigest()
        self.write_registry(registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "outside.*allowlist"):
            self.runtime().validate("HOST-A")

    def test_secret_or_file_content_is_rejected_while_paths_remain_allowed(self):
        registry = self.registry()
        registry["paths"][0]["description"] = "password=not-allowed"
        self.write_registry(registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "secret or file content"):
            self.runtime().validate("HOST-A")

        registry = self.registry()
        registry["paths"][0]["content_included"] = True
        self.write_registry(self.resign(registry))
        with self.assertRaisesRegex(TrustedPeerPathError, "must be false"):
            self.runtime().validate("HOST-A")

        registry = self.registry()
        registry["private_key"] = "metadata-only-is-not-an-exception"
        self.write_registry(registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "forbidden secret"):
            self.runtime().validate("HOST-A")

        registry = self.registry()
        registry["paths"][0]["description"] = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
        self.write_registry(registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "opaque secret"):
            self.runtime().validate("HOST-A")

    def test_direct_and_tailscale_are_labels_not_provider_selection(self):
        for network in ("direct", "tailscale"):
            with self.subTest(network=network):
                self.write_registry(self.registry(network_label=network))
                validated = self.runtime().validate("HOST-A")
                self.assertEqual(validated["endpoints"][0]["network_label"], network)
                self.assertFalse(validated["network_contacted"])

    def test_unauthorized_peer_cannot_resolve(self):
        registry = self.registry()
        registry["paths"][0]["allowed_peer_ids"] = ["PEER-C"]
        unsigned = dict(registry)
        del unsigned["signature_reference"]
        registry["signature_reference"]["payload_sha256"] = hashlib.sha256(
            canonical(unsigned)
        ).hexdigest()
        self.write_registry(registry)
        with self.assertRaisesRegex(TrustedPeerPathError, "not allowed"):
            self.runtime().resolve("HOST-A", "service-credential-file")


class TestPullPreparation(TrustedPeerPathFixture):
    def test_plan_is_deterministic_read_only_and_non_executable(self):
        registry_path = self.write_registry()
        before = registry_path.read_bytes()
        destination = self.pull_root / "credential.json"
        runtime = self.runtime()
        first = runtime.pull_plan("HOST-A", "service-credential-file", destination)
        second = runtime.pull_plan("HOST-A", "service-credential-file", destination)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertFalse(first.executable)
        receipt = first.as_dict()
        self.assertEqual(receipt["schema"], RECEIPT_SCHEMA)
        self.assertEqual(receipt["status"], "prepared-no-transfer")
        self.assertFalse(receipt["network_contacted"])
        self.assertFalse(receipt["file_transfer_performed"])
        self.assertFalse(receipt["referenced_files_read"])
        self.assertFalse(receipt["executable"])
        self.assertFalse(receipt["constraints"]["provider_selected"])
        self.assertEqual(receipt["constraints"]["credentials"], "external-not-read")
        self.assertEqual(registry_path.read_bytes(), before)
        self.assertFalse(destination.exists())

    def test_direct_pull_false_fails_closed(self):
        self.write_registry(self.registry(direct_pull=False))
        with self.assertRaisesRegex(TrustedPeerPathError, "direct_pull=false"):
            self.runtime().pull_plan(
                "HOST-A",
                "service-credential-file",
                self.pull_root / "blocked.json",
            )

    def test_sqlite_is_discovery_only(self):
        self.write_registry()
        with self.assertRaisesRegex(TrustedPeerPathError, "direct_pull=false"):
            self.runtime().pull_plan(
                "HOST-A",
                "application-state",
                self.pull_root / "state.sqlite3",
            )

    def test_unsafe_destination_fails_closed(self):
        self.write_registry()
        existing = self.pull_root / "existing.json"
        existing.write_text("do not overwrite", encoding="utf-8")
        cases = [
            (existing, "already exists"),
            (self.root / "outside.json", "parent|outside"),
            (
                self.yard / "hosts" / "HOST-B" / "result.json",
                "parent|sync yard",
            ),
        ]
        for destination, error in cases:
            with (
                self.subTest(destination=destination),
                self.assertRaisesRegex(TrustedPeerPathError, error),
            ):
                self.runtime().pull_plan(
                    "HOST-A", "service-credential-file", destination
                )
        self.assertEqual(existing.read_text(encoding="utf-8"), "do not overwrite")

    def test_live_publish_and_pull_are_unavailable(self):
        runtime = self.runtime()
        with self.assertRaisesRegex(TrustedPeerPathError, "publishing.*unavailable"):
            runtime.publish({})
        with self.assertRaisesRegex(TrustedPeerPathError, "live transfer"):
            runtime.pull("HOST-A")

    def test_receipt_lists_activation_gates(self):
        self.write_registry()
        receipt = (
            self.runtime()
            .pull_plan(
                "HOST-A",
                "service-credential-file",
                self.pull_root / "credential.json",
            )
            .as_dict()
        )
        gates = "\n".join(receipt["remaining_activation_gates"])
        for term in (
            "signature",
            "host key",
            "read-only",
            "route",
            "executor",
            "destination",
        ):
            self.assertIn(term, gates)


class TestCliAndPublicContracts(TrustedPeerPathFixture):
    def write_config(self):
        path = self.root / "config.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        return path

    def test_read_only_cli_surface(self):
        registry = self.registry()
        now = datetime.now(timezone.utc)
        registry["published_at"] = (
            (now - timedelta(minutes=1))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        registry["expires_at"] = (
            (now + timedelta(minutes=30))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        self.write_registry(self.resign(registry))
        config = self.write_config()
        commands = [
            ["validate", "--config", str(config), "--host-id", "HOST-A"],
            ["list", "--config", str(config), "--host-id", "HOST-A"],
            [
                "resolve",
                "--config",
                str(config),
                "--host-id",
                "HOST-A",
                "--path-id",
                "service-credential-file",
            ],
            [
                "pull-plan",
                "--config",
                str(config),
                "--host-id",
                "HOST-A",
                "--path-id",
                "service-credential-file",
                "--destination",
                str(self.pull_root / "cli.json"),
            ],
        ]
        for command in commands:
            with (
                self.subTest(command=command[0]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(command), 0)
        self.assertFalse((self.pull_root / "cli.json").exists())

    def test_cli_has_no_publish_apply_or_output_write_flags(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "system_gap_master"
            / "trusted_peer_paths.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("Popen", source)
        self.assertNotIn("--apply", source)
        self.assertNotIn("--output", source)
        self.assertNotIn("known_hosts_ref", source)
        self.assertNotIn("signing_key_ref", source)
        self.assertNotIn("verification_key_ref", source)

    def test_schemas_and_examples_are_machine_readable_and_neutral(self):
        repository = Path(__file__).resolve().parents[1]
        files = [
            repository / "schemas" / "trusted-peer-path-registry.schema.json",
            repository / "schemas" / "trusted-peer-path-local-config.schema.json",
            repository / "examples" / "trusted-peer-paths.registry.example.json",
            repository / "examples" / "trusted-peer-paths.local-config.example.json",
            repository / "ellmos-module.v2.json",
        ]
        for path in files:
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files).lower()
        for forbidden in (
            "workstation-lg",
            "asus-gei",
            "onedrive",
            "lukas",
        ):
            self.assertNotIn(forbidden, combined)

    def test_duplicate_json_keys_fail_closed(self):
        self.write_registry()
        config_path = self.root / "duplicate.json"
        text = json.dumps(self.config).replace(
            '"local_host_id": "HOST-B"',
            '"local_host_id": "HOST-B", "local_host_id": "HOST-C"',
        )
        config_path.write_text(text, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "validate",
                        "--config",
                        str(config_path),
                        "--host-id",
                        "HOST-A",
                    ]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
