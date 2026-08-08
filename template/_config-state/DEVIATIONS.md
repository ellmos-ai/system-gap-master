# Configuration deviations

This file is hand-maintained. `scripts/config_snapshot.py report` compares
only the keys named by the provider table and marks an explained difference
with `~` in `CONFIG-STATE.md`.

## How to document a difference

Add one heading whose exact form is `### \`provider.key\`` (or
`### \`provider.env.key\``), followed by the rationale and, where useful, the
date and owner. Keep secrets, tokens, and private paths out of this file.

Example:

### `agent-one.model`

The release slot intentionally uses the smaller model until the next review.
