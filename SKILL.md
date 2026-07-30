---
name: system-gap-master
description: Daily cross-machine sync ritual for multi-agent setups. Use this when the user says "/sync", "run the sync", "check the sync yard", when a session-start reminder says today's sync is missing, or when bringing knowledge from one machine/agent to another via the shared sync folder.
---

# system-gap-master — the daily sync ritual (v1)

You are executing the daily synchronization between this machine and the
other machines/agents of this user, via the shared sync folder (the "yard").
Rules live in `PROTOCOL.md` (repo) / `SYNC_PROTOCOL.md` (yard copy) — the
slot rule (R1) and the no-secrets rule (R6) are non-negotiable.

**Configuration:** the yard path comes from (in order) the `SYSTEM_GAP_MASTER_DIR`
environment variable, the user's agent rule file (CLAUDE.md/AGENTS.md), or
asking the user once. `<HOST>` is this machine's name.

## Steps

1. **Gate check** — `python scripts/system_gap_daily_check.py check` (or read
   `DAILY_SYNC_LOG.md` directly). Today's row for `<HOST>` exists → say so and
   stop; the ritual runs once per day.
2. **Conflict sweep (R7)** — run the `conflict-copy-reconciler` scan/plan for
   the explicitly configured roots. Discovery never proves canonicality.
   Apply only candidates marked ready by an authoritative mapping and a
   deterministic safe class. The engine owns lease, backup, atomic write,
   verify, recoverable archive and rollback. Leave every blocked candidate
   untouched and log only the path-redacted receipt/status in
   `CONFLICT_REVIEW_LOG.md`. Run observer configs with `plan`; never call
   `reconcile` unless this host/root is the single documented mutating owner.
3. **Read inbound** —
   - `messages/to-<HOST>.md` and `messages/to-<your-agent-name>.md`: act on
     each entry, move lasting information into local rule files/docs, then
     DELETE the read entries (empty channel = nothing new).
   - Other hosts' slots (`hosts/<OTHER>/`) and root topic documents: skim for
     new items since the last sync; integrate what concerns this machine
     (install, configure, note down), then move fully-integrated items to
     `_archive/` — but only items addressed to this machine or marked done
     everywhere they apply.
   - Trusted peer registries: use `trusted-peer-paths list|resolve` with the
     host-local trust config. `pull-plan` emits preparation metadata only:
     it never contacts a peer, reads referenced files or performs a transfer.
     Signature verification and any executor remain separate activation gates.
     SQLite/`-wal`/`-shm` paths always stay on the R9
     `sqlite-transit-sync`/`db-transit/<namespace>` route.
4. **Write outbound** —
   - Update this machine's slot `hosts/<HOST>/` with anything the other
     systems need (new lessons, changed setup, running long jobs).
   - Refresh agent-rule snapshots in `agents/` if local rule files changed
     since the last snapshot (R5: snapshots are copies OF local files, the
     local file stays authoritative).
   - Leave messages for other hosts/agents where action is needed on their
     side (`messages/to-<recipient>.md`, `[<HOST>/<agent> YYYY-MM-DD] …`).
   - This skill does not publish trusted-peer registries. A separately
     reviewed owner-only publisher may update only
     `hosts/<HOST>/trusted-peer-paths/registry.json`; keys, SSH files and
     referenced content never enter the yard.
5. **Bootstrap freshness (R8)** — if the yard's structure or the machine
   inventory changed today, update `BOOTSTRAP.md` accordingly.
6. **Mark the gate** — `python scripts/system_gap_daily_check.py mark` (appends
   today's row for `<HOST>`), and report to the user in 3–6 lines what came
   in, what went out, and anything needing their decision.

## Discipline

- Never edit foreign host slots (R1) — leave a message instead.
- Never place secrets or personal/case data in the yard (R6).
- Exact credential paths are allowed only as signed R10 registry metadata;
  credential values and key material remain forbidden.
- Archive, don't delete (R3) — except read messages (R4).
- Don't expand the ritual: it should stay a 2–5 minute routine. Anything
  bigger becomes a normal task outside the sync.
