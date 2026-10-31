#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep a yard's snapshots of local agent rule files current.

Agent rule files (``AGENTS.md``, ``CLAUDE.md``, ``GEMINI.md`` ...) usually live
in local, *unsynced* paths. Other machines only see them if somebody copies
them into the yard by hand, and a hand-copied snapshot silently rots: the file
is still there, it is just old. This script measures source against snapshot,
reports drift and refreshes the snapshots.

Commands::

    check    measure source vs. snapshot and write the state file
    mirror   refresh drifted or missing snapshots (``--dry-run`` shows the plan)
    report   render ``agents/RULEFILES.md`` from all state files
    all      mirror + report

It is deliberately one-way (local -> yard). Rule files often end with a live
message board; copying a foreign snapshot back would delete open messages.
Merge foreign snapshots by hand.

Division of labour: *who owns which file* is a decision and lives in a
hand-maintained registry, ``<yard>/agents/registry/<slot>.json`` (see
``examples/rulefile-registry.example.json``). Hash, age and drift are measured
and live in ``<slot>.state.json`` next to it. Zero dependencies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

REGISTRY_SCHEMA = "system-gap.rulefiles.v1"
STATE_SCHEMA = "system-gap.rulefiles-state.v1"
MARKER = "<!-- MIRROR-BODY BELOW -->"
ENV_YARD = "SYSTEM_GAP_MASTER_DIR"
ENV_SLOT = "SYSTEM_GAP_SLOT"
SLOT_RE = re.compile(r"[A-Za-z0-9._-]+")

# Real secret *formats* only, never bare words: "api_key" appears in nearly every
# rule file and would only produce false alarms that get ignored.
SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "API-key-like token"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "npm token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
]

LABELS = {
    "current": "ok", "drift": "DRIFT", "mirror-missing": "mirror missing",
    "source-missing": "source missing", "no-mirror-wanted": "none wanted",
    "no-source": "no source file",
}


class RegistryError(Exception):
    """The registry or one of its entries is unusable."""


def resolve_source(raw: str | None) -> Path | None:
    if not raw:
        return None
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def mirror_path(yard: Path, rel: str) -> Path:
    """Snapshot paths must stay inside ``<yard>/agents``; anything else is refused."""
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if (not parts or parts[0] != "agents" or len(parts) < 2
            or any(p in ("..", "") or p.endswith(":") for p in parts) or os.path.isabs(rel)):
        raise RegistryError(f"mirror path must be a relative path under agents/: {rel!r}")
    target = (yard.joinpath(*parts)).resolve()
    if (yard / "agents").resolve() not in target.parents:
        raise RegistryError(f"mirror path escapes agents/: {rel!r}")
    return target


def file_info(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"exists": False}
    raw = path.read_bytes()
    return {
        "exists": True,
        "bytes": len(raw),
        "sha256_12": hashlib.sha256(raw).hexdigest()[:12],
        "mtime": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
    }


def secret_hits(path: Path) -> list[dict[str, Any]]:
    """Line numbers of suspicious spots - never the value itself."""
    hits: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for pattern, name in SECRET_PATTERNS:
                if pattern.search(line):
                    hits.append({"line": number, "kind": name})
    except OSError:
        pass
    return hits


def lines_of(path: Path | None) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines() if path else []
    except OSError:
        return []


def atomic_write(path: Path, data: bytes) -> None:
    """Encode first, write a sibling temp file, then replace - never a half-written snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_registry(yard: Path, slot: str) -> tuple[dict[str, Any], Path]:
    if not SLOT_RE.fullmatch(slot):
        raise RegistryError(f"slot may only contain A-Za-z0-9._-: {slot!r}")
    path = yard / "agents" / "registry" / f"{slot}.json"
    if not path.is_file():
        raise RegistryError(f"no registry for slot {slot!r}: {path}")
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(f"registry schema must be {REGISTRY_SCHEMA!r}")
    entries = registry.get("files")
    if not isinstance(entries, list) or not entries:
        raise RegistryError("registry needs a non-empty 'files' list")
    ids = [e.get("id") for e in entries if isinstance(e, dict)]
    if len(ids) != len(entries) or not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
        raise RegistryError("every file entry needs a unique string 'id'")
    return registry, path


def check_central(registry: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """Exactly one entry may have role=central."""
    central = [e["id"] for e in registry["files"] if e.get("role") == "central"]
    if len(central) == 1:
        return True, central[0], None
    if not central:
        return False, None, "no central rule file registered (role=central is missing)"
    return False, None, "several central rule files: " + ", ".join(central) + " - exactly one is allowed"


def measure(yard: Path, registry: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for entry in registry["files"]:
        source = resolve_source(entry.get("source"))
        mirror = mirror_path(yard, entry["mirror"]) if entry.get("mirror") else None
        src, mir = file_info(source), file_info(mirror)
        if not entry.get("source"):
            state = "no-source"
        elif not src["exists"]:
            state = "source-missing"
        elif mirror is None:
            state = "no-mirror-wanted"
        elif not mir["exists"]:
            state = "mirror-missing"
        elif entry.get("mode") == "annotated":
            assert source is not None  # src["exists"] implies a source path
            state = "current" if annotated_body_equal(mirror, source) else "drift"
        else:
            state = "current" if src["sha256_12"] == mir["sha256_12"] else "drift"
        results.append({
            "id": entry["id"], "actor": entry.get("actor"), "role": entry.get("role"),
            "source": entry.get("source"), "mirror": entry.get("mirror"),
            "mode": entry.get("mode", "copy"), "state": state, "src": src, "mir": mir,
            "has_message_board": bool(entry.get("has_message_board")),
            "_source": source, "_mirror": mirror,
        })
    return results


def annotated_body_equal(mirror: Path, source: Path) -> bool:
    """For hand-headed snapshots only the body below the marker is compared."""
    lines = lines_of(mirror)
    if MARKER not in lines:
        return False
    body = "\n".join(lines[lines.index(MARKER) + 1:]).strip()
    return body == source.read_text(encoding="utf-8", errors="replace").strip()


def lines_only_in_mirror(item: dict[str, Any]) -> int:
    """How many lines would an overwrite destroy?

    Lines that exist only in the snapshot are usually answered messages the
    owner deleted locally (overwriting is right) - or a message another machine
    wrote into the snapshot that never arrived here. The two cases cannot be
    told apart mechanically, so the lines are counted, reported and backed up.
    """
    if not item["mir"].get("exists"):
        return 0
    return len({z for z in set(lines_of(item["_mirror"])) - set(lines_of(item["_source"])) if z.strip()})


def backup(yard: Path, mirror: Path) -> str:
    folder = yard / "agents" / "_previous"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{mirror.stem}.{dt.date.today().isoformat()}{mirror.suffix}"
    shutil.copy2(mirror, target)  # one backup per file and day
    return target.relative_to(yard).as_posix()


def write_annotated(item: dict[str, Any]) -> tuple[bool, str]:
    """Keep the hand-written head, replace only the body. No marker -> no write."""
    old = lines_of(item["_mirror"]) if item["mir"].get("exists") else []
    if MARKER not in old:
        return False, f"head marker missing - add '{MARKER}' where the mirrored body should start"
    head = old[: old.index(MARKER) + 1]
    body = item["_source"].read_text(encoding="utf-8", errors="replace")
    atomic_write(item["_mirror"], ("\n".join(head) + "\n" + body).encode("utf-8"))
    return True, f"body replaced, head ({len(head)} lines) kept"


def mirror_all(yard: Path, items: list[dict[str, Any]], dry_run: bool) -> tuple[list, list]:
    done, skipped = [], []
    for item in items:
        if item["state"] not in ("drift", "mirror-missing"):
            continue
        hits = secret_hits(item["_source"])
        if hits:
            kinds = ", ".join(sorted({h["kind"] for h in hits}))
            where = ", ".join(str(h["line"]) for h in hits)
            skipped.append((item["id"], f"suspected secret on line(s) {where} ({kinds}) - NOT mirrored"))
            continue
        loss = lines_only_in_mirror(item)
        if dry_run:
            note = f", {loss} line(s) only in snapshot -> backup" if loss else ""
            done.append((item["id"], f"would mirror ({item['state']}){note}"))
            continue
        saved = backup(yard, item["_mirror"]) if loss else None
        if item["mode"] == "annotated":
            ok, note = write_annotated(item)
            (done if ok else skipped).append((item["id"], note))
            continue
        atomic_write(item["_mirror"], item["_source"].read_bytes())
        suffix = f"; {loss} line(s) only in snapshot saved to {saved}" if loss else ""
        done.append((item["id"], f"mirrored ({item['state']}){suffix}"))
    return done, skipped


def write_state(yard: Path, slot: str, registry: dict[str, Any], items: list[dict[str, Any]],
                central: tuple[bool, str | None, str | None]) -> Path:
    data = {
        "schema": STATE_SCHEMA, "slot": slot, "host": registry.get("host"),
        "measured": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "central_ok": central[0], "central": central[1], "central_error": central[2],
        "files": [{k: v for k, v in i.items() if not k.startswith("_")} for i in items],
    }
    target = yard / "agents" / "registry" / f"{slot}.state.json"
    atomic_write(target, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return target


def write_report(yard: Path) -> Path:
    folder = yard / "agents" / "registry"
    states = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.state.json"))] \
        if folder.is_dir() else []
    out = ["# Rule files - measured state per system", "",
           "**Generated by `scripts/rulefile_mirror.py report` - do not edit by hand.**",
           "Which file belongs to which actor is hand-maintained in `registry/<slot>.json`.", ""]
    if not states:
        out.append("*No system recorded yet. Run `python scripts/rulefile_mirror.py check`.*")
    for st in states:
        out += [f"## {st['slot']} ({st.get('host') or '?'}) - measured {st.get('measured')}", ""]
        out.append(f"Central rule file: **{st.get('central')}**" if st.get("central_ok")
                   else f"WARNING - central rule file unclear: {st.get('central_error')}")
        out += ["", "| File | Actor | Role | State | Source | Snapshot |", "|---|---|---|---|---|---|"]
        for e in st["files"]:
            def cell(info: dict[str, Any]) -> str:
                return f"{info['bytes']} B, {info['mtime']}" if info.get("exists") else "-"
            out.append(f"| `{e['id']}` | {e.get('actor')} | {e.get('role')} | "
                       f"{LABELS.get(e['state'], e['state'])} | {cell(e['src'])} | {cell(e['mir'])} |")
        out.append("")
    out += ["## Why there is no way back", "",
            "Snapshots only flow local -> yard. Rule files often end with a live message board;",
            "copying a foreign snapshot back would delete open messages. Foreign states are",
            "merged by hand, not overwritten - see `agents/README.md`.", ""]
    target = yard / "agents" / "RULEFILES.md"
    atomic_write(target, "\n".join(out).encode("utf-8"))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep snapshots of local agent rule files current.")
    parser.add_argument("command", choices=["check", "mirror", "report", "all"], nargs="?", default="check")
    parser.add_argument("--yard-root", default=os.environ.get(ENV_YARD), help=f"yard directory (or ${ENV_YARD})")
    parser.add_argument("--slot", default=os.environ.get(ENV_SLOT), help=f"this machine's slot (or ${ENV_SLOT})")
    parser.add_argument("--dry-run", action="store_true", help="with mirror: only show the plan")
    parser.add_argument("--fail-on-drift", action="store_true", help="exit 2 if any snapshot is not current")
    args = parser.parse_args(argv)
    if not args.yard_root:
        parser.error(f"--yard-root or ${ENV_YARD} is required")
    yard = Path(args.yard_root).expanduser().resolve()

    try:
        if args.command == "report":
            print(f"Report: {write_report(yard)}")
            return 0
        if not args.slot:
            parser.error(f"--slot or ${ENV_SLOT} is required")
        registry, registry_path = load_registry(yard, args.slot)
        central = check_central(registry)
        items = measure(yard, registry)
        print(f"Slot {args.slot} ({registry.get('host') or '?'}) - registry: {registry_path}")
        print("Central rule file: " + (str(central[1]) if central[0] else "PROBLEM - " + (central[2] or "")))
        for i in items:
            print(f"  {i['id']:<16} {i.get('actor')!s:<14} {i['state']:<18} {i.get('mirror') or '(no snapshot)'}")
        if args.command in ("mirror", "all"):
            done, skipped = mirror_all(yard, items, args.dry_run)
            for name, note in done:
                print(f"  + {name:<16} {note}")
            for name, note in skipped:
                print(f"  ! {name:<16} {note}")
            if not done and not skipped:
                print("  (nothing to do - all snapshots current)")
            if not args.dry_run:
                items = measure(yard, registry)
        print(f"\nState: {write_state(yard, args.slot, registry, items, central)}")
        if args.command == "all":
            print(f"Report: {write_report(yard)}")
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not central[0]:
        return 1
    if args.fail_on_drift and any(i["state"] in ("drift", "mirror-missing", "source-missing") for i in items):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
