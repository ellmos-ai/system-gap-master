# Changelog

## 1.2.0 - 2026-07-27

- Renamed the public project from `sync-master` to `system-gap-master`.
- Renamed the daily gate to `scripts/system_gap_daily_check.py`.
- Added temporary compatibility for `scripts/sync_daily_check.py` and
  `SYNC_MASTER_DIR`.

All notable changes to system-gap-master are documented here.

## Unreleased

- Documented Republica (the sqlite-transit-sync showcase fallback) as a
  permanent second operating mode alongside direct tunnel sync, not a
  stopgap: a bilingual README section with a failure-scenario table, and
  `system_gap_master/republica_transit.py` — a dependency-free helper that
  resolves the R9 tool-owned `db-transit/<namespace>` transit zone for a
  namespace and validates that a `republica_root` import destination stays
  outside the yard. Registered as the `republica-transit` console script;
  never imports `sqlite_transit_sync`, so it works with or without the
  companion package installed.
- Synchronized the maintained German README with the canonical English
  structure, protocol rules, companion-tool, stack-family, security and
  provenance sections while preserving code blocks byte-for-byte.
- Replaced live trusted-peer publishing and `pull --apply` with a read-only
  V4 preparation boundary. The stable CLI now validates, lists, resolves and
  emits deterministic non-executable pull receipts without network contact,
  referenced-file reads or yard writes.
- Added registry/config/receipt v2 schemas and fail-closed gates for owner
  slot, schema/version, host/peer, freshness/expiry, pinned signature
  reference, payload digest, known-host pins, exact remote paths, destination
  safety and secret/content detection.
- Documented the remaining detached-signature, SSH ACL/authentication,
  network-route, anti-replay and reviewed-executor gates for real two-host
  activation.
- Reject UNC/device namespaces and non-portable Windows aliases before any
  filesystem probe, bind registry reads to one checked file identity, and use
  provider-neutral `direct`/`private-overlay` route labels.

### Added — conflict reconciler: keep the review queue worth reading

- **Machine-regenerable artefacts are no longer surfaced.** `__pycache__`,
  `.pytest_cache`, `.mypy_cache`, `.ruff_cache` and bytecode extensions
  (`.pyc`, `.pyo`, `.pyd`, `.class`, `.o`, `.obj`) are skipped during
  candidate iteration. A conflict copy of a bytecode cache carries no
  information — the file is rebuilt on the next run, so neither merging nor
  human review is worth anyone's time. Observed on a real repository: thirteen
  of thirteen "undecidable" candidates were bytecode and VCS internals. A queue
  like that trains reviewers to ignore it, which is worse than no queue at all.
  (`.git` was already excluded.)
- **`host_specific_markers()`** reports evidence that a file legitimately
  differs per host (absolute user paths, known host names in content). Such a
  pair is not a conflict to merge: either both sides are kept under explicit
  per-host names, or — better — the file is made path-neutral so the split
  disappears. Merging them silently destroys one host's configuration.
- **`excerpt()`** returns beginning, middle and end of a text. Before comparing
  two versions line by line, a reviewer needs the cheaper answer first: is
  merging this worth doing at all? That matters when one run surfaces dozens of
  candidates.
- Six regression tests covering all three additions, including an end-to-end
  check that a bytecode conflict copy never reaches the queue while a README
  in the same run still does.

## 1.4.0 - 2026-07-29

- Added the user- and host-neutral `trusted-peer-paths` API/CLI with atomic
  own-slot publish, HMAC-authenticated validation, peer-filtered list/resolve,
  replay pins and machine-readable schemas/examples.
- Added shell-free SFTP pull plans and explicit `pull --apply` for ordinary
  files with strict known-host checking, destination allowlists, local
  staging and no-overwrite installation.
- Added the R9 database boundary: SQLite/`-wal`/`-shm` paths may be published
  only as discovery metadata with `direct_pull=false` and
  `adapter=sqlite-transit-sync`; direct pull remains blocked.
- Hardened Windows 8.3/reparse handling and physical overlap comparisons,
  canonical IDs, strict JSON/revision state, OpenSSH option paths, immutable
  pull plans, bounded null-output SFTP staging, owner-only modes and atomic
  no-overwrite result/file installation.

## 1.3.2 - 2026-07-29

- Renew leases through a unique no-overwrite temporary file followed by
  flush, fsync, final guard/token/fingerprint binding and atomic replacement;
  a failed or interrupted temporary write cannot corrupt the active lease.
- Added fail-safe malformed-lease recovery: only an adapter with explicit
  expired-takeover authority may quarantine a stable malformed lease whose
  file age exceeds the configured lease TTL. Recent or unstable damage stays
  busy for review.

## 1.3.1 - 2026-07-29

- Serialized lease creation, expired takeover, renewal and release with a
  crash-released host-local OS lock; renewal now writes only through the
  token- and inode-bound lease descriptor.
- Bound every signed operation manifest to the operation ID requested by the
  caller, preventing valid-manifest substitution under another filename.
- Rebound rollback inputs immediately before each mutation, restored missing
  conflict copies without overwrite, and retained recoverable archives as
  immutable rollback evidence.

## 1.3.0 - 2026-07-29

- Added the user-neutral `conflict-copy-reconciler` API/CLI with explicit
  root allowlists, authoritative canonical mappings, scan/plan/apply/verify/
  rollback, per-root leases, compare-before-swap, local backups, recoverable
  archives and path-redacted receipts.
- Added deterministic automatic classes for exact copies, append-only UTF-8
  supersets, non-overlapping three-way text merges and conflict-free JSON
  object merges. Semantic conflicts, secrets, binaries, databases, archives,
  `.git`, dirty work, locks and unready cloud placeholders fail closed.
- Added provider-neutral desktop-automation and macOS LaunchAgent/runner
  templates. Provider registrations remain instance-owned; every path scope
  has one mutating owner and any number of observers.
- Added Windows/macOS detector, race, rollback, idempotency and real temporary
  canary coverage.
- Hardened the mutation boundary with observer/owner enforcement, signed
  plan/manifest readback, stable file-descriptor fingerprints, no-symlink/
  junction/reparse traversal, no-overwrite archives, token-safe lease renewal
  and preflighted non-destructive rollback.
- Set the package minimum to Python 3.10, matching the typed public API and
  the existing CI matrix.

## 2026-07-26

- Performed technical hygiene & maintenance check (Path A).
- Added comprehensive German documentation (`README_de.md`) covering system architecture, 8-rule protocol summary, quick start guide, and ecosystem integration.
- Enhanced `README.md` with language switcher toggle (`[English](README.md) | [Deutsch](README_de.md)`).
- Updated `llms.txt` `Last-checked` timestamp to 2026-07-26 and added `README_de.md` index reference.
- Added `pythonpath = ["."]` to `[tool.pytest.ini_options]` in `pyproject.toml` for standard module test discovery.
- Linked `dev-bricks/sqlite-transit-sync` companion repository directly in `README.md`.
- Verified unit test suite execution (5/5 passed, 100% green).

## 2026-07-25

- Added PEP 621 compliant `pyproject.toml` package metadata and Pytest configuration.
- Enhanced `README.md` with Shields.io badges (Python, MIT License, Protocol, Indexing, Tests), AI/LLM callout note, and Mermaid architecture flowchart.
- Updated `llms.txt` `Last-checked` timestamp to 2026-07-25 and expanded search phrase anchors.

## 2026-07-21

- Added unit test suite `tests/test_sync_daily_check.py` covering `sync_daily_check.py` gate script and CLI.
- Added GitHub Actions workflow (`.github/workflows/tests.yml`) running unit tests on Python 3.10, 3.11, and 3.12 across Ubuntu and Windows.
- Synchronized `llms.txt` `Last-checked` date to 2026-07-21 and updated `TODO.md` status.
- Added release-gate hygiene docs: `TODO.md` now records the current gate status, and `.gitignore` covers the .MODULES minimum local-secret, database, virtualenv and editor patterns.
- Added public module metadata for the dev-bricks ecosystem.
- Added `llms.txt` so agents and search tools can identify the protocol,
  safety boundaries and canonical repository quickly.

## 2026-07-11

- Documented structured payload handling for database transit zones.
- Added `sqlite-transit-sync` companion notes to the protocol and README.
