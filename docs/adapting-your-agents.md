# Adapting your agents to the yard

system-gap-master is convention + a tiny gate script; the actual work is done by
whatever agents you run. Wire it in three places.

## 1. Rule-file section (all agents)

Add a section like this to each machine's agent rule file (CLAUDE.md,
AGENTS.md, GEMINI.md, .codex/GPT.md, …) — adapt paths and host name:

```markdown
## Cross-machine sync (system-gap-master)

- Yard: <path to your synced folder>   ·   This machine's slot: hosts/<HOST>/
- Rules: SYNC_PROTOCOL.md in the yard. Slot rule: write only our own slot +
  agents/ + messages/; never edit foreign slots. No secrets in the yard.
- Once per day run the sync ritual (system-gap-master SKILL.md): gate via
  `python <repo>/scripts/system_gap_daily_check.py check|mark`
  (or SYSTEM_GAP_MASTER_DIR env var). Read messages/to-<HOST>.md and
  messages/to-<agent>.md, delete entries after reading.
```

## 2. Session-start reminder (optional, recommended)

Wire the gate into your agent's session-start hook so the reminder fires at
most once per day. Example for Claude Code (`~/.claude/settings.json`):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python <path-to-repo>/scripts/system_gap_daily_check.py check --dir <path-to-yard>"
          }
        ]
      }
    ]
  }
}
```

The script exits 0 with a quiet message when today's sync is done (or the
gate file doesn't exist), and prints a gentle reminder when the sync is due.
Other agents: any "run a command at session start" mechanism works the same
way; a shell profile line is a valid low-tech fallback.

## 3. Concrete Setup Examples per Agent Family

### A. Claude Code (Anthropic Family)

1. **Global/Local Rule File (`CLAUDE.md`):**
   ```markdown
   ## System Gap Master (Cross-machine Sync)
   - Yard path: `<SYNC_DIR>`
   - Host slot: `hosts/<HOST>/`
   - Daily Gate: Run `python <LOCAL_REPO>/scripts/system_gap_daily_check.py check` at session start.
   ```

2. **SessionStart Hook (`~/.claude/settings.json`):**
   ```json
   {
     "hooks": {
       "SessionStart": [
         {
           "hooks": [
             {
               "type": "command",
             "command": "python <LOCAL_REPO>/scripts/system_gap_daily_check.py check"
             }
           ]
         }
       ]
     }
   }
   ```

3. **Skill Installation:**
   Copy `SKILL.md` to `~/.claude/skills/system-gap-master/SKILL.md` to trigger via `/system-gap-master`.

---

### B. Codex CLI & Codex Desktop (OpenAI Family)

1. **Global Instruction File (`<HOME>/CLAUDE.md` or `.codex/GPT.md`):**
   ```markdown
   ## System Gap Sync Protocol
   - Check daily sync status: `python <LOCAL_REPO>/scripts/system_gap_daily_check.py check`
   - Read inbox: `messages/to-codex.md` and `messages/to-<HOST>.md`.
   - Update state snapshot in `hosts/<HOST>/STATE.md`.
   ```

2. **Automation Startup Gate:**
   Configure `safe-start-for-codex` or `.codex/config.toml` to execute `system_gap_daily_check.py check` before starting scheduled tasks.

---

### C. Gemini / Antigravity (Google / AGY Family)

1. **System Prompt / User Rules (`GEMINI.md` / `user_rules`):**
   ```markdown
   ## Cross-Machine Sync Rule
   - Sync Yard: `<SYNC_DIR>`
   - Check daily gate status via `system_gap_daily_check.py check`.
   - If sync is due, read inbox messages in `messages/to-gemini.md` and update `hosts/<HOST>/STATE.md`.
   ```

2. **Launcher / Startup Script (`START-AGY.bat`):**
   ```cmd
   @echo off
   python <LOCAL_REPO>\scripts\system_gap_daily_check.py check
   agy chat
   ```

---

### D. Custom Python Scripts & Subagent Frameworks

1. **Python API Integration:**
   ```python
   from pathlib import Path
   from system_gap_daily_check import check_sync_due, mark_sync_done

   yard_dir = Path("<SYNC_DIR>")
   if check_sync_due(yard_dir):
       print("Daily sync is due. Processing inbox...")
       # Perform sync tasks
       mark_sync_done(yard_dir)
   ```

---

## Multi-agent note

If several agents run on the SAME machine, they share the host slot and the
daily gate (one sync per day per machine, whichever agent gets there first).
Per-agent message channels (`messages/to-<agent>.md`) keep their inboxes
separate.

For conflict-copy maintenance, all desktop-agent apps may have the
provider-neutral task from
`template/runners/desktop-agent/conflict-copy-reconciler.task.json`, but only
one app is the mutating owner for a given host/root. The others are observers
that inspect redacted receipts and may request a policy-governed takeover.
Observers run the plan command only; the engine rejects mutation when their
config says `observer`. All apps for one host/root use the same protected
host-local config/state directory so the path-derived lease is shared.
Register through the provider's supported native UI/API. A private automation
registry file is not an API; do not edit it directly just to complete setup.

## Trusted peer path registries

Agents may validate a pre-authorized R10 registry and prepare a no-transfer
receipt. This is metadata preflight, not SFTP activation:

```markdown
## Trusted peer paths

- Local trust config: <HOST_LOCAL_CONFIG_OUTSIDE_THE_YARD>
- Read another host only through `trusted-peer-paths validate|list|resolve`.
- Review `pull-plan`; it is always non-executable and contacts no peer.
- Never bypass peer/path/pin/expiry/destination gates. Detached-signature
  verification, SSH ACL/authentication, route choice and any executor remain
  separate activation gates.
- SQLite/`-wal`/`-shm` is discovery-only and always uses
  `sqlite-transit-sync` through `db-transit/<namespace>`.
```

This CLI never publishes or writes a slot. A separately reviewed owner-only
publisher is outside this capability. Authentication material and referenced
content remain host-local and are never read by the preflight. The registry
contains exact path metadata, endpoint details, pins and allowed peer IDs
only. Full setup and threat model:
[`trusted-peer-path-registry.md`](trusted-peer-path-registry.md).
