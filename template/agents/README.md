# agents/ — per-machine agent-rule snapshots

Periodic copies of each machine's local agent rule files, so other machines
can refresh their non-synced local rules.

- Naming: `<AGENT>_<HOST>_snapshot.md` (e.g. `CLAUDE_LAPTOP_snapshot.md`,
  `AGENTS_STUDIO_snapshot.md`).
- Refresh during the daily ritual whenever the local rule file changed.
- **Snapshots are reference material: MERGE into the target machine's local
  file, never overwrite it.** Adapt absolute paths — they rarely transfer.
- No secrets (PROTOCOL rule R6) — snapshots travel through the sync provider.
