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
MESSAGE_ROUTE_SCHEMA = "ellmos.ticket.message-route-intent.v1"
TARGET_KINDS = frozenset({"any", "all", "grouped", "exact"})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MESSAGE_ID_RE = re.compile(r"^M-[0-9]{8}-[1-9][0-9]{8}$")
_RECEIPT_ID_RE = re.compile(r"^R-[0-9]{8}-[1-9][0-9]{8}$")
_SAFE_REF_RE = re.compile(r"^[^\x00-\x20\x7f\\]{1,256}$")
_MESSAGE_FIELDS = frozenset({
    "route_intent", "message_id", "receipt_id", "origin_ref", "recipient_ref",
    "target_ref", "surface", "delivery_mode", "payload_ref", "payload_sha256",
    "idempotency_key",
})


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


@dataclass(frozen=True)
class MessageHandoff:
    """One metadata-only submission and its canonical local observation."""

    route_intent: dict[str, Any]
    transport_result: Any
    observation: dict[str, Any] | None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_message_route_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the independent message subject without weakening ticket v1."""
    if not isinstance(value, Mapping) or frozenset(value) != _MESSAGE_FIELDS:
        raise TicketRouteAdapterError("message intent fields do not match v1")
    if value["route_intent"] != MESSAGE_ROUTE_SCHEMA:
        raise TicketRouteAdapterError("unsupported message intent schema")
    for field, regex in (("message_id", _MESSAGE_ID_RE), ("receipt_id", _RECEIPT_ID_RE)):
        if not isinstance(value[field], str) or not regex.fullmatch(value[field]):
            raise TicketRouteAdapterError(f"invalid {field}")
    for field in ("origin_ref", "recipient_ref", "target_ref"):
        ref = value[field]
        if not isinstance(ref, str) or not _SAFE_REF_RE.fullmatch(ref) or ref.startswith("/") or re.match(r"^[A-Za-z]:/", ref):
            raise TicketRouteAdapterError(f"invalid {field}")
    if not isinstance(value["surface"], str) or value["surface"] not in {
        "ticket_master", "local_gui", "server_gui", "registered_server"
    }:
        raise TicketRouteAdapterError("invalid message surface")
    if not isinstance(value["delivery_mode"], str) or value["delivery_mode"] not in {
        "chat_start", "on_demand", "active_chat"
    }:
        raise TicketRouteAdapterError("invalid message delivery mode")
    if value["payload_ref"] != f"messages-line:{value['message_id']}":
        raise TicketRouteAdapterError("message payload reference is not bound")
    if not isinstance(value["payload_sha256"], str) or not _SHA256_RE.fullmatch(value["payload_sha256"]):
        raise TicketRouteAdapterError("invalid message payload digest")
    detached = json.loads(_canonical_json(value))
    stable = {key: detached[key] for key in _MESSAGE_FIELDS - {"idempotency_key"}}
    key = "sha256:" + hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()
    if detached["idempotency_key"] != key:
        raise TicketRouteAdapterError("message idempotency key does not match its payload")
    return detached


def _verified_payload(intent: Mapping[str, Any], source: Any, *, for_delivery: bool,
                      current_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        record = (source.read_for_delivery(
            intent["message_id"], target_ref=intent["target_ref"], surface=intent["surface"],
            current_config=current_config,
        ) if for_delivery else source.get(intent["message_id"]))
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise TicketRouteAdapterError(f"message payload cannot be resolved: {type(exc).__name__}") from exc
    if not isinstance(record, Mapping):
        raise TicketRouteAdapterError("message payload resolver did not return a record")
    for field in ("message_id", "receipt_id", "origin_ref", "recipient_ref", "delivery_mode"):
        if record.get(field) != intent[field]:
            raise TicketRouteAdapterError(f"message payload {field} does not match intent")
    if {"target_ref": intent["target_ref"], "surface": intent["surface"]} not in record.get("target_snapshot", []):
        raise TicketRouteAdapterError("message payload does not contain target")
    if not isinstance(record.get("content_text"), str) or not record["content_text"].strip():
        raise TicketRouteAdapterError("message content is not resolvable")
    digest = "sha256:" + hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
    if digest != intent["payload_sha256"]:
        raise TicketRouteAdapterError("message payload digest does not match intent")
    return dict(record)


def _verify_transport_artifact(intent: Mapping[str, Any], result: Any, artifact_root: Path) -> tuple[str, str]:
    if not isinstance(result, Mapping) or frozenset(result) != {
        "state", "transport_id", "observed_at", "artifact_path", "artifact_sha256"
    } or result["state"] != "transported":
        raise TicketRouteAdapterError("transport receipt does not describe one artifact")
    transport_id = result["transport_id"]
    if not isinstance(transport_id, str) or not _SAFE_REF_RE.fullmatch(transport_id):
        raise TicketRouteAdapterError("transport id is invalid")
    if not isinstance(result["observed_at"], str) or not result["observed_at"]:
        raise TicketRouteAdapterError("transport observation time is invalid")
    given_root = Path(artifact_root)
    if given_root.is_symlink():
        raise TicketRouteAdapterError("artifact root is a symbolic link")
    root = given_root.resolve(strict=True)
    if not root.is_dir():
        raise TicketRouteAdapterError("artifact root is not a real directory")
    raw = Path(result["artifact_path"])
    if not raw.is_absolute():
        raise TicketRouteAdapterError("transport artifact path must be absolute")
    try:
        relative = raw.relative_to(root)
    except ValueError as exc:
        raise TicketRouteAdapterError("transport artifact escapes its approved root") from exc
    if ".." in relative.parts:
        raise TicketRouteAdapterError("transport artifact traverses its approved root")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise TicketRouteAdapterError("transport artifact traverses a symbolic link")
    path = raw.resolve(strict=True)
    if not path.is_file() or path != cursor:
        raise TicketRouteAdapterError("transport artifact is not a regular in-root file")
    data = path.read_bytes()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if result["artifact_sha256"] != digest or data != _canonical_json(intent).encode("utf-8"):
        raise TicketRouteAdapterError("transport artifact does not match the message intent")
    return transport_id, digest


def submit_message_handoff(
    intent: Mapping[str, Any], *, sender_source: Any, target_source: Any,
    current_config_loader: Callable[[], Mapping[str, Any]],
    target_config_loader: Callable[[], Mapping[str, Any]],
    transport_submitter: TransportSubmitter, artifact_root: Path | str,
) -> MessageHandoff:
    """Submit once via the existing callback; reconcile only a local artifact.

    Target content resolution is mandatory. A staged artifact proves only a
    transport observation, never an acknowledgement or remote display.
    Historical receipt import and retries remain outside this function.
    """
    checked = validate_message_route_intent(intent)
    def policy(loader: Callable[[], Mapping[str, Any]]) -> Mapping[str, Any]:
        config = loader()
        if not isinstance(config, Mapping) or config.get("transport_contract_ref") != MESSAGE_ROUTE_SCHEMA:
            raise TicketRouteAdapterError("message route contract is not active in current config")
        return config

    _verified_payload(checked, sender_source, for_delivery=True, current_config=policy(current_config_loader))
    _verified_payload(checked, target_source, for_delivery=True, current_config=policy(target_config_loader))
    _verified_payload(checked, sender_source, for_delivery=True, current_config=policy(current_config_loader))
    result = transport_submitter(checked)
    if isinstance(result, Mapping) and result == {"state": "offline"}:
        return MessageHandoff(checked, dict(result), None)
    transport_id, digest = _verify_transport_artifact(checked, result, Path(artifact_root))
    # Recheck after callback; a policy change does not allow a new receipt.
    _verified_payload(checked, sender_source, for_delivery=True, current_config=policy(current_config_loader))
    _verified_payload(checked, target_source, for_delivery=True, current_config=policy(target_config_loader))
    channel_ref = f"system-gap:{transport_id}"
    existing = [row for row in sender_source.receipts(checked["message_id"])
                if row["channel_ref"] == channel_ref]
    for row in existing:
        if (row["event"] != "transported" or row["evidence_ref"] != digest
                or row["observed_at"] != result["observed_at"]):
            raise TicketRouteAdapterError("transport id was reused for conflicting evidence")
    try:
        observation = sender_source.record_observation(
            checked["message_id"], target_ref=checked["target_ref"], surface=checked["surface"],
            channel_ref=channel_ref, actor_ref="system-gap:local-adapter", evidence_ref=digest,
            event="transported", observed_at=result["observed_at"],
            current_config=policy(current_config_loader),
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise TicketRouteAdapterError(f"canonical receipt ledger rejected observation: {type(exc).__name__}") from exc
    return MessageHandoff(checked, dict(result), observation)


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
