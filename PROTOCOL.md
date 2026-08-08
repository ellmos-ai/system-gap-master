# The system-gap-master Protocol

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
mark it. `scripts/system_gap_daily_check.py check|mark` automates the gate and can
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
(e.g. `*conflict*`, `*-<HOSTNAME>*` duplicates). Discovery is not proof of
canonicality. Automatic reconciliation is allowed only through the
policy-driven `conflict-copy-reconciler`: explicit allowlisted root,
authoritative canonical mapping, one mutating lease owner, stable
fingerprints, a deterministic safe merge class, local backup, atomic apply,
verification and rollback. Unknown or semantic conflicts remain untouched.
Every adapter starts as a read-only observer; exactly one host/root config may
be promoted to mutating owner. Signed plans and operation manifests, plus
no-symlink/reparse path checks, protect the handoff between scan and apply.
The conflict copy moves to a recoverable archive only after hash readback;
log the redacted receipt in `CONFLICT_REVIEW_LOG.md`.

### R8 — Bootstrap
`BOOTSTRAP.md` is the disaster-recovery and new-device runbook: which tools to
install, which snapshots to pull from `agents/`, which slot to create, how to
register the daily gate. Keep it current — its value is exactly the day you
need it. Rule of thumb: whenever the yard's structure changes, ask "would
BOOTSTRAP still bring up a fresh machine?"

### R9 — Structured payloads (databases, hot binaries)

The yard carries documents, not live data stores. **Never place a live SQLite
database (or its `-wal`/`-shm` files) or any file with open handles into the
yard** — file-sync providers corrupt hot files and create conflict copies.
For syncing application state (databases) between machines, use a
snapshot-based transit tool: it publishes closed, checksum-verified snapshots
into a transit directory and each node pulls and merges into its own local
database. Companion module from the same family: **sqlite-transit-sync**
(verified snapshots, SHA-256 manifests, per-node pull state, pluggable merge
policies).

Convention: give such tools their own **tool-owned zone** `db-transit/<namespace>/`
at the yard root. Tool-owned zones are exempt from R1 and R3 — the tool
manages ownership and lifecycle itself; agents do not hand-edit or archive
anything inside them during the daily ritual.

### R10 — Trusted-peer path metadata and preparation

A host-owned registry may advertise exact SFTP paths in
`hosts/<OWN-HOST>/trusted-peer-paths/registry.json`. The document names a
stable `path_id`, exact remote path, read-only SFTP endpoint, network label,
known-host pin and `allowed_peer_ids`. Every path explicitly declares
`metadata_type=path-location` and `content_included=false`; credential values,
file content, private keys, tokens and passwords are forbidden.

The `trusted-peer-paths` planner is a read-only preflight. It derives the
foreign registry path from the trusted host ID, validates owner slot,
schema/version, revision, freshness/expiry, an out-of-band pinned signature
reference, canonical payload digest, known-host pin, peer permission and an
exact local remote-path allowlist. It neither publishes nor authenticates a
detached signature, contacts a peer, invokes SSH/SFTP, reads any referenced
file, writes the yard, enables `direct_pull` or copies bytes.

For an explicitly enabled ordinary file it may emit a deterministic,
non-executable pull-preparation receipt. Destinations must be absent,
host-local, allowlisted and outside the yard. `direct` and `private-overlay` are
provider-neutral labels only. Directory transfer needs a separate reviewed
adapter. Live SQLite paths and `.db`, `.sqlite`, `.sqlite3`, `-wal`, `-shm`
payloads may be advertised only as `kind=database/sqlite` with
`direct_pull=false` and `adapter=sqlite-transit-sync`; their bytes still use
the R9 `db-transit/<namespace>` snapshot workflow.

The separate optional `trusted-peer-sftp-executor` implements the client-side
execution gates: detached registry and one-shot-grant verification, local
credential binding, presented host-key matching, durable anti-replay state,
bounded single-file SFTP read, no-replace commit and local redacted receipts.
It remains inactive until a host separately provisions its keys, signatures,
route and server-side read-only ACL and supplies an exact local configuration.

The preparation contract and threat model are in
`docs/trusted-peer-path-registry.md`; the execution contract is in
`docs/trusted-peer-sftp-executor.md`; schemas are in `schemas/`.

## Artifact naming conventions

| Artifact | Pattern | Notes |
|---|---|---|
| Agent rule snapshot | `agents/<AGENT>_<HOST>_snapshot.md` | merge, never overwrite |
| Message channel | `messages/to-<recipient>.md` | recipient deletes after reading |
| Automation export | `hosts/<HOST>/<tool>-automations_<YYYY-MM-DD>/` | manifest + copies + extracted prompts |
| Setup/config summary | `hosts/<HOST>/<TOOL>_CONFIG_<YYYY-MM-DD>.md` | summaries, not raw config dumps (paths/trust state are not portable) |
| Topic document | `<TOPIC>_<YYYY-MM-DD>.md` at root | living status source; archive when obsolete |
| Handoff/runbook | `<SYSTEM>_HANDOFF.md` at root | how to operate something from another machine |
| Database transit zone | `db-transit/<namespace>/` at root | tool-owned (R9): managed by a snapshot tool like sqlite-transit-sync, not by hand |
| Trusted peer path registry | `hosts/<HOST>/trusted-peer-paths/registry.json` | host-owned path metadata only (R10); exact paths allowed, content/keys forbidden |

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
- **Discovery never establishes canonicality.** A shorter filename, newer
  timestamp or provider suffix can identify a candidate, but only a manifest,
  pointer, registry or documented writer policy may authorize mutation.
- **Prepared discovery is not transport authority.** A pinned signature
  reference and digest are not cryptographic verification. The optional
  executor additionally requires signed registry and one-shot grant plus
  host-local SSH material; server-side ACL and route provisioning remain
  separate host activation gates.
