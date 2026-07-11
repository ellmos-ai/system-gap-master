# BOOTSTRAP — bring up a NEW machine from this yard

> Disaster-recovery / new-device runbook. Keep this current: whenever the
> yard's structure or the tool inventory changes, update this file. Its value
> is exactly the day you need it.

## 1. Prerequisites

- [ ] Install the sync provider (<YOUR SYNC PROVIDER>) and sync this folder.
- [ ] Install the agents/tools this setup uses:
      <list: e.g. Claude Code, Codex CLI, Gemini CLI, Python 3.x, git, gh>
- [ ] Log the tools in (interactive logins cannot be synced).

## 2. Create this machine's identity

- [ ] Pick the host name (short, ASCII, e.g. `LAPTOP-2`).
- [ ] Create the slot: `hosts/<NEW-HOST>/README.md` (one line: what this
      machine is).
- [ ] Add the machine to the slot table in `SYNC_PROTOCOL.md`.

## 3. Restore agent rules

- [ ] For each agent, open the newest `agents/<AGENT>_<HOST>_snapshot.md`
      from the machine most similar to this one.
- [ ] MERGE the relevant parts into this machine's local rule files
      (CLAUDE.md / AGENTS.md / GEMINI.md …) — adapt absolute paths; the local
      file is authoritative, the snapshot is reference.
- [ ] Add the sync-yard section to the local rule file (see sync-master
      `docs/adapting-your-agents.md`): yard path, slot name, daily ritual.

## 4. Register the daily gate

- [ ] Set `SYNC_MASTER_DIR` (user environment variable) to this folder.
- [ ] Optional: wire `scripts/sync_daily_check.py check` into the agent's
      session-start hook for the once-a-day reminder.

## 5. First sync

- [ ] Run the sync-master ritual (SKILL.md): read all slots and root topic
      documents, integrate what applies to this machine, announce the new
      machine via `messages/to-<other-hosts>.md`, mark the gate.

## Machine-specific restore notes

<add per-machine notes here: services to reinstall, scheduled tasks,
credential locations (locations only — never the secrets themselves)>
