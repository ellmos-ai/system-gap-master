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
- [ ] Add the sync-yard section to the local rule file (see system-gap-master
      `docs/adapting-your-agents.md`): yard path, slot name, daily ritual.

## 4. Register the daily gate

- [ ] Set `SYSTEM_GAP_MASTER_DIR` (user environment variable) to this folder.
- [ ] Optional: wire `scripts/system_gap_daily_check.py check` into the agent's
      session-start hook for the once-a-day reminder.
- [ ] If automatic conflict reconciliation is enabled, create a host-local
      config from `examples/conflict-reconciler.config.example.json`. Keep
      state/backups outside the synced yard, run `canary`, and register all
      desktop-agent tasks as observers first.
- [ ] Assign at most one `mutating-owner` per root. On macOS, instantiate and
      lint the runner/LaunchAgent templates under `template/runners/macos/`.
- [ ] Generate one persistent high-entropy `receipt_salt`, protect the
      host-local config/state directory, and make every adapter for the same
      root use that same state directory. Start every adapter as `observer`.
- [ ] Optional trusted-peer paths: create host-local config/entries and one
      high-entropy HMAC key outside the yard; provision each publisher key
      out-of-band only to allowed peers, pin peer SSH host keys in a
      host-local `known_hosts`, pin the absolute OpenSSH `sftp` executable,
      and create allowlisted import roots.
- [ ] Run `trusted-peer-paths publish` for this host's own slot, then verify
      it from one peer with `validate`, `list`, `resolve` and `pull-plan`.
      Test an ordinary-file `pull --apply` only after the SSH account is
      read-only. Keep SQLite on the R9 `sqlite-transit-sync` adapter.

## 5. First sync

- [ ] Run the system-gap-master ritual (SKILL.md): read all slots and root topic
      documents, integrate what applies to this machine, announce the new
      machine via `messages/to-<other-hosts>.md`, mark the gate.

## Machine-specific restore notes

<add per-machine notes here: services to reinstall, scheduled tasks,
credential locations (locations only — never the secrets themselves)>
