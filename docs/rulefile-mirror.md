# Rule-file mirror

`scripts/rulefile_mirror.py` keeps a yard's snapshots of local agent rule files
(`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, ...) current and makes drift visible.

## Why

Rule files normally live in local, unsynced paths. Other machines only see them
if someone copies them into the yard by hand, and a hand-copied snapshot rots
silently: the file is still there, it is just old. The yard template already
asks for snapshots in `agents/`; this script turns that convention into a
measured, repeatable step.

## Division of labour

| What | Where | Maintained by |
|---|---|---|
| Which file belongs to which actor, which one is central | `agents/registry/<slot>.json` | a human (it is a decision) |
| Hash, size, age, drift | `agents/registry/<slot>.state.json` | the script (measured) |
| Overview of all slots | `agents/RULEFILES.md` | the script (`report`) |
| Snapshots | the `mirror` path of each entry, always under `agents/` | the script (`mirror`) |

Start from [`examples/rulefile-registry.example.json`](../examples/rulefile-registry.example.json).
Exactly one entry has `role: "central"`; the check fails otherwise. An entry
without `mirror` is measured but not copied.

## Usage

```bash
export SYSTEM_GAP_MASTER_DIR=/path/to/SYNC     # or pass --yard-root
export SYSTEM_GAP_SLOT=laptop-1                # or pass --slot

python scripts/rulefile_mirror.py check        # measure, write the state file
python scripts/rulefile_mirror.py mirror --dry-run
python scripts/rulefile_mirror.py mirror       # refresh drifted/missing snapshots
python scripts/rulefile_mirror.py all          # mirror + report
python scripts/rulefile_mirror.py check --fail-on-drift   # exit 2 for hooks/CI
```

Exit codes: `0` ok, `1` registry problem or no/ambiguous central file, `2`
drift with `--fail-on-drift`.

States: `current`, `drift`, `mirror-missing`, `source-missing`,
`no-mirror-wanted`, `no-source`.

## Safety properties

- **One-way.** Local file -> yard snapshot, never back. Rule files often end
  with a live message board; copying a foreign snapshot back would delete open
  messages. Merge foreign snapshots by hand.
- **Contained.** A `mirror` path must be relative and stay under `agents/`;
  `..`, absolute paths and other directories are refused. A slot name may only
  contain `A-Za-z0-9._-`.
- **No secrets in the yard.** A source containing a real secret *format*
  (provider API keys, GitHub/npm/Slack tokens, AWS keys, private-key headers)
  is not mirrored; only line numbers and the kind are printed, never the value.
- **No silent loss.** If an overwrite would remove lines that exist only in the
  snapshot, the old snapshot is saved to `agents/_previous/` (one per file and
  day) and the count is reported.
- **Atomic.** Files are encoded first, written to a sibling temp file and moved
  into place with `os.replace`.
- **Hand-written heads.** With `"mode": "annotated"` a snapshot keeps its head
  and only the body below the line `<!-- MIRROR-BODY BELOW -->` is replaced. No
  marker means no write.

## Boundaries

- It does not create, repair or install rule files and does not choose which
  provider file is canonical. That belongs to an installer or an agent bridge;
  see the boundary note in [`instance-manager.md`](instance-manager.md).
- It does not verify that another machine ever read a snapshot.
- Run it from the daily ritual or a hook; it has no scheduler of its own.
