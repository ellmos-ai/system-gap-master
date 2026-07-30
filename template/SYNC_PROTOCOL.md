# Cross-machine sync yard — local protocol summary

> Instantiated from system-gap-master (https://github.com/dev-bricks/system-gap-master).
> Full rules and reasoning: PROTOCOL.md in the repo. This file is the
> yard-local summary — extend the artifact table below as your yard grows.

## Purpose

This folder is the **transfer yard** between the machines and AI agents of
this user. It is synced by <YOUR SYNC PROVIDER>. It is not a workspace:
items arrive, get integrated by their target system, then move to `_archive/`.

## Machines (slots)

| Slot | Machine | Notes |
|---|---|---|
| `hosts/<HOST-A>/` | <describe> | |
| `hosts/<HOST-B>/` | <describe> | |

## The rules (short form)

1. **Slot rule:** write only your own slot + shared drop zones; never edit a
   foreign slot — leave a message instead.
2. **Daily ritual:** once per day per host (gate: `DAILY_SYNC_LOG.md`,
   tooling: `scripts/system_gap_daily_check.py`); routine: SKILL.md of system-gap-master.
3. **Transfer yard:** integrated items go to `_archive/`, never raw-delete.
4. **Messages:** `messages/to-<recipient>.md`, `[<from> YYYY-MM-DD] …`;
   recipient deletes after reading.
5. **Agent snapshots:** `agents/<AGENT>_<HOST>_snapshot.md` — merge on the
   target, never overwrite local rule files.
6. **No secrets** in the yard — reference local locations instead.
7. **Conflict copies:** check daily (gate: `CONFLICT_REVIEW_LOG.md`).
8. **BOOTSTRAP.md** must always be able to bring up a fresh machine.
9. **Structured payloads:** live SQLite/WAL/SHM files use a tool-owned
   `db-transit/<namespace>` snapshot adapter, never direct file sync.
10. **Trusted peer paths:** a host-owned
    `hosts/<HOST>/trusted-peer-paths/registry.json` contains path metadata
    only. Peers validate owner/expiry/signature-reference/pin/path policy and
    may emit a non-executable preparation receipt. Network transfer remains
    a separate activation. SQLite stays on the R9 snapshot adapter.

## Artifact types in this yard

| Artifact | Pattern | Notes |
|---|---|---|
| Trusted peer path registry | `hosts/<HOST>/trusted-peer-paths/registry.json` | host-owned metadata; read-only validation and no-transfer preparation only |
| (extend as conventions emerge) | | |
