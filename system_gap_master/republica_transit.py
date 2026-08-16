"""Yard-side path resolution for the Republica showcase companion tool.

This module does **not** implement Republica. Encryption, snapshotting,
publish/list/import and the sealed-envelope courier live exclusively in the
companion package `sqlite-transit-sync` — see its README section "Republica —
the showcase method" and ADR-006 through ADR-010 in its `DECISIONS.md`. What
this module answers is the one question a yard user would otherwise have to
guess: *which path inside this yard is the tool-owned transit zone for a given
namespace* (protocol rule R9), and *is a proposed `republica_root` safe to use
as the local import destination* (it must stay outside both the transit zone
and the yard as a whole).

`sqlite_transit_sync` is never imported here, not even optionally — this
module is pure path arithmetic over the local filesystem, so it keeps working
without the companion package installed. Use
:func:`sqlite_transit_sync_available` to warn a caller before pointing them at
commands that need it.

Why "outside the whole yard" and not just "outside the transit directory"
(which is all the companion tool itself requires): a materialised Republica
showcase is a decrypted, host-local read-only copy. Anything placed inside the
yard is subject to R1 (foreign hosts must not be edited/relied upon as their
own slot) and R3 (the yard archives integrated items rather than keeping them
forever) — neither behaviour is appropriate for a decrypted showcase that a
daily sync ritual should never touch, move or archive.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

NAMESPACE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
TRANSIT_ZONE_NAME = "db-transit"

RESOLVE_SCHEMA = "system-gap.republica-transit.resolve.v1"
CHECK_ROOT_SCHEMA = "system-gap.republica-transit.check-root.v1"
ERROR_SCHEMA = "system-gap.republica-transit.error.v1"


class RepublicaTransitError(RuntimeError):
    """Raised when a yard/namespace/root combination violates R9."""


def _lexical_absolute(raw: str | Path, label: str) -> Path:
    text = str(raw).strip()
    if not text:
        raise RepublicaTransitError(f"{label} must be a non-empty path")
    if text.replace("/", "\\").startswith("\\\\"):
        raise RepublicaTransitError(
            f"{label} must be host-local; UNC and device namespaces are forbidden"
        )
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise RepublicaTransitError(f"{label} must be absolute")
    return candidate


def _comparison_path(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise RepublicaTransitError(f"cannot resolve path boundary: {path}") from exc
    return Path(os.path.normcase(os.path.abspath(resolved)))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _overlaps(left: Path, right: Path) -> bool:
    left_cmp = _comparison_path(left)
    right_cmp = _comparison_path(right)
    return _is_relative_to(left_cmp, right_cmp) or _is_relative_to(right_cmp, left_cmp)


@dataclass(frozen=True)
class RepublicaTransitPaths:
    """Result of resolving the R9 transit zone for one namespace."""

    yard_root: Path
    namespace: str
    transit: Path
    zone_relative: str


def resolve_republica_transit(
    yard_root: str | Path, namespace: str
) -> RepublicaTransitPaths:
    """Resolve the R9 tool-owned transit zone for ``namespace`` inside ``yard_root``.

    Returns the path to hand to sqlite-transit-sync as its ``transit`` config
    key. Republica reuses the same ``transit`` directory as ``push``/``pull``
    (see the companion tool's ``RepublicaTransit._own_transit``, which
    publishes under ``<transit>/<node_id>/…`` inside it) — this function only
    fixes *where inside the yard* that directory lives, so two hosts do not
    have to agree on the convention by hand.

    Does not create the directory: sqlite-transit-sync creates it lazily on
    first ``push`` or ``republica-publish``. Per R9, nothing under
    ``db-transit/`` is touched by the daily sync ritual (no archiving, no
    foreign-slot rule) — the tool owns its own zone's lifecycle.
    """
    root = _lexical_absolute(yard_root, "yard_root")
    if not root.is_dir():
        raise RepublicaTransitError(f"yard_root must be an existing directory: {root}")
    ns = str(namespace).strip()
    if not NAMESPACE_RE.fullmatch(ns):
        raise RepublicaTransitError(
            "namespace must be a lowercase, path-safe identifier "
            "(letters, digits, '.', '_', '-', max 64 chars): "
            f"{namespace!r}"
        )
    transit = root / TRANSIT_ZONE_NAME / ns
    return RepublicaTransitPaths(
        yard_root=root,
        namespace=ns,
        transit=transit,
        zone_relative=f"{TRANSIT_ZONE_NAME}/{ns}",
    )


def assert_republica_root_outside_yard(
    republica_root: str | Path, yard_root: str | Path
) -> Path:
    """Fail closed if a proposed ``republica_root`` overlaps the yard.

    sqlite-transit-sync itself already refuses a ``republica_root`` that
    equals or nests inside its own ``transit`` directory (ADR-007). This
    function adds the stronger, yard-level check: ``republica_root`` — the
    destination for imported, decrypted showcases — must stay outside the
    *entire* yard, not just the transit zone, because R1/R3 apply to
    everything inside the yard and neither is appropriate for a decrypted
    local copy.

    Returns the validated absolute path on success; raises
    :class:`RepublicaTransitError` otherwise.
    """
    root = _lexical_absolute(yard_root, "yard_root")
    candidate = _lexical_absolute(republica_root, "republica_root")
    if _overlaps(candidate, root):
        raise RepublicaTransitError(
            f"republica_root must stay outside the yard, not inside or "
            f"above it: {candidate} vs yard {root}"
        )
    return candidate


def sqlite_transit_sync_available() -> bool:
    """Report whether the optional companion package can be imported.

    This module never imports ``sqlite_transit_sync`` itself — callers use
    this only to produce a clearer error message before shelling out to the
    ``sqlite-transit-sync`` CLI or importing it themselves. A ``False`` result
    is not an error condition here: this module's own path resolution works
    identically with or without the companion installed.
    """
    return importlib.util.find_spec("sqlite_transit_sync") is not None


def config_fragment(paths: RepublicaTransitPaths) -> dict[str, Any]:
    """A ready-to-paste fragment for a node's sqlite-transit-sync config.

    Only the yard-derived keys are filled in. ``database``, ``node_id`` and
    ``key_file`` stay host-specific and are deliberately left for the caller
    to fill in — this module has no opinion on them and must not guess.
    """
    return {
        "transit": str(paths.transit),
        "namespace": paths.namespace,
    }


def _dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="republica-transit",
        description=(
            "Resolve the R9 tool-owned transit zone for a Republica namespace "
            "inside a system-gap-master yard, or validate a republica_root "
            "destination. Does not publish, encrypt or transfer anything — "
            "that remains sqlite-transit-sync's job."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser(
        "resolve", help="resolve the db-transit/<namespace> zone for a namespace"
    )
    resolve.add_argument("--yard-root", required=True)
    resolve.add_argument("--namespace", required=True)

    check_root = subparsers.add_parser(
        "check-root",
        help="validate that a republica_root destination stays outside the yard",
    )
    check_root.add_argument("--yard-root", required=True)
    check_root.add_argument("--republica-root", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "resolve":
            paths = resolve_republica_transit(args.yard_root, args.namespace)
            result: dict[str, Any] = {
                "schema": RESOLVE_SCHEMA,
                "yard_root": str(paths.yard_root),
                "namespace": paths.namespace,
                "transit": str(paths.transit),
                "zone_relative": paths.zone_relative,
                "config_fragment": config_fragment(paths),
                "sqlite_transit_sync_available": sqlite_transit_sync_available(),
            }
        else:
            validated = assert_republica_root_outside_yard(
                args.republica_root, args.yard_root
            )
            result = {
                "schema": CHECK_ROOT_SCHEMA,
                "yard_root": str(_lexical_absolute(args.yard_root, "yard_root")),
                "republica_root": str(validated),
                "outside_yard": True,
            }
        _dump(result)
        return 0
    except (RepublicaTransitError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"schema": ERROR_SCHEMA, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
