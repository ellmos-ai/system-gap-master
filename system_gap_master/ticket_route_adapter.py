"""Narrow ticket-master to transport adapter.

Ticket lifecycle, target resolution and receipt completion stay owned by
ticket-master.  This module validates its public ``route_intent`` boundary and
optionally passes that payload to an existing system-gap transport callback.
It deliberately implements no queue, fan-out, retry loop or completion state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

ROUTE_INTENT_SCHEMA = "ellmos.ticket.route-intent.v1"
TARGET_KINDS = frozenset({"any", "all", "grouped", "exact"})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class TicketRouteAdapterError(ValueError):
    """The public ticket routing boundary is missing or contradictory."""


class TicketRoutingAPI(Protocol):
    """Only the three public ticket-master operations used by this adapter."""

    def create_routed_ticket(self, **request: Any) -> str: ...

    def load_contract(self, path: Path | str) -> Any: ...

    def build_route_intent(self, view: Any) -> Mapping[str, Any]: ...


TransportSubmitter = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class TicketHandoff:
    """Result without interpreting the transport's provider-specific state."""

    ticket_path: Path
    route_intent: dict[str, Any]
    transport_result: Any = None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _default_api() -> TicketRoutingAPI:
    try:
        import lib as ticket_master_api
    except (ImportError, OSError) as exc:
        raise TicketRouteAdapterError(
            "ticket-master routing API is unavailable; install the ticket-routing extra "
            "or inject a compatible public API"
        ) from exc
    return ticket_master_api  # type: ignore[return-value]


def validate_route_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach the complete ticket-master transport payload."""

    if not isinstance(value, Mapping):
        raise TicketRouteAdapterError("route intent must be a mapping")
    expected = {
        "route_intent",
        "ticket_id",
        "target_snapshot",
        "receipt_to",
        "idempotency_key",
    }
    if set(value) != expected:
        raise TicketRouteAdapterError("route intent fields do not match the public v1 boundary")
    if value.get("route_intent") != ROUTE_INTENT_SCHEMA:
        raise TicketRouteAdapterError("unsupported route intent schema")

    ticket_id = value.get("ticket_id")
    receipt_to = value.get("receipt_to")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise TicketRouteAdapterError("route intent requires a ticket_id")
    if not isinstance(receipt_to, str) or not receipt_to.strip():
        raise TicketRouteAdapterError("route intent requires receipt_to")

    snapshot = value.get("target_snapshot")
    snapshot_fields = {"kind", "systems", "at", "source", "fingerprint"}
    if not isinstance(snapshot, Mapping) or set(snapshot) != snapshot_fields:
        raise TicketRouteAdapterError("target snapshot fields do not match the public v1 boundary")
    kind = snapshot.get("kind")
    systems = snapshot.get("systems")
    if kind not in TARGET_KINDS:
        raise TicketRouteAdapterError("unknown target kind")
    if not isinstance(systems, list) or any(
        not isinstance(system, str) or not system.strip() for system in systems
    ):
        raise TicketRouteAdapterError("target systems must be a list of non-empty strings")
    if len(systems) != len(set(systems)):
        raise TicketRouteAdapterError("target systems must be unique")
    if kind == "any" and systems:
        raise TicketRouteAdapterError("an any-target intent must not preselect a system")
    if kind != "any" and not systems:
        raise TicketRouteAdapterError("a concrete target intent requires systems")
    if kind == "exact" and len(systems) != 1:
        raise TicketRouteAdapterError("an exact target intent requires one system")
    for field in ("at", "source"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise TicketRouteAdapterError(f"target snapshot requires {field}")
    fingerprint = snapshot.get("fingerprint")
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise TicketRouteAdapterError("target snapshot fingerprint is not a SHA-256 reference")

    detached = json.loads(_canonical_json(value))
    stable = {
        "ticket_id": detached["ticket_id"],
        "target_snapshot": detached["target_snapshot"],
        "receipt_to": detached["receipt_to"],
    }
    expected_key = "sha256:" + hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()
    if detached["idempotency_key"] != expected_key:
        raise TicketRouteAdapterError("route intent idempotency key does not match its payload")
    return detached


def create_ticket_handoff(
    create_request: Mapping[str, Any],
    *,
    api: TicketRoutingAPI | None = None,
    transport_submitter: TransportSubmitter | None = None,
) -> TicketHandoff:
    """Create/load through ticket-master and optionally submit one route intent.

    The caller must supply a stable creation ``idempotency_key``.  Retrying the
    same request therefore resolves to the same ticket through ticket-master's
    canonical writer before the transport boundary is rebuilt.
    """

    if not isinstance(create_request, Mapping):
        raise TicketRouteAdapterError("create_request must be a mapping")
    request = dict(create_request)
    request_key = request.get("idempotency_key")
    if not isinstance(request_key, str) or not request_key.strip():
        raise TicketRouteAdapterError("create_request requires a stable idempotency_key")
    routing_api = api or _default_api()
    try:
        ticket_path = Path(routing_api.create_routed_ticket(**request))
        view = routing_api.load_contract(ticket_path)
        intent = validate_route_intent(routing_api.build_route_intent(view))
    except TicketRouteAdapterError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise TicketRouteAdapterError(
            f"ticket-master public routing API rejected the handoff: {type(exc).__name__}"
        ) from exc

    transport_result = transport_submitter(intent) if transport_submitter is not None else None
    return TicketHandoff(ticket_path, intent, transport_result)
