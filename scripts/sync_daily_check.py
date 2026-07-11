#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync-master daily gate — check/mark the once-per-day-per-host sync ritual.

Usage:
    python sync_daily_check.py check [--dir <SYNC_DIR>] [--host <NAME>]
    python sync_daily_check.py mark  [--dir <SYNC_DIR>] [--host <NAME>] [--note "..."]

Resolution order for the yard directory: --dir, then the SYNC_MASTER_DIR
environment variable. The host defaults to this machine's hostname
(uppercased). Exit codes for `check`: 0 = already synced today (or gate file
missing entirely — nothing to nag about), 1 = sync still due today.

Wire `check` into your agent's session-start hook so the agent gets a gentle
reminder at most once a day (see docs/adapting-your-agents.md).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import socket
import sys
from pathlib import Path

LOG_NAME = "DAILY_SYNC_LOG.md"
HEADER = (
    "# Daily sync log — one row per host per day\n\n"
    "> Gate for the sync-master daily ritual (see SKILL.md). Appended by\n"
    "> scripts/sync_daily_check.py mark. Do not edit rows by hand unless fixing mistakes.\n\n"
    "| date | host | note |\n|---|---|---|\n"
)


def resolve_dir(cli_dir: str | None) -> Path | None:
    raw = cli_dir or os.environ.get("SYNC_MASTER_DIR")
    if not raw:
        return None
    return Path(raw).expanduser()


def default_host() -> str:
    return socket.gethostname().split(".")[0].upper()


def today() -> str:
    return _dt.date.today().isoformat()


def row_exists(log: Path, date: str, host: str) -> bool:
    if not log.exists():
        return False
    needle_host = host.strip().lower()
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) >= 2 and parts[0] == date and parts[1].lower() == needle_host:
            return True
    return False


def cmd_check(sync_dir: Path, host: str) -> int:
    log = sync_dir / LOG_NAME
    if not log.exists():
        print(f"[sync-master] no {LOG_NAME} in {sync_dir} — gate inactive.")
        return 0
    if row_exists(log, today(), host):
        print(f"[sync-master] {host}: today's sync is done.")
        return 0
    print(
        f"[sync-master] {host}: no sync yet today — consider running the "
        f"daily sync ritual (SKILL.md), then mark the gate."
    )
    return 1


def cmd_mark(sync_dir: Path, host: str, note: str) -> int:
    sync_dir.mkdir(parents=True, exist_ok=True)
    log = sync_dir / LOG_NAME
    if row_exists(log, today(), host):
        print(f"[sync-master] {host}: already marked for today.")
        return 0
    if not log.exists():
        log.write_text(HEADER, encoding="utf-8")
    safe_note = (note or "").replace("|", "/").strip()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"| {today()} | {host} | {safe_note} |\n")
    print(f"[sync-master] {host}: marked {today()}.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="sync-master daily gate")
    ap.add_argument("command", choices=["check", "mark"])
    ap.add_argument("--dir", dest="dir", default=None, help="yard directory (or set SYNC_MASTER_DIR)")
    ap.add_argument("--host", default=None, help="host name (default: this machine)")
    ap.add_argument("--note", default="", help="optional note for mark")
    args = ap.parse_args(argv)

    sync_dir = resolve_dir(args.dir)
    if sync_dir is None:
        print("[sync-master] ERROR: no yard directory — pass --dir or set SYNC_MASTER_DIR.", file=sys.stderr)
        return 2
    if not sync_dir.exists() and args.command == "check":
        print(f"[sync-master] yard {sync_dir} does not exist — gate inactive.")
        return 0

    host = (args.host or default_host()).strip()
    if args.command == "check":
        return cmd_check(sync_dir, host)
    return cmd_mark(sync_dir, host, args.note)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
