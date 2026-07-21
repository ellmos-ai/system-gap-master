# Changelog

All notable changes to sync-master are documented here.

## Unreleased

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
