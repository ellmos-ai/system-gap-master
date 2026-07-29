# Conflict-copy reconciler

`conflict-copy-reconciler` is the single user-neutral mutation engine for
sync-provider conflict copies. Desktop-agent automations and host schedulers
are adapters: they may discover, plan, observe and request a takeover, but
they do not reimplement merge logic.

## Safety boundary

The engine mutates only when every gate is green:

1. The target is below an explicitly allowlisted root.
2. A canonical mapping names both files and cites one authority:
   `manifest`, `pointer`, `registry`, or `writer-policy`.
3. The root has a positive cloud-readiness attestation and neither file is an
   offline/recall placeholder.
4. No `LOCK*.txt`, active Git operation, dirty Git path, unstable writer,
   database, archive, binary, secret path or secret-looking content is
   involved.
5. The scan fingerprint still matches at apply time.
6. Exactly one adapter owns the root lease.
7. One deterministic merge class produces a parseable result.
8. The canonical file and the recoverable archive pass hash readback.
9. Every path component is regular: symlinks, Windows reparse points,
   junctions, alternate data streams and reserved device names fail closed.
10. The plan and local operation manifest have a host-secret HMAC and match
    the current actor, mode and complete configuration digest.

All other candidates stay in place with a blocker code. An LLM may prepare a
review synopsis, but its prose is never sufficient mutation authority.

## Automatic classes

| Class | Proof |
|---|---|
| `exact` | Both SHA-256 hashes match. Canonical content is not rewritten. |
| `append-only-text` | UTF-8 conflict copy begins byte-for-byte with canonical content and adds only a line-aligned suffix. |
| `three-way-text` | Policy provides a common base path and SHA-256; edits from both sides are non-overlapping. |
| `json-object` | Explicit adapter; recursive object keys are identical or disjoint. Differing scalar/list values fail closed. |

YAML, TOML on runtimes without `tomllib`, arbitrary code, opaque binary
formats and databases have no generic merge adapter. Add a domain adapter
with fixtures and consumer validation instead of weakening the generic gate.

## Configuration

Use [`examples/conflict-reconciler.config.example.json`](../examples/conflict-reconciler.config.example.json).
The state directory must be local and outside the synced root. It contains
leases, operational manifests, backups and path-redacted receipts.

`cloud_ready` is an instance attestation, not a discovery default. Set it only
after the host adapter has confirmed that the configured root is available.
Windows recall/offline attributes and macOS `.icloud` placeholders are still
blocked independently.

Mappings are deliberately explicit. A filename detector can find additional
candidates, but without a mapping they are reported as
`canonical-authority-missing`.

`max_files` and `max_file_bytes` bound each run. Oversized candidates are
hashed for identification but never loaded into the merge engine.

`mode` defaults to `observer`. Observer configs may scan and produce a signed
plan, but `apply`, `reconcile` and `rollback` reject them. Exactly one config
per host/root may use `mutating-owner`. All adapters on that host must share
the same host-local config and state directory; otherwise they cannot share
the path-derived lease.

`receipt_salt` is required, persistent and at least 16 characters. Generate a
high-entropy value during host setup, keep it outside the synced yard, and
protect the config with host-local permissions. It signs plans and operation
manifests as well as salting receipt path identifiers.

## CLI and API

```bash
conflict-copy-reconciler scan --config ./conflict-reconciler.config.json
conflict-copy-reconciler plan --config ./conflict-reconciler.config.json \
  --output ./plan.json
conflict-copy-reconciler apply --config ./conflict-reconciler.config.json \
  --plan ./plan.json
conflict-copy-reconciler reconcile --config ./conflict-reconciler.config.json
conflict-copy-reconciler verify --config ./conflict-reconciler.config.json \
  --operation-id <OPERATION_ID>
conflict-copy-reconciler rollback --config ./conflict-reconciler.config.json \
  --operation-id <OPERATION_ID>
conflict-copy-reconciler canary
```

The same flow is available through `ConflictCopyReconciler.scan()`,
`.plan()`, `.apply()`, `.verify()` and `.rollback()`.

`reconcile` plans and applies only `ready` items. Blocked items remain
untouched. The output receipt contains no absolute paths, file content or
plain relative filenames; it uses salted path hashes. The local operation
manifest retains root-relative paths because rollback needs them. Never copy
that private local manifest into a shared yard.

## Desktop-agent automation contract

Use the task template in
`template/runners/desktop-agent/conflict-copy-reconciler.task.json`.

- Title: `<APP_DISPLAY_NAME> — Conflict Copy Reconciler`.
- Every installed desktop-agent app may have an observer task.
- Exactly one adapter per host/root scope has `mutating-owner` mode.
- A second app may take over only after lease expiry and an instance policy
  that explicitly enables expired-lease takeover.
- Native provider automation APIs/UIs are required. If a provider has no
  supported headless update path, leave a host-local setup order instead of
  editing its private registry.
- A scheduler registration is not a successful run. Preserve a receipt for
  both registration readback and a real canary/run.
- Observer tasks run `plan`; they never call `reconcile`. The task template
  contains separate commands for both modes.

## macOS runner

`template/runners/macos/` contains a user-neutral shell runner and LaunchAgent
template. Replace placeholders during host setup; keep the actual root,
state directory, schedule and log locations in host-local configuration.
The runner has no embedded username or volume and calls the same engine used
on Windows. It defaults to observer mode; the host-local LaunchAgent may be
switched to `mutating-owner` only after the gates below and after all other
adapters for that root are confirmed observers.

Install only after:

1. a temporary `canary` is green on that Mac;
2. the config has an explicit root and one owner;
3. Full Disk Access is granted where the chosen root requires it;
4. `plutil -lint` and `launchctl print` readback are green;
5. an observer run produces a receipt before automatic apply is enabled.
