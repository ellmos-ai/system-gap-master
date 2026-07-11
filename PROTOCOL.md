# The sync-master Protocol

The full specification of the protocol. The copy-ready starter files live in
`template/`; this document explains the rules and the reasoning behind them.

## The problem

You work on several machines (laptop, workstation, home server) and with
several AI agents (Claude Code, Codex, Gemini CLI, …). Each machine and each
agent accumulates knowledge the others need: setup fixes, credentials
locations (not the credentials!), lessons learned, running jobs, decisions.
Without a shared place, every machine drifts into its own silo and every new
device starts from zero.

## The idea

One shared folder — synced by whatever you already use (OneDrive, Dropbox,
Syncthing, Nextcloud, a NAS share, even a git repo) — acting as the **transfer
yard** between systems. Not a workspace: things arrive here, get picked up by
the target system, and are then archived. Three mechanisms keep it working
without a server and without merge conflicts:

1. **Slot rule** — every machine writes only to its own host slot and to
   shared drop zones; it never edits another machine's slot.
2. **Daily ritual** — once per day per machine, an agent runs the sync
   routine (see `SKILL.md`): update your own exports, read the other slots,
   integrate what is new. A log file gates this to once a day.
3. **Named artifact types** — recurring payloads (agent-rule snapshots,
   automation exports, messages, runbooks) have documented naming
   conventions, so every agent knows what a file is without opening it.

## Folder layout

```
<SYNC_DIR>/
  SYNC_PROTOCOL.md         local copy of the protocol summary (from template/)
  BOOTSTRAP.md             runbook: bring up a NEW machine from this folder
  DAILY_SYNC_LOG.md        gate: one sync per day per host
  CONFLICT_REVIEW_LOG.md   gate: daily check for sync-provider conflict copies
  agents/                  snapshots of per-machine agent rule files
  messages/                agent-to-agent / machine-to-machine messages
  hosts/
    <HOST-A>/              slot: only HOST-A writes here
    <HOST-B>/              slot: only HOST-B writes here
  _archive/                integrated/expired items (nothing gets deleted raw)
```

Naming: host slots use the machine name (e.g. `LAPTOP`, `STUDIO-M1`). Keep
every name ASCII and path-safe.

## The rules

### R1 — Slot ownership
A machine writes only inside `hosts/<OWN-HOST>/` and the shared drop zones
(`agents/`, `messages/`, root-level topic documents it authored). It reads
everything. It never edits files inside a foreign host slot — if something
there needs correction, leave a message (R4).

### R2 — Daily ritual, gated
Once per day per host, run the routine in `SKILL.md`. The gate is
`DAILY_SYNC_LOG.md`: if today's row for this host exists, skip; when done,
mark it. `scripts/sync_daily_check.py check|mark` automates the gate and can
be wired into an agent's session-start hook (see `docs/adapting-your-agents.md`).

### R3 — Transfer yard, not storage
Every file in the yard is on its way somewhere. After a target system
integrates an item, the item moves to `_archive/` (never raw-delete — the
archive is the audit trail). Root-level topic documents that serve as living
status sources (e.g. "server X decommissioned") stay until obsolete.

### R4 — Messages
`messages/to-<recipient>.md` — recipient is a host or an agent name. Append
entries as `[<from> YYYY-MM-DD] message`. **The recipient deletes entries
after reading**; anything of lasting value is moved into the recipient's own
rule files or docs first. Keeps the channel empty-by-default, so anything
present is genuinely new.

### R5 — Agent-rule snapshots
`agents/<AGENT>_<HOST>_snapshot.md` (e.g. `CLAUDE_LAPTOP_snapshot.md`) —
periodic copies of per-machine rule files (CLAUDE.md, AGENTS.md, GEMINI.md, …)
so other machines can refresh their local, non-synced rules. Snapshots are
**reference material: merge, never overwrite** — the local file on the target
machine stays authoritative.

### R6 — No secrets in the yard
Never place credentials, tokens, keys or personal/case data in the sync
folder. Reference their local locations instead ("token lives in
`~/.config/x/`"). The yard travels through a sync provider you may not fully
control; treat it as semi-trusted. If you must move something sensitive,
encrypt it and pass the passphrase out-of-band.

### R7 — Conflict copies
File-sync providers create conflict copies on concurrent edits (the slot rule
makes this rare, but gates and shared documents can race). Check once per day
(gate: `CONFLICT_REVIEW_LOG.md`): search for the provider's conflict pattern
(e.g. `*conflict*`, `*-<HOSTNAME>*` duplicates), merge the content, remove the
copy, log one line.

### R8 — Bootstrap
`BOOTSTRAP.md` is the disaster-recovery and new-device runbook: which tools to
install, which snapshots to pull from `agents/`, which slot to create, how to
register the daily gate. Keep it current — its value is exactly the day you
need it. Rule of thumb: whenever the yard's structure changes, ask "would
BOOTSTRAP still bring up a fresh machine?"

## Artifact naming conventions

| Artifact | Pattern | Notes |
|---|---|---|
| Agent rule snapshot | `agents/<AGENT>_<HOST>_snapshot.md` | merge, never overwrite |
| Message channel | `messages/to-<recipient>.md` | recipient deletes after reading |
| Automation export | `hosts/<HOST>/<tool>-automations_<YYYY-MM-DD>/` | manifest + copies + extracted prompts |
| Setup/config summary | `hosts/<HOST>/<TOOL>_CONFIG_<YYYY-MM-DD>.md` | summaries, not raw config dumps (paths/trust state are not portable) |
| Topic document | `<TOPIC>_<YYYY-MM-DD>.md` at root | living status source; archive when obsolete |
| Handoff/runbook | `<SYSTEM>_HANDOFF.md` at root | how to operate something from another machine |

Extend the table in your local `SYNC_PROTOCOL.md` as your yard grows — the
convention that a convention EXISTS is the load-bearing part.

## Why this works (design notes)

- **Write-ownership beats merge tooling.** The slot rule removes the need for
  three-way merges; the only shared-write files are append-only logs and
  delete-after-read message channels, both of which tolerate races well.
- **A gate makes rituals cheap.** Agents check the gate at session start; the
  reminder fires at most once a day, so the ritual never becomes noise.
- **Provider-agnostic by design.** Nothing here depends on OneDrive/Dropbox
  specifics; the conflict-copy check (R7) is the only provider-facing part.
- **Archive over delete.** Cheap storage, expensive reconstructions.
