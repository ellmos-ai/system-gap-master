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

Optional R10 path publication lives at
`hosts/<HOST>/trusted-peer-paths/registry.json`. Only a separately reviewed
owner-only publisher may create it; the `trusted-peer-paths` CLI is read-only.
Never copy a foreign registry into this slot. Exact approved credential paths
are metadata, but referenced content, credential values, signing keys and SSH
authentication material stay host-local. See
`_slot-template/trusted-peer-paths/README.md`.

Create a new slot with a one-line `README.md` saying what the machine is —
and add it to the slot table in `SYNC_PROTOCOL.md` and to `BOOTSTRAP.md`.
