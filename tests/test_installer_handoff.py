from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from system_gap_master import instance_manager
from system_gap_master.installer_handoff import export_installer_handoff

NOW = dt.datetime(2026, 10, 8, 14, 0, tzinfo=dt.timezone.utc)
TEMPLATE = Path(instance_manager.__file__).parent / "yard_template"


@pytest.fixture
def sources(tmp_path):
    yard = tmp_path / "private-yard"
    yard.mkdir()
    (yard / "private.txt").write_text("PRIVATE SENTINEL", encoding="utf-8")
    profile = tmp_path / "profile.json"
    observation = tmp_path / "observation.json"
    profile.write_text(json.dumps({"schema": "ellmos.system.v1", "id": "bach-agent-system"}), encoding="utf-8")
    observation.write_text(json.dumps({
        "schema": "codex.bach.deployment-observation.v1", "host": "mac-studio",
        "observed_at": NOW.isoformat(), "bach_source_commit": "a" * 40,
        "backend_origin": {"mode": "server", "instance_verified": True},
        "unexpected_private_content": "MUST NOT EXPORT",
    }), encoding="utf-8")
    return yard, profile, observation


def options(sources):
    _, profile, observed = sources
    return {"profile_path": profile, "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
            "observation_path": observed, "observation_sha256": hashlib.sha256(observed.read_bytes()).hexdigest(),
            "target_host": "mac-studio", "inventory_host": "ASUS-GEI", "now": NOW}


def test_real_metadata_export_is_hashed_private_and_has_no_mutation(sources, monkeypatch):
    yard, _, _ = sources
    args = options(sources)
    before = sorted(p.name for p in yard.iterdir())
    original = Path.read_bytes

    def protected_read(path):
        if path.is_relative_to(yard):
            raise AssertionError("private yard contents must not be read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", protected_read)
    exported = export_installer_handoff(yard, TEMPLATE, **args)
    handoff, sync = exported["handoff"], exported["sync_evidence"]
    assert instance_manager._verify_digest(handoff, "content_hash")
    assert instance_manager._verify_digest(sync, "content_hash")
    assert sync["private"] is True and sync["included_content"] is False
    assert handoff["sync_evidence_hash"] == sync["content_hash"]
    assert handoff["runtime_observation"]["runtime_reverified_by_exporter"] is False
    assert handoff["source_refs"]["deployment_observation"]["signature_verified"] is False
    assert handoff["approval_created"] is False and handoff["grant_created"] is False
    assert "PRIVATE SENTINEL" not in json.dumps(exported)
    assert "MUST NOT EXPORT" not in json.dumps(exported)
    assert before == sorted(p.name for p in yard.iterdir())


def test_pin_host_and_staleness_fail_closed(sources):
    yard, _, observation = sources
    args = options(sources)
    with pytest.raises(instance_manager.InstanceManagerError, match="SHA-256 pin"):
        export_installer_handoff(yard, TEMPLATE, **{**args, "observation_sha256": "b" * 64})
    with pytest.raises(instance_manager.InstanceManagerError, match="host does not match"):
        export_installer_handoff(yard, TEMPLATE, **{**args, "target_host": "foreign-host"})
    with pytest.raises(instance_manager.InstanceManagerError, match="stale"):
        export_installer_handoff(yard, TEMPLATE, **{**args, "now": NOW + dt.timedelta(days=2)})
    observation.write_text("{}", encoding="utf-8")
    with pytest.raises(instance_manager.InstanceManagerError, match="SHA-256 pin"):
        export_installer_handoff(yard, TEMPLATE, **args)


def test_utc_z_observation_is_supported_on_python310(sources):
    yard, _, observation = sources
    value = json.loads(observation.read_text(encoding="utf-8"))
    value["observed_at"] = "2026-10-08T14:00:00Z"
    observation.write_text(json.dumps(value), encoding="utf-8")
    result = export_installer_handoff(yard, TEMPLATE, **options(sources))
    assert result["handoff"]["source_refs"]["deployment_observation"]["observed_at"] == value["observed_at"]


def test_native_cli_exports_metadata_without_writing_yard(sources, capsys):
    yard, _, observed = sources
    value = json.loads(observed.read_text(encoding="utf-8"))
    value["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    observed.write_text(json.dumps(value), encoding="utf-8")
    args = options(sources)
    cli = ["installer-handoff", "--yard-root", str(yard), "--template-root", str(TEMPLATE)]
    for field in ("profile_path", "profile_sha256", "observation_path", "observation_sha256",
                  "target_host", "inventory_host"):
        cli.extend(["--" + field.replace("_", "-"), str(args[field])])
    assert instance_manager.main(cli) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["handoff"]["schema"] == "ellmos.system-gap.handoff.v1"
    assert sorted(p.name for p in yard.iterdir()) == ["private.txt"]
