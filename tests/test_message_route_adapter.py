"""The existing transport boundary handles a separate message subject."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from system_gap_master.ticket_route_adapter import (
    MESSAGE_ROUTE_SCHEMA,
    TicketRouteAdapterError,
    _canonical_json,
    submit_message_handoff,
    validate_message_route_intent,
)


def _intent(record):
    stable = {
        "route_intent": MESSAGE_ROUTE_SCHEMA,
        "message_id": record["message_id"],
        "receipt_id": record["receipt_id"],
        "origin_ref": record["origin_ref"],
        "recipient_ref": record["recipient_ref"],
        "target_ref": "fixture:target",
        "surface": "registered_server",
        "delivery_mode": record["delivery_mode"],
        "payload_ref": "messages-line:" + record["message_id"],
        "payload_sha256": "sha256:" + hashlib.sha256(_canonical_json(record).encode()).hexdigest(),
    }
    return {**stable, "idempotency_key": "sha256:" + hashlib.sha256(_canonical_json(stable).encode()).hexdigest()}


class Source:
    def __init__(self, record):
        self.record = dict(record)
        self.saved = []

    def get(self, message_id):
        assert message_id == self.record["message_id"]
        return dict(self.record)

    def read_for_delivery(self, message_id, *, target_ref, surface, current_config):
        assert message_id == self.record["message_id"]
        if not current_config["enabled"]:
            raise ValueError("disabled")
        assert {"target_ref": target_ref, "surface": surface} in self.record["target_snapshot"]
        return dict(self.record)

    def receipts(self, message_id):
        assert message_id == self.record["message_id"]
        return list(self.saved)

    def record_observation(self, message_id, **kwargs):
        assert message_id == self.record["message_id"]
        row = dict(kwargs)
        for old in self.saved:
            if old == row:
                return old
        self.saved.append(row)
        return row


def _record():
    return {
        "message_id": "M-20261027-123456789",
        "receipt_id": "R-20261027-987654321",
        "origin_ref": "fixture:automation",
        "recipient_ref": "fixture:user",
        "delivery_mode": "on_demand",
        "target_snapshot": [{"target_ref": "fixture:target", "surface": "registered_server"}],
        "content_text": "Umlaute äöü",
    }


def _config(enabled=True):
    return {"enabled": enabled, "transport_contract_ref": MESSAGE_ROUTE_SCHEMA}


def _write_artifact(root: Path, intent, *, observed_at="2026-10-27T15:00:00Z"):
    path = root / "outbound.json"
    data = _canonical_json(intent).encode("utf-8")
    path.write_bytes(data)
    return {
        "state": "transported", "transport_id": "fixture:transfer-1",
        "observed_at": observed_at, "artifact_path": str(path),
        "artifact_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def test_staged_transport_reconciles_without_ack_and_replays(tmp_path):
    record = _record()
    sender, target = Source(record), Source(record)
    intent = _intent(record)
    assert validate_message_route_intent(intent) == intent
    assert target is not sender
    def callback(value):
        return _write_artifact(tmp_path, value)

    args = {
        "sender_source": sender, "target_source": target,
        "current_config_loader": lambda: _config(),
        "target_config_loader": lambda: _config(),
        "transport_submitter": callback, "artifact_root": tmp_path,
    }
    first = submit_message_handoff(intent, **args)
    second = submit_message_handoff(intent, **args)
    assert first.observation == second.observation
    assert first.observation["event"] == "transported"
    assert len(sender.saved) == 1
    assert "content_text" not in first.route_intent


def test_offline_has_no_receipt_and_no_retry_engine(tmp_path):
    record = _record()
    source = Source(record)
    result = submit_message_handoff(
        _intent(record), sender_source=source, target_source=Source(record),
        current_config_loader=lambda: _config(),
        target_config_loader=lambda: _config(),
        transport_submitter=lambda _value: {"state": "offline"}, artifact_root=tmp_path,
    )
    assert result.observation is None and source.saved == []


def test_bad_target_payload_and_disabled_policy_stop_before_callback(tmp_path):
    record = _record()
    intent = _intent(record)
    calls = []
    args = {
        "sender_source": Source(record),
        "target_source": Source({**record, "content_text": "tampered"}),
        "current_config_loader": lambda: _config(),
        "target_config_loader": lambda: _config(),
        "transport_submitter": lambda value: calls.append(value),
        "artifact_root": tmp_path,
    }
    with pytest.raises(TicketRouteAdapterError, match="digest"):
        submit_message_handoff(intent, **args)
    assert not calls
    args["target_source"] = Source(record)
    args["current_config_loader"] = lambda: _config()
    args["target_config_loader"] = lambda: _config(False)
    with pytest.raises(TicketRouteAdapterError, match="resolved"):
        submit_message_handoff(intent, **args)
    assert not calls
    args["target_config_loader"] = lambda: _config()
    args["current_config_loader"] = lambda: _config(False)
    with pytest.raises(TicketRouteAdapterError, match="resolved"):
        submit_message_handoff(intent, **args)
    assert not calls
    with pytest.raises(TicketRouteAdapterError, match="idempotency"):
        validate_message_route_intent({**intent, "receipt_id": "R-20261027-123456789"})


def test_artifact_and_transport_id_conflicts_fail_closed(tmp_path):
    record = _record()
    source = Source(record)
    intent = _intent(record)
    args = {
        "sender_source": source, "target_source": Source(record),
        "current_config_loader": lambda: _config(),
        "target_config_loader": lambda: _config(), "artifact_root": tmp_path,
    }
    submit_message_handoff(intent, transport_submitter=lambda value: _write_artifact(tmp_path, value), **args)
    with pytest.raises(TicketRouteAdapterError, match="conflicting evidence"):
        submit_message_handoff(
            intent,
            transport_submitter=lambda value: _write_artifact(tmp_path, value, observed_at="2026-10-27T15:01:00Z"),
            **args,
        )
    with pytest.raises(TicketRouteAdapterError, match="does not match"):
        submit_message_handoff(
            intent,
            transport_submitter=lambda value: {**_write_artifact(tmp_path, value), "artifact_sha256": "sha256:" + "0" * 64},
            **args,
        )


def test_policy_change_during_callback_cannot_record_transport_observation(tmp_path):
    record = _record()
    source = Source(record)
    target_state = {"enabled": True}

    def submit(value):
        result = _write_artifact(tmp_path, value)
        target_state["enabled"] = False
        return result

    with pytest.raises(TicketRouteAdapterError, match="resolved"):
        submit_message_handoff(
            _intent(record), sender_source=source, target_source=Source(record),
            current_config_loader=lambda: _config(),
            target_config_loader=lambda: _config(target_state["enabled"]),
            transport_submitter=submit, artifact_root=tmp_path,
        )
    assert source.saved == []
