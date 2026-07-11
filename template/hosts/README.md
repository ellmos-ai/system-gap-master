# hosts/ — one slot per machine

Every machine gets exactly one folder here, named after the machine
(short, ASCII, e.g. `LAPTOP`, `STUDIO-M1`).

**Slot rule (PROTOCOL R1):** a machine writes ONLY inside its own slot (plus
the shared drop zones `agents/`, `messages/`, and root topic documents it
authored). It reads all slots. It never edits a foreign slot — corrections
travel as messages.

Typical slot content: exports for the other systems, lessons learned,
automation/setup packages (`<tool>-automations_<YYYY-MM-DD>/`), notes about
long-running jobs other machines should know about.

Create a new slot with a one-line `README.md` saying what the machine is —
and add it to the slot table in `SYNC_PROTOCOL.md` and to `BOOTSTRAP.md`.
