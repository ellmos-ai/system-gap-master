# sync-master

**A serverless sync yard for people who run several machines and several AI
agents.** One shared folder — synced by whatever you already use (OneDrive,
Dropbox, Syncthing, a NAS, even git) — plus three conventions that keep
laptop, workstation and home server from drifting into silos: a **slot rule**
(each machine writes only its own slot — no merge conflicts by design), a
**gated daily ritual** your agents run in 2–5 minutes, and a **bootstrap
runbook** that can bring up a fresh machine from the yard alone.

Part of the cross-agent infrastructure family:
[lock-master](https://github.com/dev-bricks/lock-master) (locks) ·
[ticket-master](https://github.com/dev-bricks/ticket-master) (tickets) ·
**sync-master** (cross-machine sync).

> **Deutsch:** sync-master ist die nutzerneutrale, offene Fassung eines seit
> Monaten produktiv laufenden Cross-System-Sync-Ordners: mehrere Rechner,
> mehrere KI-Agenten (Claude/Codex/Gemini), EIN gemeinsamer Übergaberaum —
> ohne Server, über einen beliebigen Datei-Sync. Slot-Regel gegen Konflikte,
> tägliches Ritual mit Einmal-pro-Tag-Gate, Nachrichtenkanäle zwischen
> Agenten, Bootstrap-Runbook für neue Geräte.

## Why not X?

| Existing tools | What they solve | What they don't |
|---|---|---|
| agentsync & friends (config synchronizers) | one config source → many AI tools, same machine | knowledge/state between **machines** |
| runtime shared-memory layers | agents talking on one machine, same session | persistence across devices and days |
| dotfiles repos | config files | agent-centric knowledge, messages, runbooks, rituals |
| memory MCPs / cloud memory | one agent's memory | multi-agent, multi-machine, provider-neutral, inspectable files |

sync-master's niche: **multi-machine + multi-agent + serverless + plain
files.** Everything is human-readable Markdown you can audit, grep and sync
with anything.

## What's in the box

```
PROTOCOL.md          the full protocol (8 rules) + design notes
SKILL.md             the daily ritual as an agent-neutral skill
CHANGELOG.md         notable public maintenance changes
llms.txt             machine-readable summary for agents and search tools
ellmos-module.v2.json  ecosystem module metadata
template/            copy-ready yard skeleton:
  SYNC_PROTOCOL.md     yard-local protocol summary + slot table
  BOOTSTRAP.md         new-device / disaster-recovery runbook
  DAILY_SYNC_LOG.md    once-per-day-per-host gate
  CONFLICT_REVIEW_LOG.md  daily conflict-copy sweep gate
  agents/  messages/  hosts/  _archive/   (each with its rules README)
scripts/sync_daily_check.py   the gate (check|mark), zero dependencies
docs/adapting-your-agents.md  wiring for CLAUDE.md/AGENTS.md/GEMINI.md + hooks
```

## Quick start

```bash
# 1) Create the yard inside your synced storage and copy the skeleton
cp -r template/ /path/to/your/synced/storage/SYNC/

# 2) Fill in SYNC_PROTOCOL.md (slot table) and create your first slot
mkdir /path/to/.../SYNC/hosts/<YOUR-HOST>

# 3) Point your agents at it (see docs/adapting-your-agents.md)
setx SYNC_MASTER_DIR "C:\path\to\SYNC"     # Windows
export SYNC_MASTER_DIR=/path/to/SYNC       # macOS/Linux

# 4) Daily, per machine (your agent does this via SKILL.md):
python scripts/sync_daily_check.py check   # gate: due today?
# ... run the ritual (read inbound, write outbound) ...
python scripts/sync_daily_check.py mark
```

## The eight rules (short)

1. **Slot rule** — write your own slot only; never edit foreign slots.
2. **Daily ritual, gated** — once per day per host, 2–5 minutes.
3. **Transfer yard, not storage** — integrated items move to `_archive/`.
4. **Messages** — `messages/to-<recipient>.md`; recipient deletes after reading.
5. **Agent snapshots** — merge on the target, never overwrite local rules.
6. **No secrets in the yard** — reference local locations instead.
7. **Conflict-copy sweep** — daily, provider-agnostic.
8. **BOOTSTRAP.md stays current** — it must always bring up a fresh machine.

Full reasoning: [PROTOCOL.md](PROTOCOL.md).

## Companion tools

The yard carries documents; it deliberately does NOT carry live databases
(rule 9: hot SQLite/WAL files + file-sync providers = corruption). To sync
application state between machines, pair the yard with a snapshot-based
transit tool in a tool-owned `db-transit/<namespace>/` zone — from the same
module family: **sqlite-transit-sync** (local-first SQLite sync through
verified snapshots, SHA-256 manifests and pluggable merge policies;
publication pending, link will follow). The yard is the transport; the
transit tool owns integrity and merging.

## Part of the ellmos stack family

sync-master is deliberately both: a standalone dev tool you can drop into any
project, and a core module of the ellmos stack family.

Core module of [ellmos-ai/agent-ops-stack](https://github.com/ellmos-ai/agent-ops-stack)
(role `file-sync`); family/catalog: [ellmos-ai/stacks](https://github.com/ellmos-ai/stacks);
org overview: [ellmos-ai](https://github.com/ellmos-ai). Companion module for live
SQLite state (role `sync.database`): **sqlite-transit-sync** — see
[Companion tools](#companion-tools) above; publication under
`dev-bricks/sqlite-transit-sync` is still pending.

## Security & privacy notes

- The yard travels through your sync provider: treat it as **semi-trusted**.
  Never put credentials, tokens or personal/case data in it (rule 6) — the
  templates and the skill repeat this at every write point.
- Everything is plain files: your existing backup, encryption and access
  control apply unchanged.

## Provenance & license

Distilled 2026 from a production cross-system sync folder that has been
coordinating multiple machines and agents (Claude, Codex, Gemini) since
spring 2026 — generalized, user-neutral rebuild; no production data included.
MIT license — covers code, templates and documentation alike.
