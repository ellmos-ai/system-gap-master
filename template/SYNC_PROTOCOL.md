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

## Artifact types in this yard

| Artifact | Pattern | Notes |
|---|---|---|
| (extend as conventions emerge) | | |
