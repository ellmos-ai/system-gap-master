# Changelog

## 1.2.0 - 2026-07-27

- Renamed the public project from `sync-master` to `system-gap-master`.
- Renamed the daily gate to `scripts/system_gap_daily_check.py`.
- Added temporary compatibility for `scripts/sync_daily_check.py` and
  `SYNC_MASTER_DIR`.

All notable changes to system-gap-master are documented here.

## Unreleased

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
