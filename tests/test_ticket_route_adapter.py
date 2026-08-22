from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from system_gap_master.ticket_route_adapter import (
    TicketRouteAdapterError,
    create_ticket_handoff,
    validate_route_intent,
)


def _intent(*, kind="grouped", systems=None):
    snapshot = {
        "kind": kind,
        "systems": systems if systems is not None else ["ASUS-GEI", "WORKSTATION-LG"],
        "at": "2026-08-22T19:00:00Z",
        "source": "test-system-registry",
        "fingerprint": "sha256:" + "a" * 64,
    }
    stable = {
        "ticket_id": "T-20260822-123456789",
        "target_snapshot": snapshot,
        "receipt_to": "T-20260822-123456789",
    }
    key = "sha256:" + hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"route_intent": "ellmos.ticket.route-intent.v1", **stable, "idempotency_key": key}


class FakeAPI:
    def __init__(self):
        self.created = []
        self.loaded = []
        self.built = []
        self.path = Path("tickets/INBOX/T-20260822-123456789.to-grouped.txt")

    def create_routed_ticket(self, **request):
        self.created.append(request)
        return str(self.path)

    def load_contract(self, path):
        self.loaded.append(path)
        return {"path": str(path)}

    def build_route_intent(self, view):
        self.built.append(view)
        return _intent()


def test_handoff_uses_only_public_writer_boundary_and_one_transport_call():
    api = FakeAPI()
    submitted = []
    result = create_ticket_handoff(
        {"title": "Route", "body": "Body", "idempotency_key": "caller:42"},
        api=api,
        transport_submitter=lambda intent: submitted.append(intent) or {"state": "queued"},
    )

    assert len(api.created) == len(api.loaded) == len(api.built) == 1
    assert submitted == [result.route_intent]
    assert result.ticket_path == api.path
    assert result.transport_result == {"state": "queued"}


def test_transport_result_remains_opaque_and_is_not_completion():
    result = create_ticket_handoff(
        {"idempotency_key": "caller:42"},
        api=FakeAPI(),
        transport_submitter=lambda _intent: {"delivered": True, "done": True},
    )
    assert result.transport_result == {"delivered": True, "done": True}
    assert "done" not in result.route_intent


def test_retry_preserves_caller_idempotency_key():
    api = FakeAPI()
    request = {"idempotency_key": "caller:stable"}
    first = create_ticket_handoff(request, api=api)
    second = create_ticket_handoff(request, api=api)
    assert first.ticket_path == second.ticket_path
    assert [call["idempotency_key"] for call in api.created] == ["caller:stable", "caller:stable"]


@pytest.mark.parametrize(
    ("kind", "systems"),
    [("any", ["ASUS-GEI"]), ("all", []), ("exact", ["A", "B"])],
)
def test_target_cardinality_fails_closed(kind, systems):
    with pytest.raises(TicketRouteAdapterError):
        validate_route_intent(_intent(kind=kind, systems=systems))


def test_duplicate_systems_fail_closed():
    with pytest.raises(TicketRouteAdapterError):
        validate_route_intent(_intent(systems=["ASUS-GEI", "ASUS-GEI"]))


def test_changed_payload_with_old_idempotency_key_fails_closed():
    intent = _intent()
    intent["receipt_to"] = "T-OTHER"
    with pytest.raises(TicketRouteAdapterError, match="idempotency"):
        validate_route_intent(intent)


def test_unknown_fields_fail_closed():
    intent = _intent()
    intent["transport_state"] = "done"
    with pytest.raises(TicketRouteAdapterError, match="fields"):
        validate_route_intent(intent)


def test_missing_creation_idempotency_key_stops_before_writer():
    api = FakeAPI()
    with pytest.raises(TicketRouteAdapterError, match="idempotency_key"):
        create_ticket_handoff({"title": "unsafe retry"}, api=api)
    assert api.created == []
