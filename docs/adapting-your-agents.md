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

## 3. Register the ritual as a skill/command (optional)

- **Claude Code:** copy `SKILL.md` to `~/.claude/skills/system-gap-master/SKILL.md`
  (or expose it as a `/sync` command wrapper).
- **Codex / Gemini / others:** paste the SKILL.md steps into the tool's
  prompt/automation format — the routine is plain natural-language steps on
  purpose, with the gate script as the only tooling dependency.

## Multi-agent note

If several agents run on the SAME machine, they share the host slot and the
daily gate (one sync per day per machine, whichever agent gets there first).
Per-agent message channels (`messages/to-<agent>.md`) keep their inboxes
separate.
