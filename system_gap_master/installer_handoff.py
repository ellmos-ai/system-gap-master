"""Read-only, hash-bound partner metadata export for the canonical installer.

An external deployment observation remains an observation by its named source.
This adapter verifies bytes/identity/freshness, inventories yard metadata and
never performs a runtime probe, adoption, approval, grant or installation.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import __version__
from .instance_manager import InstanceManagerError, _attach_digest, _canonical_bytes, inventory_yard


def _pinned_object(path: str | Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise InstanceManagerError("metadata source requires a full SHA-256 pin")
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 65536:
        raise InstanceManagerError("metadata source must be a JSON file of at most 64 KiB")
    payload = source.read_bytes()
    if len(payload) > 65536 or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise InstanceManagerError("metadata source bytes do not match their SHA-256 pin")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise InstanceManagerError("metadata source must contain a JSON object")
    return value, str(source)


def export_installer_handoff(
    yard_root: str | Path,
    template_root: str | Path,
    *,
    profile_path: str | Path,
    profile_sha256: str,
    observation_path: str | Path,
    observation_sha256: str,
    target_host: str,
    inventory_host: str,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Bind real native profile and a fresh BACH technical observation, without writes."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", target_host) or not re.fullmatch(
        r"[A-Za-z0-9._-]+", inventory_host
    ):
        raise InstanceManagerError("target and inventory hosts must be explicit stable identifiers")
    profile, profile_source = _pinned_object(profile_path, profile_sha256)
    if profile.get("schema") != "ellmos.system.v1" or profile.get("id") != "bach-agent-system":
        raise InstanceManagerError("partner export requires the native bach-agent-system profile")
    observed, observation_source = _pinned_object(observation_path, observation_sha256)
    if observed.get("schema") != "codex.bach.deployment-observation.v1":
        raise InstanceManagerError("unsupported technical observation schema; explicit adapter required")
    if observed.get("host") != target_host:
        raise InstanceManagerError("technical observation host does not match the partner target")
    raw_timestamp = str(observed.get("observed_at", ""))
    if raw_timestamp.endswith("Z"):
        raw_timestamp = raw_timestamp[:-1] + "+00:00"
    timestamp = dt.datetime.fromisoformat(raw_timestamp)
    current = now or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None or current.tzinfo is None:
        raise InstanceManagerError("technical observation timestamps must include a timezone")
    age = (current - timestamp).total_seconds()
    if age < -300 or age > 86400:
        raise InstanceManagerError("technical observation is stale or from the future")
    commit = observed.get("bach_source_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise InstanceManagerError("technical observation has no immutable BACH source commit")
    backend = observed.get("backend_origin") or {}
    if not isinstance(backend, dict):
        raise InstanceManagerError("technical observation backend identity must be an object")
    inventory = inventory_yard(yard_root, template_root)
    inventory_sha256 = hashlib.sha256(_canonical_bytes(inventory)).hexdigest()
    source_refs = {
        "profile": {"path": profile_source, "sha256": profile_sha256, "schema": profile["schema"]},
        "deployment_observation": {
            "path": observation_source, "sha256": observation_sha256,
            "schema": observed["schema"], "observed_at": observed["observed_at"],
            "authority": "external-observation", "signature_verified": False,
        },
    }
    sync = _attach_digest({
        "schema": "ellmos.installer.sync-evidence.v1",
        "private": True, "included_content": False,
        "verification_scope": "local-yard-metadata-inventory",
        "inventory_host": inventory_host, "yard_root": inventory["yard_root"],
        "template_version": inventory["template_version"],
        "inventory_sha256": inventory_sha256, "summary": inventory["summary"],
        "remote_yard_verified": False, "mutation_performed": False,
    }, "content_hash")
    handoff = _attach_digest({
        "schema": "ellmos.system-gap.handoff.v1", "mode": "partner",
        "target": {"host": target_host, "system_id": profile["id"]},
        "producer": {
            "id": "system-gap-master", "version": __version__,
            "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "source_refs": source_refs,
        "verification_scope": "source-bytes-identity-freshness-and-local-yard-metadata",
        "runtime_observation": {
            "source_commit": commit, "observed_by": observed["schema"],
            "backend": {key: backend.get(key) for key in (
                "mode", "backend_kind", "instance_label", "connection_verified", "schema_verified",
                "instance_verified", "adapter_binding_verified", "reason_code"
            )},
            "runtime_reverified_by_exporter": False,
        },
        "sync_evidence_hash": sync["content_hash"],
        "limits": [
            "External technical observation is hash-bound, not independently re-probed or signed.",
            "Local yard inventory does not verify the remote target yard or its contents.",
            "GUI/provider adoption, decision writer and full composition require separate verification.",
            "This handoff is not an audit PASS, owner approval or signed capability grant.",
        ],
        "approval_created": False, "grant_created": False, "mutation_performed": False,
    }, "content_hash")
    return {"schema": "system-gap.installer-export.v1", "handoff": handoff, "sync_evidence": sync}
