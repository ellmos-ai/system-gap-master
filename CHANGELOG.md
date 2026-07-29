# Changelog

## 1.2.0 - 2026-07-27

- Renamed the public project from `sync-master` to `system-gap-master`.
- Renamed the daily gate to `scripts/system_gap_daily_check.py`.
- Added temporary compatibility for `scripts/sync_daily_check.py` and
  `SYNC_MASTER_DIR`.

All notable changes to system-gap-master are documented here.

## Unreleased

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
