# messages/ — agent-to-agent and machine-to-machine channels

- One file per recipient: `to-<recipient>.md` — recipient is a host
  (`to-LAPTOP.md`) or an agent (`to-codex.md`).
- Entry format: `[<from> YYYY-MM-DD] message` (from = host and/or agent).
- **The recipient deletes entries after reading.** Anything of lasting value
  is moved into the recipient's own rule files or docs FIRST, then the entry
  goes. An empty channel means "nothing new" — that is the healthy state.
- Use messages instead of editing a foreign host slot (PROTOCOL rule R1).
