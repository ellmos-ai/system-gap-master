---
name: sync-master
description: Daily cross-machine sync ritual for multi-agent setups. Use this when the user says "/sync", "run the sync", "check the sync yard", when a session-start reminder says today's sync is missing, or when bringing knowledge from one machine/agent to another via the shared sync folder.
---

# sync-master — the daily sync ritual (v1)

You are executing the daily synchronization between this machine and the
other machines/agents of this user, via the shared sync folder (the "yard").
Rules live in `PROTOCOL.md` (repo) / `SYNC_PROTOCOL.md` (yard copy) — the
slot rule (R1) and the no-secrets rule (R6) are non-negotiable.

**Configuration:** the yard path comes from (in order) the `SYNC_MASTER_DIR`
environment variable, the user's agent rule file (CLAUDE.md/AGENTS.md), or
asking the user once. `<HOST>` is this machine's name.

## Steps

1. **Gate check** — `python scripts/sync_daily_check.py check` (or read
   `DAILY_SYNC_LOG.md` directly). Today's row for `<HOST>` exists → say so and
   stop; the ritual runs once per day.
2. **Conflict sweep (R7)** — look for sync-provider conflict copies in the
   yard (patterns like `*conflict*` or duplicated names with a host suffix).
   Found → merge content into the canonical file, remove the copy, log one
   line in `CONFLICT_REVIEW_LOG.md`.
3. **Read inbound** —
   - `messages/to-<HOST>.md` and `messages/to-<your-agent-name>.md`: act on
     each entry, move lasting information into local rule files/docs, then
     DELETE the read entries (empty channel = nothing new).
   - Other hosts' slots (`hosts/<OTHER>/`) and root topic documents: skim for
     new items since the last sync; integrate what concerns this machine
     (install, configure, note down), then move fully-integrated items to
     `_archive/` — but only items addressed to this machine or marked done
     everywhere they apply.
4. **Write outbound** —
   - Update this machine's slot `hosts/<HOST>/` with anything the other
     systems need (new lessons, changed setup, running long jobs).
   - Refresh agent-rule snapshots in `agents/` if local rule files changed
     since the last snapshot (R5: snapshots are copies OF local files, the
     local file stays authoritative).
   - Leave messages for other hosts/agents where action is needed on their
     side (`messages/to-<recipient>.md`, `[<HOST>/<agent> YYYY-MM-DD] …`).
5. **Bootstrap freshness (R8)** — if the yard's structure or the machine
   inventory changed today, update `BOOTSTRAP.md` accordingly.
6. **Mark the gate** — `python scripts/sync_daily_check.py mark` (appends
   today's row for `<HOST>`), and report to the user in 3–6 lines what came
   in, what went out, and anything needing their decision.

## Discipline

- Never edit foreign host slots (R1) — leave a message instead.
- Never place secrets or personal/case data in the yard (R6).
- Archive, don't delete (R3) — except read messages (R4).
- Don't expand the ritual: it should stay a 2–5 minute routine. Anything
  bigger becomes a normal task outside the sync.
