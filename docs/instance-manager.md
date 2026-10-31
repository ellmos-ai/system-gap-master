# Yard instance lifecycle manager

`yard-instance-manager` is the controlled deployment boundary between a local
`system-gap-master` repository clone and a live shared yard. The yard is not a
Git checkout. Generic templates are built and tested in the clone, then a
saved plan can seed or update only the paths declared in
`system_gap_master/yard_template/YARD_TEMPLATE.json`.

## Ownership model

The template manifest distinguishes four relevant ownership classes:

- `repo`: generic structure or documentation supplied by this repository;
- `instance`: locally curated yard content;
- `host` or `actor`: content written by exactly one host or access path;
- `tool`: a tool-owned lifecycle zone such as `db-transit/<namespace>`.

Files additionally use one of two modes:

- `managed`: a later template version may update the file only when its
  current hash still equals the hash recorded by the previous successful
  operation;
- `seed-once`: create the file when absent, then preserve the instance copy.

Any untracked or locally modified `managed` file blocks the plan. A matching
filename, newer timestamp or host suffix never establishes authority.
Managed files below protected or non-repository-owned directories require an
exact `managed_path_exceptions` entry in the template manifest; a broad glob
cannot silently turn host, message, archive or tool payloads into managed data.

## Read-only inspection

```bash
yard-instance-manager doctor \
  --yard-root /path/to/shared/SYNC

yard-instance-manager inventory \
  --yard-root /path/to/shared/SYNC

yard-instance-manager retention-plan \
  --yard-root /path/to/shared/SYNC \
  --legacy-host-slot laptop --legacy-host-slot workstation
```

`doctor` checks the declared structure, ownership collisions, instance-state
integrity, stale template state, link boundaries and the legacy `_transit`
marker. `inventory` classifies top-level entries without changing them.
`retention-plan` identifies old host artifacts, stale messages, old archives
and flat host slots, but every candidate has `safe_to_apply=false`: reader,
writer, references and acknowledgements must be proven separately.
Repeatable `--legacy-host-slot` values let an older instance expose pre-v1
root-level slots without hard-coding private host names into the public tool.

The legacy `_transit/<publisher>` path is never moved automatically. Its
reader and writer must be identified first, then structured payloads move to
the existing R9 `db-transit/<namespace>` adapter owned by
`sqlite-transit-sync`.

## Plan, upgrade and verify

```bash
yard-instance-manager plan \
  --yard-root /path/to/shared/SYNC \
  --output /host-local/review/yard-plan.json

# Review the JSON. Upgrade refuses a plan with blockers.
yard-instance-manager upgrade \
  --plan /host-local/review/yard-plan.json \
  --state-dir /host-local/system-gap-master-state

# A second plan must contain no create/update actions.
yard-instance-manager plan \
  --yard-root /path/to/shared/SYNC
```

The plan is SHA-256 bound to its content and to the template manifest. Apply
rechecks every source and target hash, rejects symlink, junction and reparse
boundaries, creates local backups before updates, writes an integrity-protected
host-local write-ahead operation journal before its first yard mutation, uses
atomic replacement and writes a
compact `.system-gap-instance.json` state file to the yard. Operational
manifests and backups stay in the host-local state directory outside both the
yard and the repository clone. The packaged template is the default;
`--template-root` selects an explicitly reviewed development template.

The shared state contains a random stable `instance_id`, template hashes and
relative managed paths, but no absolute local yard path. This keeps the state
portable between Windows, macOS and Linux. Its exact pre-plan hash is rebound
before apply. Mutating runs still require one externally coordinated rollout
owner for the yard; the file-sync provider is not a distributed lock service.

### Cross-host POSIX mode drift

`plan` reads the actual POSIX mode of every managed file (`os.stat`, skipped
on Windows, where mode is not portable). If a file-sync provider re-lands an
otherwise byte-identical managed file on a second POSIX host with a different
mode than the template declares (for example `0644` instead of a declared
`0755`), `plan` blocks with `untracked-or-modified-managed-mode` instead of
silently accepting it. This is intentional, not a bug: content-only
comparison cannot tell a benign sync artifact from a real permission
regression (an executable helper losing its exec bit is exactly this
shape), so the manager treats the two identically and fails closed.

To resolve the blocker, restore the declared mode on the affected host and
re-run `plan`:

```bash
chmod <declared-posix-mode> /path/to/shared/SYNC/<relative-path>
yard-instance-manager plan --yard-root /path/to/shared/SYNC
```

The declared mode for each managed file is in the template manifest
(`posix_mode`, e.g. `"0755"`); a blocked `doctor` run reports it directly as
`source_mode` next to the drifted `target_mode` in the blocker entry.
Do not resolve this by relaxing the check to a warning — mode drift is rare
enough that a manual `chmod` is cheap, and downgrading a real permission
change to a warning is exactly the kind of quiet erosion this fail-closed
check exists to prevent.

## Rollback

```bash
yard-instance-manager rollback \
  --operation-id <hex-operation-id> \
  --yard-root /path/to/shared/SYNC \
  --state-dir /host-local/system-gap-master-state
```

Rollback first verifies the complete operation manifest, every post-upgrade
or already-restored target, every backup and the current instance state. It
sets a journaled `rolling-back` state before mutation and advances the journal
after each record. If a process stops between a file restore and the journal
write, the same command resumes from the observed before/after hashes. Files
created by the operation are removed only when their hashes are unchanged;
updated files and POSIX modes are restored; directories are removed only when
the operation created them and they remain empty. An `applying` journal from a
crashed upgrade can be passed to the same rollback command.

## Partner handoff (read-only)

An external installer or partner system can ask for a hash-bound summary of a
yard without gaining any rights over it:

```bash
yard-instance-manager installer-handoff \
  --yard-root /path/to/shared/SYNC \
  --profile-path /review/system-profile.json --profile-sha256 <64-hex> \
  --observation-path /review/deployment-observation.json --observation-sha256 <64-hex> \
  --target-host <stable-host-id> --inventory-host <stable-host-id>
```

The command prints one JSON document with the schema
`system-gap.installer-export.v1`. It contains a `handoff`
(`ellmos.system-gap.handoff.v1`) and the `sync_evidence`
(`ellmos.installer.sync-evidence.v1`), each carrying a `content_hash`.

What it checks before it exports:

- Both input files are pinned by a full SHA-256, are JSON objects and are at
  most 64 KiB. A byte or pin mismatch aborts.
- The system profile must use the native `ellmos.system.v1` schema with the id
  `bach-agent-system`, and the deployment observation the schema
  `codex.bach.deployment-observation.v1`. Any other profile or observation
  schema needs an explicit adapter and is refused.
- The observation host must equal `--target-host`; both host ids may contain
  only `A-Za-z0-9._-`.
- The observation must carry a timezone, may not be from the future (5 minute
  tolerance) and may not be older than 24 hours, and must name an immutable
  40-hex source commit.
- The yard side is the same non-mutating inventory as `inventory`; only
  metadata and counts are included, never file content.

What it deliberately does not do: it performs no runtime probe, adoption,
approval or grant, does not verify the remote yard, and does not turn an
external observation into an audit result. The `limits` array of the handoff
repeats this, and `mutation_performed`, `approval_created` and
`grant_created` are always `false`. The exporter never writes into the yard.

## Boundaries

- The manager never runs `git` inside the yard.
- It never edits undeclared paths, host content, messages, archives or
  `db-transit` payloads.
- It does not provision a new operating system or provider rule files. That
  belongs to an installer or agent bridge; this module only aligns an existing
  multi-system yard.
- A digest detects accidental plan/state corruption; it is not a signature or
  an authorization token. Access control and release approval remain external
  gates.
