# Ticket route-intent adapter

`system-gap-master` 1.5 adds a narrow adapter between the public routing API
of `ticket-master` 1.11 and an existing system-gap transport. Install the
explicit integration dependency with:

```console
python -m pip install "system-gap-master[ticket-routing]>=1.5,<1.6"
```

The boundary is intentionally one-way:

1. `ticket-master` alone creates the ticket, resolves its immutable target
   snapshot and owns claims, leases, the per-system ledger and completion.
2. `system-gap-master` validates the resulting
   `ellmos.ticket.route-intent.v1` payload and may submit exactly that payload
   once to a caller-provided transport callback.
3. The transport result stays opaque. `queued`, `sent` or `delivered` never
   means that a ticket is complete; only valid host receipts recorded by
   `ticket-master` may advance its ledger.

The adapter owns no queue, inbox, retry loop, target fan-out or ticket files.
Callers must provide a stable creation `idempotency_key`; a retry is therefore
resolved by the canonical ticket writer instead of minting another ticket.

```python
from system_gap_master import create_ticket_handoff

result = create_ticket_handoff(
    {
        "title": "Run on both systems",
        "body": "Perform the host-local check and return a receipt.",
        "tickets_dir": tickets_dir,
        "registry_snapshot": registry_snapshot,
        "ticket_kind": "fork",
        "target_kind": "grouped",
        "targets": ["ASUS-GEI", "WORKSTATION-LG"],
        "primary_ticket": "T-20260822-123456789",
        "original_owner": "ticket-master@ASUS-GEI",
        "receipt_to": "T-20260822-123456789",
        "idempotency_key": "bootstrap:both-systems:v1",
    },
    transport_submitter=existing_transport.submit,
)
```

Inject a compatible API object through `api=` for an embedded installation or
test. Without injection, the adapter imports only the three packaged public
functions `create_routed_ticket`, `load_contract` and `build_route_intent`.

## Separate Messages-Line subject

`ellmos.ticket.message-route-intent.v1` is a non-executable message subject.
It carries MessageID, canonical ReceiptID, origin, recipient, target and a
digest of the full Message-Ticket, but no content or synthetic T-ticket. The
existing ticket `route-intent.v1` API is unchanged.

`submit_message_handoff` requires sender and target message sources and fresh
configuration loaders. Both sources must resolve the complete payload and
match its identifiers, target and digest before the existing transport
callback runs. Both policies must explicitly select the message contract.
An offline callback creates no observation. A transported callback must
identify an exact metadata artifact under the approved root; only then is a
`transported` observation written through the canonical Messages-Line API.
An identical replay remains one observation, while conflicting reuse of a
transport ID fails closed. A file or callback response is never proof of
remote display, delivery or acknowledgement. No authenticated historical Ack
import, remote store registration or production transport is claimed here.
