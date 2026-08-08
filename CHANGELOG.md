# Changelog

## 1.2.0 - 2026-07-27

- Renamed the public project from `sync-master` to `system-gap-master`.
- Renamed the daily gate to `scripts/system_gap_daily_check.py`.
- Added temporary compatibility for `scripts/sync_daily_check.py` and
  `SYNC_MASTER_DIR`.

All notable changes to system-gap-master are documented here.

## Unreleased

- **Conflict reconciler: `exempt_name_patterns` keeps by-design host-suffixed
  artefacts out of the scan entirely.** Ticket T-20260729-04 (SS4b) requires
  that files a yard maintains per host on purpose — a per-host status log, a
  per-host registry snapshot, a per-host scan manifest that itself carries a
  trailing host token — are never reported as conflict-copy candidates. The
  existing `known_hosts` suffix detector matches any `-HOST` filename
  regardless of intent, so without an exclusion these files reach the scan
  queue as `canonical-authority-missing` noise. A root may now declare
  `exempt_name_patterns` (regexes matched against the root-relative POSIX
  path); matches are skipped before detection runs, not merely left unmapped,
  and are reported back under `scan()["exempted_by_policy"]` so a fail-open
  regex mistake stays auditable. Directories literally named `_archive`
  (case-insensitive) are skipped unconditionally, independent of
  configuration. Five regression tests, including negative cases for each
  SS4b category and a check that a genuine host-suffixed conflict copy is
  still detected alongside the exemptions. No production root config exists
  yet for any real yard, so this is a capability, not an active exclusion.
- Maintainer verification on 2026-08-04: 83 tests passed and 1 Windows
  symlink-platform test was skipped due to missing account privilege; Ruff and
  both public CLI help surfaces passed.
- Maintainer verification on 2026-08-01: 83 tests passed and 1 Windows
  symlink-platform test was skipped due to missing account privilege; Ruff and
  the daily-gate, trusted-peer and conflict-reconciler CLI help surfaces passed.
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
