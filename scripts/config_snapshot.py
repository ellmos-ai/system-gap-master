#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create allowlisted, comparable configuration snapshots.

The provider table is deliberately data-driven.  The public repository does
not know where a user's agent configuration lives; each yard supplies a small
JSON table with paths and keys that are safe to compare.  Values outside that
allowlist are never read into the snapshot, and home-directory paths are
normalised to ``<HOME>`` so Windows and macOS snapshots can be compared.

Usage::

    python scripts/config_snapshot.py snapshot --state-dir /path/to/_config-state \
        --config /path/to/providers.json
    python scripts/config_snapshot.py report --state-dir /path/to/_config-state
    python scripts/config_snapshot.py all --state-dir /path/to/_config-state \
        --config /path/to/providers.json

Add ``--check`` to any mode for a read-only preview.  The generated report
marks unexplained differences with ``!`` and differences documented by a
``### `provider.key``` heading in ``DEVIATIONS.md`` with ``~``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Mapping

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - supported Python starts at 3.10.
    tomllib = None


SCHEMA = "system-gap-master/config-snapshot.v2"
PROVIDER_CONFIG_SCHEMA = "system-gap-master/config-state-providers.v1"
STATE_ENV = "SYSTEM_GAP_CONFIG_STATE_DIR"
CONFIG_ENV = "SYSTEM_GAP_CONFIG_STATE_CONFIG"
SLOT_ENV = "SYSTEM_GAP_CONFIG_STATE_SLOT"
DEFAULT_STATE_DIR_NAME = "_config-state"
DEFAULT_PROVIDER_CONFIG_NAME = "providers.json"

SECRET_RE = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API[-_]?KEY|[-_]KEY$|^KEY$|AUTH(?!OR)|[-_]PAT$|^PAT$)",
    re.IGNORECASE,
)
SLOT_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MISSING = object()


class ConfigSnapshotError(ValueError):
    """Raised for an invalid provider table or unsafe value."""


def _as_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def resolve_home(home: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the comparison home without requiring it to exist."""

    return _as_path(home or os.path.expanduser("~"))


def norm(value: Any, home: str | os.PathLike[str] | None = None) -> Any:
    """Normalise separators and the active home directory in scalar values."""

    if not isinstance(value, str):
        return value
    result = value.replace("\\", "/")
    home_text = resolve_home(home).as_posix().rstrip("/")
    folded = result.casefold()
    home_folded = home_text.casefold()
    start = folded.find(home_folded)
    if start >= 0:
        end = start + len(home_text)
        boundary = (
            (start == 0 or result[start - 1] == "/")
            and (end == len(result) or result[end] == "/")
        )
        if boundary:
            result = result[:start] + "<HOME>" + result[end:]
    return result


def expand_path(value: str, home: str | os.PathLike[str] | None = None) -> Path:
    """Expand only path placeholders supported by the provider table."""

    home_path = resolve_home(home)
    raw = value.strip()
    raw = raw.replace("<HOME>", str(home_path))
    raw = raw.replace("${HOME}", str(home_path))
    if raw == "~":
        raw = str(home_path)
    elif raw.startswith("~/") or raw.startswith("~\\"):
        raw = str(home_path / raw[2:])
    return _as_path(raw)


def safe_value(key: str, value: Any, home: str | os.PathLike[str] | None = None) -> Any:
    """Keep a scalar value or a shape-only marker, never secret content."""

    if SECRET_RE.search(str(key)):
        return "<redacted>"
    if isinstance(value, dict):
        return f"<dict:{len(value)}>"
    if isinstance(value, list):
        return f"<list:{len(value)}>"
    if isinstance(value, tuple):
        return f"<tuple:{len(value)}>"
    return norm(value, home)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - malformed local config is reportable data.
        return {"_parse_error": f"{type(exc).__name__}: invalid JSON"}


def _read_toml(path: Path) -> Any:
    if tomllib is None:
        return {"_parse_error": "tomllib unavailable"}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except Exception as exc:  # noqa: BLE001 - malformed local config is reportable data.
        return {"_parse_error": f"{type(exc).__name__}: invalid TOML"}


def read_config(path: Path, file_format: str | None = None) -> Any:
    """Read one configured JSON/TOML file, returning a redacted parse marker."""

    suffix = (file_format or path.suffix.lstrip(".") or "json").lower()
    if suffix in {"json", "jsonc"}:
        return _read_json(path)
    if suffix in {"toml", "tml"}:
        return _read_toml(path)
    return {"_parse_error": f"unsupported format: {suffix}"}


def _lookup(source: Any, dotted_path: str) -> Any:
    """Look up an exact key first, then a dotted nested path."""

    if not isinstance(source, Mapping):
        return _MISSING
    if dotted_path in source:
        return source[dotted_path]
    current: Any = source
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _allowlist_entries(spec: Any) -> list[tuple[str, str, str]]:
    """Normalise list/mapping allowlists to ``(output, source, mode)``."""

    if spec is None:
        return []
    entries: list[tuple[str, str, str]] = []
    if isinstance(spec, Mapping):
        iterable = spec.items()
        for output, source in iterable:
            if isinstance(source, str):
                entries.append((str(output), source, "value"))
            elif isinstance(source, Mapping):
                path = source.get("path", output)
                mode = source.get("mode", "value")
                entries.append((str(output), str(path), str(mode)))
        return entries
    if isinstance(spec, list):
        for item in spec:
            if isinstance(item, str):
                entries.append((item, item, "value"))
            elif isinstance(item, Mapping) and isinstance(item.get("path"), str):
                output = str(item.get("name", item["path"]))
                entries.append((output, item["path"], str(item.get("mode", "value"))))
    return entries


def _extract(source: Any, source_path: str, mode: str, home: Path) -> Any:
    value = _lookup(source, source_path)
    if value is _MISSING or value is None:
        return _MISSING
    if mode == "names":
        return sorted(str(key) for key in value) if isinstance(value, Mapping) else _MISSING
    if mode == "count":
        try:
            return len(value)
        except TypeError:
            return _MISSING
    return safe_value(source_path, value, home)


def _read_file_entry(file_spec: Mapping[str, Any], home: Path) -> dict[str, Any]:
    raw_path = file_spec.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigSnapshotError("provider file requires a non-empty path")
    path = expand_path(raw_path, home)
    result: dict[str, Any] = {
        "config_file": norm(str(path), home),
        "present": path.is_file(),
    }
    if not path.is_file():
        return result
    data = read_config(path, str(file_spec.get("format")) if file_spec.get("format") else None)
    if not isinstance(data, Mapping):
        result["error"] = "top-level config is not an object"
        return result
    if "_parse_error" in data:
        result["error"] = data["_parse_error"]
        return result

    settings: dict[str, Any] = {}
    for output, source, mode in _allowlist_entries(file_spec.get("allowlist", file_spec.get("allow"))):
        extracted = _extract(data, source, mode, home)
        if extracted is not _MISSING:
            settings[output] = extracted
    result["settings"] = settings

    for section, source_spec in (("counts", file_spec.get("counts")), ("names", file_spec.get("names"))):
        values: dict[str, Any] = {}
        for output, source, mode in _allowlist_entries(source_spec):
            effective_mode = "count" if section == "counts" and mode == "value" else mode
            if section == "names" and mode == "value":
                effective_mode = "names"
            extracted = _extract(data, source, effective_mode, home)
            if extracted is not _MISSING:
                values[output] = extracted
        if values:
            result[section] = values
    return result


def _env_entries(spec: Any) -> list[tuple[str, str]]:
    if isinstance(spec, Mapping):
        return [(str(output), str(source)) for output, source in spec.items() if isinstance(source, str)]
    if isinstance(spec, list):
        return [(item, item) for item in spec if isinstance(item, str)]
    return []


def _provider_entries(config: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    raw = config.get("providers", {})
    if isinstance(raw, Mapping):
        result = []
        for provider_id, spec in raw.items():
            if isinstance(spec, Mapping):
                result.append((str(provider_id), spec))
        return result
    if isinstance(raw, list):
        result = []
        for spec in raw:
            if isinstance(spec, Mapping) and isinstance(spec.get("id"), str):
                result.append((spec["id"], spec))
        return result
    raise ConfigSnapshotError("providers must be an object or list")


def load_provider_config(path: Path | None = None, state_dir: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    """Load the provider table without inventing provider-specific defaults."""

    state = state_dir or resolve_state_dir()
    candidate = path
    if candidate is None:
        raw = os.environ.get(CONFIG_ENV)
        candidate = Path(raw) if raw else state / DEFAULT_PROVIDER_CONFIG_NAME
    candidate = _as_path(candidate)
    if not candidate.is_file():
        return {"_schema": PROVIDER_CONFIG_SCHEMA, "providers": {}}, candidate
    try:
        value = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - invalid table is a clear CLI error.
        raise ConfigSnapshotError(f"provider table is not valid JSON: {candidate}") from exc
    if not isinstance(value, Mapping):
        raise ConfigSnapshotError("provider table must be a JSON object")
    schema = value.get("_schema") or value.get("schema")
    if schema and schema != PROVIDER_CONFIG_SCHEMA:
        raise ConfigSnapshotError(f"unsupported provider table schema: {schema}")
    ids: set[str] = set()
    for provider_id, spec in _provider_entries(value):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", provider_id):
            raise ConfigSnapshotError(f"unsafe provider id: {provider_id!r}")
        if provider_id in ids:
            raise ConfigSnapshotError(f"duplicate provider id: {provider_id}")
        ids.add(provider_id)
        if not isinstance(spec.get("files", spec.get("path", "")), (list, str, type(None))):
            raise ConfigSnapshotError(f"provider files must be a list: {provider_id}")
    return dict(value), candidate


def resolve_state_dir(cli_state_dir: str | os.PathLike[str] | None = None) -> Path:
    raw = cli_state_dir or os.environ.get(STATE_ENV)
    return _as_path(raw) if raw else _as_path(Path.cwd() / DEFAULT_STATE_DIR_NAME)


def resolve_slot(explicit: str | None = None) -> str:
    raw = explicit or os.environ.get(SLOT_ENV) or socket.gethostname() or "unknown"
    slot = SLOT_RE.sub("-", raw.strip()).strip("-.").lower()
    return slot or "unknown"


def collect_provider(provider_id: str, spec: Mapping[str, Any], home: Path) -> dict[str, Any]:
    files_spec = spec.get("files")
    if files_spec is None and isinstance(spec.get("path"), str):
        files_spec = [spec]
    if files_spec is None:
        files_spec = []
    if not isinstance(files_spec, list):
        raise ConfigSnapshotError(f"provider files must be a list: {provider_id}")

    files: dict[str, Any] = {}
    settings: dict[str, Any] = {}
    counts: dict[str, Any] = {}
    names: dict[str, Any] = {}
    present = False
    for index, file_spec in enumerate(files_spec):
        if not isinstance(file_spec, Mapping):
            raise ConfigSnapshotError(f"provider file entry must be an object: {provider_id}")
        file_id = str(file_spec.get("id") or file_spec.get("name") or f"file-{index + 1}")
        entry = _read_file_entry(file_spec, home)
        files[file_id] = entry
        present = present or bool(entry.get("present"))
        settings.update(entry.get("settings") or {})
        counts.update(entry.get("counts") or {})
        names.update(entry.get("names") or {})

    env: dict[str, Any] = {}
    for output, variable in _env_entries(spec.get("environment", spec.get("env"))):
        if variable in os.environ:
            # Redact according to both the configured output name and the
            # source variable name; a neutral label must not defeat a secret
            # environment-variable name.
            env[output] = safe_value(f"{output} {variable}", os.environ[variable], home)

    result: dict[str, Any] = {"present": present, "files": files, "settings": settings}
    if env:
        result["env"] = env
    if counts:
        result["counts"] = counts
    if names:
        result["names"] = names
    return result


def build_snapshot(
    provider_config: Mapping[str, Any] | None = None,
    *,
    config_file: Path | None = None,
    home: str | os.PathLike[str] | None = None,
    slot: str | None = None,
    generated: str | None = None,
) -> dict[str, Any]:
    """Collect one machine without reading anything outside configured paths."""

    home_path = resolve_home(home)
    config = provider_config
    if config is None:
        config = load_provider_config(config_file)[0]
    providers = {
        provider_id: collect_provider(provider_id, spec, home_path)
        for provider_id, spec in sorted(_provider_entries(config), key=lambda item: item[0])
    }
    return {
        "_schema": SCHEMA,
        "_hint": "Generated by scripts/config_snapshot.py; edit DEVIATIONS.md for rationale.",
        "host": socket.gethostname(),
        "slot": resolve_slot(slot),
        "platform": sys.platform,
        "home": "<HOME>",
        "generated": generated or _dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "config_file": norm(str(config_file), home_path) if config_file else None,
        "providers": providers,
    }


def load_snapshots(state_dir: Path) -> dict[str, dict[str, Any]]:
    snapshots_dir = state_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(snapshots_dir.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001 - one malformed host snapshot must not crash report.
            continue
        if not isinstance(value, dict) or value.get("_schema") != SCHEMA:
            continue
        slot = str(value.get("slot") or path.stem)
        result[slot] = value
    return result


def documented_keys(deviations_file: Path) -> set[str]:
    """Read only explicit rationale headings, never examples in code blocks."""

    if not deviations_file.is_file():
        return set()
    found: set[str] = set()
    in_code = False
    pattern = re.compile(r"\s*#{2,4}\s+`([A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)`\s*$")
    for line in deviations_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            match = pattern.fullmatch(line)
            if match:
                found.add(match.group(1))
    return found


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value).replace("|", "\\|").replace("\n", " ")


def _provider_section(snapshot: Mapping[str, Any], provider_id: str, section: str) -> Mapping[str, Any]:
    provider = snapshot.get("providers", {}).get(provider_id, {})
    value = provider.get(section, {}) if isinstance(provider, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def build_report(
    snapshots: Mapping[str, Mapping[str, Any]],
    *,
    documented: set[str] | None = None,
    generated: str | None = None,
) -> str:
    """Build deterministic Markdown showing only configured comparable values."""

    slots = sorted(snapshots)
    documented = documented or set()
    provider_ids = sorted(
        {
            provider_id
            for snapshot in snapshots.values()
            for provider_id in (snapshot.get("providers", {}) or {})
        }
    )
    lines = [
        "# Configuration State — comparable machine settings",
        "",
        f"> **Generated:** {generated or _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()} by `config_snapshot.py report`; do not edit.",
        f"> **Sources:** {len(slots)} snapshot(s) in `snapshots/`; rationale lives in `DEVIATIONS.md`.",
        "",
        "Legend: **=** equal on all present systems · **!** unexplained difference · **~** documented difference · blank = unset.",
        "",
        "## Systems",
        "",
        "| Slot | Host | Platform | Snapshot |",
        "|---|---|---|---|",
    ]
    for slot in slots:
        snapshot = snapshots[slot]
        lines.append(
            f"| `{_display(slot)}` | `{_display(snapshot.get('host', '?'))}` | `{_display(snapshot.get('platform', '?'))}` | `{_display(str(snapshot.get('generated', '?'))[:16])}` |"
        )
    lines.append("")

    open_deviations: set[str] = set()
    if not provider_ids:
        lines.extend(["> No provider table or snapshots supplied; configure providers before collecting state.", ""])

    for provider_id in provider_ids:
        lines.extend([f"## {provider_id}", ""])
        # A provider may be absent on a slot; keep that slot in the comparison
        # so the report can make the absence explicit instead of hiding drift.
        active = list(slots)
        present = [
            slot
            for slot in active
            if bool((snapshots[slot].get("providers", {}).get(provider_id) or {}).get("present"))
        ]
        missing = [slot for slot in active if slot not in present]
        if missing:
            lines.append(f"> Not present on: {', '.join(f'`{_display(slot)}`' for slot in missing)}")
            lines.append("")
        if not present:
            lines.extend(["*(not present on any system)*", ""])
            continue

        for section in ("settings", "env"):
            rows: dict[str, dict[str, Any]] = {}
            for slot in present:
                for key, value in _provider_section(snapshots[slot], provider_id, section).items():
                    rows.setdefault(str(key), {})[slot] = value
            if not rows:
                continue
            lines.extend([f"### {section}", "", "| Setting | " + " | ".join(f"`{s}`" for s in present) + " | Status |", "|---" * (len(present) + 2) + "|"])
            for key in sorted(rows):
                values = [rows[key].get(slot, _MISSING) for slot in present]
                comparable = [value for value in values if value is not _MISSING]
                same = len(comparable) == len(present) and len({json.dumps(v, sort_keys=True, ensure_ascii=False) for v in comparable}) <= 1
                full_key = f"{provider_id}.{key}" if section == "settings" else f"{provider_id}.env.{key}"
                if same:
                    status = "="
                elif full_key in documented:
                    status = "~"
                else:
                    status = "!"
                    open_deviations.add(full_key)
                cells = " | ".join("" if value is _MISSING else f"`{_display(value)}`" for value in values)
                lines.append(f"| `{_display(key)}` | {cells} | {status} |")
            lines.append("")

        count_rows: dict[str, dict[str, Any]] = {}
        for slot in present:
            for key, value in _provider_section(snapshots[slot], provider_id, "counts").items():
                count_rows.setdefault(str(key), {})[slot] = value
        if count_rows:
            lines.extend(["### Counts (informational)", "", "| Metric | " + " | ".join(f"`{s}`" for s in present) + " |", "|---" * (len(present) + 1) + "|"])
            for key in sorted(count_rows):
                lines.append(f"| `{_display(key)}` | " + " | ".join(_display(count_rows[key].get(slot, "")) for slot in present) + " |")
            lines.append("")

    lines.extend(["## Open deviations (without rationale)", ""])
    if open_deviations:
        lines.extend(["These differences are not documented in `DEVIATIONS.md`:", ""])
        lines.extend(f"- `{key}`" for key in sorted(open_deviations))
    elif len(slots) < 2:
        if slots:
            lines.extend([f"**No comparison possible yet** — only `{slots[0]}` has a snapshot.", "Collect at least one more machine before treating the report as an all-clear."])
        else:
            lines.append("**No comparison possible yet** — no snapshots exist.")
    else:
        lines.append("None — every observed difference has a rationale.")
    lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, content: str, check: bool) -> None:
    if check:
        print(f"[check] would write: {path} ({len(content)} characters)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    print(f"written: {path}")


def _print_snapshot_summary(snapshot: Mapping[str, Any]) -> None:
    for provider_id, provider in snapshot.get("providers", {}).items():
        state = "ok" if provider.get("present") else "missing"
        print(f"  {provider_id:24} {state:7} {len(provider.get('settings') or {}):3} setting(s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Allowlisted, home-normalised configuration state snapshots.")
    parser.add_argument("mode", choices=("snapshot", "report", "all"), nargs="?", default="all")
    parser.add_argument("--state-dir", default=None, help=f"State directory (or {STATE_ENV})")
    parser.add_argument("--config", default=None, help=f"Provider table JSON (or {CONFIG_ENV})")
    parser.add_argument("--slot", default=None, help=f"Machine slot (or {SLOT_ENV})")
    parser.add_argument("--home", default=None, help="Comparison home; intended for tests and controlled wrappers")
    parser.add_argument("--check", action="store_true", help="Preview writes without changing files")
    args = parser.parse_args(argv)

    state_dir = resolve_state_dir(args.state_dir)
    config_path = _as_path(args.config) if args.config else None
    try:
        config: dict[str, Any] | None = None
        resolved_config: Path | None = None
        if args.mode in ("snapshot", "all"):
            config, resolved_config = load_provider_config(config_path, state_dir)
            snapshot = build_snapshot(config, config_file=resolved_config, home=args.home, slot=args.slot)
            target = state_dir / "snapshots" / f"{snapshot['slot']}.json"
            _print_snapshot_summary(snapshot)
            atomic_write(target, json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", args.check)
        if args.mode in ("report", "all"):
            snapshots = load_snapshots(state_dir)
            report = build_report(snapshots, documented=documented_keys(state_dir / "DEVIATIONS.md"))
            print(f"  snapshots found: {', '.join(sorted(snapshots)) or '(none)'}")
            atomic_write(state_dir / "CONFIG-STATE.md", report, args.check)
        return 0
    except (ConfigSnapshotError, OSError) as exc:
        print(f"config_snapshot: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
