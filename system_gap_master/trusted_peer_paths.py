"""Read-only trusted-peer registry validation and pull preparation.

This module deliberately stops before connectivity.  It reads only the
explicit local configuration and the host-owned registry JSON, validates
metadata, and returns deterministic preparation receipts.  It never reads a
referenced credential, key, signature, known-hosts file, or remote path; it
never invokes SSH/SFTP and never writes to the sync yard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence


LOCAL_CONFIG_SCHEMA = "system-gap.trusted-peer-paths.local-config.v2"
REGISTRY_SCHEMA = "system-gap.trusted-peer-paths.registry.v2"
RECEIPT_SCHEMA = "system-gap.trusted-peer-pull-preparation-receipt.v1"
ERROR_SCHEMA = "system-gap.trusted-peer-paths.error.v1"

HOST_ID_RE = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,63}")
ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
NETWORK_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}")
PIN_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
SIGNATURE_REF_RE = re.compile(r"urn:[A-Za-z0-9][A-Za-z0-9:._/-]{7,255}")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_JSON_BYTES = 1024 * 1024
WINDOWS_REPARSE_ATTRIBUTE = 0x400
WINDOWS_DEVICE_STEMS = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
    "com¹",
    "com²",
    "com³",
    "lpt¹",
    "lpt²",
    "lpt³",
}
WINDOWS_FORBIDDEN_COMPONENT_CHARS = frozenset('<>:"/\\|?*')
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", "-wal", "-shm")
NETWORK_LABELS = {"direct", "private-overlay"}
SIGNATURE_ALGORITHMS = {"external-ed25519", "external-ssh-signature"}

SECRET_FIELD_NAMES = {
    "api_key",
    "authorization",
    "credential",
    "credential_content",
    "credential_value",
    "file_content",
    "password",
    "private_key",
    "secret",
    "secret_key",
    "token",
    "value",
}
SECRET_MARKERS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:password|passwd|api[_-]?key|secret|token)\s*[:=]", re.IGNORECASE),
    re.compile(r"\bAuthorization\s*:\s*(?:Bearer|Basic)\s+", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)
OPAQUE_SECRET_RE = re.compile(
    r"(?=.{40,}\Z)(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z0-9+/=_-]+"
)
HEX_SECRET_RE = re.compile(r"[A-Fa-f0-9]{40,}")
OPAQUE_METADATA_FIELDS = {
    "known_host_pin",
    "payload_sha256",
    "plan_id",
    "registry_sha256",
    "sha256",
}


class TrustedPeerPathError(RuntimeError):
    """Raised when a registry or plan violates a fail-closed boundary."""


@dataclass(frozen=True)
class TrustedHost:
    """Pinned, host-local trust metadata.  It contains no secret material."""

    host_id: str
    min_revision: int
    signature_algorithm: str
    key_id: str
    signature_reference: str
    allowed_network_labels: tuple[str, ...]
    allowed_remote_paths: tuple[str, ...]
    known_host_pins: Mapping[str, str]


@dataclass(frozen=True)
class PullPlan:
    """A deterministic no-transfer preparation receipt."""

    value: Mapping[str, Any]

    @property
    def executable(self) -> bool:
        """Preparation receipts are never executable."""

        return False

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.value))


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TrustedPeerPathError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise TrustedPeerPathError(f"non-finite JSON number is forbidden: {value}")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    _assert_no_reparse_components(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise TrustedPeerPathError(f"{label} must be a regular file")
        if before.st_size > MAX_JSON_BYTES:
            raise TrustedPeerPathError(f"{label} exceeds {MAX_JSON_BYTES} bytes")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            _assert_no_reparse_components(path)
            after = path.lstat()
            identities = {
                (before.st_dev, before.st_ino),
                (opened.st_dev, opened.st_ino),
                (after.st_dev, after.st_ino),
            }
            if len(identities) != 1 or not stat.S_ISREG(opened.st_mode):
                raise TrustedPeerPathError(
                    f"{label} changed identity or became unsafe while opening"
                )
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                raw = handle.read(MAX_JSON_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(raw) > MAX_JSON_BYTES:
            raise TrustedPeerPathError(f"{label} exceeds {MAX_JSON_BYTES} bytes")
    except OSError as exc:
        raise TrustedPeerPathError(f"cannot read {label}: {path}") from exc
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedPeerPathError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TrustedPeerPathError(f"{label} must be a JSON object")
    _assert_secret_free(value, label)
    return value


def _assert_secret_free(value: Any, label: str, trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in SECRET_FIELD_NAMES:
                raise TrustedPeerPathError(
                    f"{label} contains forbidden secret/content field: {'.'.join((*trail, key))}"
                )
            _assert_secret_free(child, label, (*trail, key))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_secret_free(child, label, (*trail, str(index)))
        return
    if isinstance(value, str):
        for marker in SECRET_MARKERS:
            if marker.search(value):
                raise TrustedPeerPathError(
                    f"{label} appears to contain secret or file content at "
                    f"{'.'.join(trail) or '<root>'}"
                )
        field = trail[-1].lower().replace("-", "_") if trail else ""
        if field not in OPAQUE_METADATA_FIELDS and (
            OPAQUE_SECRET_RE.fullmatch(value) or HEX_SECRET_RE.fullmatch(value)
        ):
            raise TrustedPeerPathError(
                f"{label} appears to contain opaque secret material at "
                f"{'.'.join(trail) or '<root>'}"
            )


def _expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrustedPeerPathError(f"{label} must be an object")
    return dict(value)


def _expect_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    label: str,
) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise TrustedPeerPathError(
            f"{label} missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise TrustedPeerPathError(
            f"{label} has forbidden fields: {', '.join(sorted(unknown))}"
        )


def _expect_string(value: Any, label: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise TrustedPeerPathError(f"{label} must be a non-empty string")
    if CONTROL_RE.search(value):
        raise TrustedPeerPathError(f"{label} contains control characters")
    return value


def _expect_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrustedPeerPathError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise TrustedPeerPathError(f"{label} must be between {minimum} and {maximum}")
    return value


def _safe_host_id(value: Any, label: str) -> str:
    result = _expect_string(value, label, 64)
    if not HOST_ID_RE.fullmatch(result):
        raise TrustedPeerPathError(f"{label} is not a canonical uppercase host/peer ID")
    _reject_windows_alias(result, label)
    return result


def _safe_id(value: Any, label: str) -> str:
    result = _expect_string(value, label, 64)
    if not ID_RE.fullmatch(result):
        raise TrustedPeerPathError(f"{label} is not a canonical lowercase ID")
    _reject_windows_alias(result, label)
    return result


def _reject_windows_alias(value: str, label: str) -> None:
    stem = value.split(".", 1)[0].rstrip(" .").lower()
    if (
        stem in WINDOWS_DEVICE_STEMS
        or value.endswith((" ", "."))
        or any(character in WINDOWS_FORBIDDEN_COMPONENT_CHARS for character in value)
    ):
        raise TrustedPeerPathError(f"{label} uses a forbidden filesystem alias")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except OSError:
        return False
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & WINDOWS_REPARSE_ATTRIBUTE)


def _lexical_absolute(raw: str | Path, label: str) -> Path:
    text = _expect_string(str(raw), label)
    if text.replace("/", "\\").startswith("\\\\"):
        raise TrustedPeerPathError(
            f"{label} must be host-local; UNC and device namespaces are forbidden"
        )
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise TrustedPeerPathError(f"{label} must be absolute")
    for part in candidate.parts[1:]:
        if part in {".", ".."}:
            raise TrustedPeerPathError(f"{label} contains traversal")
        _reject_windows_alias(part, label)
    return Path(os.path.abspath(candidate))


def _assert_no_reparse_components(path: Path, *, include_leaf: bool = True) -> None:
    target = path if include_leaf else path.parent
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_link_or_reparse(current):
            raise TrustedPeerPathError(
                f"path crosses a symlink, junction or reparse point: {current}"
            )


def _comparison_path(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise TrustedPeerPathError(f"cannot resolve path boundary: {path}") from exc
    return Path(os.path.normcase(os.path.abspath(resolved)))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _within(path: Path, root: Path) -> bool:
    return _is_relative_to(_comparison_path(path), _comparison_path(root))


def _overlap(left: Path, right: Path) -> bool:
    left_cmp = _comparison_path(left)
    right_cmp = _comparison_path(right)
    return _is_relative_to(left_cmp, right_cmp) or _is_relative_to(right_cmp, left_cmp)


def _validate_remote_path(value: Any, label: str) -> str:
    result = _expect_string(value, label)
    if "\\" in result or any(character in result for character in "*?[]{}$`"):
        raise TrustedPeerPathError(f"{label} is not a conservative SFTP path")
    pure = PurePosixPath(result)
    if not pure.is_absolute() or any(
        part in {"", ".", ".."} for part in pure.parts[1:]
    ):
        raise TrustedPeerPathError(f"{label} must be an absolute normalized POSIX path")
    normalized = str(pure)
    if normalized != result or result == "/":
        raise TrustedPeerPathError(f"{label} must be an exact normalized file path")
    return result


def _looks_like_sqlite(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(SQLITE_SUFFIXES)


def _parse_time(value: Any, label: str) -> datetime:
    text = _expect_string(value, label, 64)
    if not text.endswith("Z"):
        raise TrustedPeerPathError(f"{label} must be UTC and end in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise TrustedPeerPathError(f"{label} is not an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TrustedPeerPathError(f"{label} must be UTC")
    return parsed


def _validate_pin(value: Any, label: str) -> str:
    result = _expect_string(value, label, 80)
    if not PIN_RE.fullmatch(result):
        raise TrustedPeerPathError(
            f"{label} must be a verified OpenSSH SHA256 host-key pin"
        )
    return result


def _validate_signature_ref(value: Any, label: str) -> str:
    result = _expect_string(value, label, 260)
    if not SIGNATURE_REF_RE.fullmatch(result):
        raise TrustedPeerPathError(f"{label} must be a safe pinned URN")
    return result


def _validate_endpoint(raw: Any, label: str) -> dict[str, Any]:
    endpoint = _expect_object(raw, label)
    _expect_keys(
        endpoint,
        required={
            "endpoint_id",
            "transport",
            "network_label",
            "host",
            "port",
            "username",
            "read_only",
            "known_host_pin",
        },
        optional={"description"},
        label=label,
    )
    endpoint_id = _safe_id(endpoint["endpoint_id"], f"{label}.endpoint_id")
    if endpoint["transport"] != "sftp":
        raise TrustedPeerPathError(f"{label}.transport must be sftp")
    network_label = _expect_string(
        endpoint["network_label"], f"{label}.network_label", 32
    )
    if network_label not in NETWORK_LABELS:
        raise TrustedPeerPathError(
            f"{label}.network_label must be direct or private-overlay"
        )
    host = _expect_string(endpoint["host"], f"{label}.host", 253)
    if not NETWORK_HOST_RE.fullmatch(host):
        raise TrustedPeerPathError(f"{label}.host is not a safe literal host label")
    username = _expect_string(endpoint["username"], f"{label}.username", 64)
    if not USERNAME_RE.fullmatch(username):
        raise TrustedPeerPathError(f"{label}.username is invalid")
    if endpoint["read_only"] is not True:
        raise TrustedPeerPathError(f"{label}.read_only must be true")
    result = {
        "endpoint_id": endpoint_id,
        "transport": "sftp",
        "network_label": network_label,
        "host": host,
        "port": _expect_int(endpoint["port"], f"{label}.port", 1, 65535),
        "username": username,
        "read_only": True,
        "known_host_pin": _validate_pin(
            endpoint["known_host_pin"], f"{label}.known_host_pin"
        ),
    }
    if "description" in endpoint:
        result["description"] = _expect_string(
            endpoint["description"], f"{label}.description", 256
        )
    return result


def _validate_path(raw: Any, endpoints: set[str], label: str) -> dict[str, Any]:
    entry = _expect_object(raw, label)
    _expect_keys(
        entry,
        required={
            "path_id",
            "kind",
            "metadata_type",
            "content_included",
            "remote_path",
            "endpoint_id",
            "allowed_peer_ids",
            "direct_pull",
        },
        optional={"adapter", "description"},
        label=label,
    )
    path_id = _safe_id(entry["path_id"], f"{label}.path_id")
    kind = _expect_string(entry["kind"], f"{label}.kind", 32)
    if kind not in {"file", "directory", "database/sqlite"}:
        raise TrustedPeerPathError(f"{label}.kind is unsupported")
    if entry["metadata_type"] != "path-location":
        raise TrustedPeerPathError(f"{label}.metadata_type must be path-location")
    if entry["content_included"] is not False:
        raise TrustedPeerPathError(f"{label}.content_included must be false")
    remote_path = _validate_remote_path(entry["remote_path"], f"{label}.remote_path")
    endpoint_id = _safe_id(entry["endpoint_id"], f"{label}.endpoint_id")
    if endpoint_id not in endpoints:
        raise TrustedPeerPathError(f"{label}.endpoint_id is unknown")
    peers = entry["allowed_peer_ids"]
    if not isinstance(peers, list) or not peers:
        raise TrustedPeerPathError(f"{label}.allowed_peer_ids must be non-empty")
    allowed_peer_ids = [
        _safe_host_id(peer, f"{label}.allowed_peer_ids") for peer in peers
    ]
    if len(set(allowed_peer_ids)) != len(allowed_peer_ids):
        raise TrustedPeerPathError(f"{label}.allowed_peer_ids has duplicates")
    if not isinstance(entry["direct_pull"], bool):
        raise TrustedPeerPathError(f"{label}.direct_pull must be boolean")
    adapter = entry.get("adapter")
    if adapter is not None:
        adapter = _safe_id(adapter, f"{label}.adapter")
    if kind == "database/sqlite" or _looks_like_sqlite(remote_path):
        if kind != "database/sqlite":
            raise TrustedPeerPathError(
                f"{label} disguises SQLite state as an ordinary path"
            )
        if entry["direct_pull"] is not False or adapter != "sqlite-transit-sync":
            raise TrustedPeerPathError(
                f"{label} SQLite paths require direct_pull=false and "
                "adapter=sqlite-transit-sync"
            )
    if kind == "directory" and entry["direct_pull"]:
        raise TrustedPeerPathError(f"{label} directory direct pull is unsupported")
    result = {
        "path_id": path_id,
        "kind": kind,
        "metadata_type": "path-location",
        "content_included": False,
        "remote_path": remote_path,
        "endpoint_id": endpoint_id,
        "allowed_peer_ids": sorted(allowed_peer_ids),
        "direct_pull": entry["direct_pull"],
    }
    if adapter is not None:
        result["adapter"] = adapter
    if "description" in entry:
        result["description"] = _expect_string(
            entry["description"], f"{label}.description", 512
        )
    return result


class TrustedPeerPathRegistry:
    """Validate a host-owned registry and prepare no-transfer receipts."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        raw = _expect_object(config, "config")
        _assert_secret_free(raw, "config")
        _expect_keys(
            raw,
            required={
                "schema",
                "yard_root",
                "local_host_id",
                "local_peer_id",
                "trusted_hosts",
                "pull_destination_roots",
                "max_registry_age_seconds",
                "max_registry_ttl_seconds",
            },
            label="config",
        )
        if raw["schema"] != LOCAL_CONFIG_SCHEMA:
            raise TrustedPeerPathError(f"config.schema must be {LOCAL_CONFIG_SCHEMA}")
        self.local_host_id = _safe_host_id(raw["local_host_id"], "config.local_host_id")
        self.local_peer_id = _safe_host_id(raw["local_peer_id"], "config.local_peer_id")
        self.yard_root = _lexical_absolute(raw["yard_root"], "config.yard_root")
        _assert_no_reparse_components(self.yard_root)
        if not self.yard_root.is_dir():
            raise TrustedPeerPathError("config.yard_root must be an existing directory")
        self.max_registry_age_seconds = _expect_int(
            raw["max_registry_age_seconds"],
            "config.max_registry_age_seconds",
            1,
            604800,
        )
        self.max_registry_ttl_seconds = _expect_int(
            raw["max_registry_ttl_seconds"],
            "config.max_registry_ttl_seconds",
            1,
            604800,
        )
        self.destination_roots = self._load_destination_roots(
            raw["pull_destination_roots"]
        )
        self.trusted_hosts = self._load_trusted_hosts(raw["trusted_hosts"])
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> "TrustedPeerPathRegistry":
        config_path = _lexical_absolute(path, "config path")
        _assert_no_reparse_components(config_path)
        return cls(_read_json(config_path, "local config"), clock=clock)

    def _load_destination_roots(self, raw: Any) -> tuple[Path, ...]:
        if not isinstance(raw, list) or not raw:
            raise TrustedPeerPathError(
                "config.pull_destination_roots must be a non-empty array"
            )
        roots: list[Path] = []
        for index, value in enumerate(raw):
            root = _lexical_absolute(value, f"config.pull_destination_roots[{index}]")
            _assert_no_reparse_components(root)
            if not root.is_dir():
                raise TrustedPeerPathError(
                    "every pull_destination_root must be an existing directory"
                )
            if _overlap(root, self.yard_root):
                raise TrustedPeerPathError(
                    "pull destination roots must be host-local and outside the yard"
                )
            roots.append(root)
        if len({_comparison_path(root) for root in roots}) != len(roots):
            raise TrustedPeerPathError("pull_destination_roots has duplicates")
        return tuple(roots)

    def _load_trusted_hosts(self, raw: Any) -> dict[str, TrustedHost]:
        if not isinstance(raw, list) or not raw:
            raise TrustedPeerPathError("config.trusted_hosts must be non-empty")
        result: dict[str, TrustedHost] = {}
        for index, item in enumerate(raw):
            label = f"config.trusted_hosts[{index}]"
            host = _expect_object(item, label)
            _expect_keys(
                host,
                required={
                    "host_id",
                    "min_revision",
                    "signature_algorithm",
                    "key_id",
                    "signature_reference",
                    "allowed_network_labels",
                    "allowed_remote_paths",
                    "known_host_pins",
                },
                label=label,
            )
            host_id = _safe_host_id(host["host_id"], f"{label}.host_id")
            if host_id == self.local_host_id:
                raise TrustedPeerPathError("trusted host must be a peer host")
            if host_id in result:
                raise TrustedPeerPathError(f"duplicate trusted host: {host_id}")
            algorithm = _expect_string(
                host["signature_algorithm"], f"{label}.signature_algorithm", 64
            )
            if algorithm not in SIGNATURE_ALGORITHMS:
                raise TrustedPeerPathError(
                    f"{label}.signature_algorithm is unsupported"
                )
            networks = host["allowed_network_labels"]
            if not isinstance(networks, list) or not networks:
                raise TrustedPeerPathError(
                    f"{label}.allowed_network_labels must be non-empty"
                )
            network_values = tuple(
                _expect_string(item, f"{label}.allowed_network_labels", 32)
                for item in networks
            )
            if len(set(network_values)) != len(network_values) or not set(
                network_values
            ).issubset(NETWORK_LABELS):
                raise TrustedPeerPathError(f"{label}.allowed_network_labels is invalid")
            paths = host["allowed_remote_paths"]
            if not isinstance(paths, list) or not paths:
                raise TrustedPeerPathError(
                    f"{label}.allowed_remote_paths must be non-empty"
                )
            remote_paths = tuple(
                _validate_remote_path(item, f"{label}.allowed_remote_paths")
                for item in paths
            )
            if len(set(remote_paths)) != len(remote_paths):
                raise TrustedPeerPathError(
                    f"{label}.allowed_remote_paths has duplicates"
                )
            pins_raw = host["known_host_pins"]
            if not isinstance(pins_raw, list) or not pins_raw:
                raise TrustedPeerPathError(f"{label}.known_host_pins must be non-empty")
            pins: dict[str, str] = {}
            for pin_index, pin_raw in enumerate(pins_raw):
                pin_label = f"{label}.known_host_pins[{pin_index}]"
                pin = _expect_object(pin_raw, pin_label)
                _expect_keys(
                    pin,
                    required={"endpoint_id", "sha256"},
                    label=pin_label,
                )
                endpoint_id = _safe_id(pin["endpoint_id"], f"{pin_label}.endpoint_id")
                if endpoint_id in pins:
                    raise TrustedPeerPathError(
                        f"{label}.known_host_pins has duplicate endpoint IDs"
                    )
                pins[endpoint_id] = _validate_pin(pin["sha256"], f"{pin_label}.sha256")
            result[host_id] = TrustedHost(
                host_id=host_id,
                min_revision=_expect_int(
                    host["min_revision"], f"{label}.min_revision", 1, 2**63 - 1
                ),
                signature_algorithm=algorithm,
                key_id=_safe_id(host["key_id"], f"{label}.key_id"),
                signature_reference=_validate_signature_ref(
                    host["signature_reference"], f"{label}.signature_reference"
                ),
                allowed_network_labels=tuple(sorted(network_values)),
                allowed_remote_paths=tuple(sorted(remote_paths)),
                known_host_pins=pins,
            )
        return result

    def _trust_for(self, host_id: str) -> TrustedHost:
        canonical = _safe_host_id(host_id, "host_id")
        trust = self.trusted_hosts.get(canonical)
        if trust is None:
            raise TrustedPeerPathError(f"host is not trusted: {canonical}")
        return trust

    def _registry_path(self, host_id: str) -> Path:
        trust = self._trust_for(host_id)
        path = (
            self.yard_root
            / "hosts"
            / trust.host_id
            / "trusted-peer-paths"
            / "registry.json"
        )
        _assert_no_reparse_components(path)
        if not path.is_file() or _is_link_or_reparse(path):
            raise TrustedPeerPathError(
                f"host-owned registry is missing or unsafe: {path}"
            )
        if not _within(path, self.yard_root / "hosts" / trust.host_id):
            raise TrustedPeerPathError("registry escapes its host-owned slot")
        return path

    def validate(self, host_id: str, *, record: bool | None = None) -> dict[str, Any]:
        # ``record`` remains accepted for API compatibility.  This read-only
        # implementation deliberately has no validation-state writer.
        del record
        trust = self._trust_for(host_id)
        registry_path = self._registry_path(trust.host_id)
        registry = _read_json(registry_path, "registry")
        _expect_keys(
            registry,
            required={
                "schema",
                "host_id",
                "revision",
                "published_at",
                "expires_at",
                "endpoints",
                "paths",
                "signature_reference",
            },
            label="registry",
        )
        if registry["schema"] != REGISTRY_SCHEMA:
            raise TrustedPeerPathError(f"registry.schema must be {REGISTRY_SCHEMA}")
        registry_host = _safe_host_id(registry["host_id"], "registry.host_id")
        if registry_host != trust.host_id:
            raise TrustedPeerPathError("registry host does not match its owner slot")
        revision = _expect_int(registry["revision"], "registry.revision", 1, 2**63 - 1)
        if revision < trust.min_revision:
            raise TrustedPeerPathError("registry revision is below the local pin")

        published_at = _parse_time(registry["published_at"], "registry.published_at")
        expires_at = _parse_time(registry["expires_at"], "registry.expires_at")
        now = self._clock()
        if now.tzinfo is None:
            raise TrustedPeerPathError("clock must return a timezone-aware value")
        now = now.astimezone(timezone.utc)
        if published_at > now:
            raise TrustedPeerPathError("registry published_at is in the future")
        age = (now - published_at).total_seconds()
        ttl = (expires_at - published_at).total_seconds()
        if age > self.max_registry_age_seconds:
            raise TrustedPeerPathError("registry is stale")
        if expires_at <= now:
            raise TrustedPeerPathError("registry is expired")
        if ttl <= 0 or ttl > self.max_registry_ttl_seconds:
            raise TrustedPeerPathError("registry expiry exceeds the configured TTL")

        signature = _expect_object(
            registry["signature_reference"], "registry.signature_reference"
        )
        _expect_keys(
            signature,
            required={"algorithm", "key_id", "ref", "payload_sha256"},
            label="registry.signature_reference",
        )
        algorithm = _expect_string(
            signature["algorithm"], "registry.signature_reference.algorithm", 64
        )
        key_id = _safe_id(signature["key_id"], "registry.signature_reference.key_id")
        reference = _validate_signature_ref(
            signature["ref"], "registry.signature_reference.ref"
        )
        payload_sha256 = _expect_string(
            signature["payload_sha256"],
            "registry.signature_reference.payload_sha256",
            64,
        )
        if not SHA256_RE.fullmatch(payload_sha256):
            raise TrustedPeerPathError(
                "registry.signature_reference.payload_sha256 is invalid"
            )
        if (
            algorithm != trust.signature_algorithm
            or key_id != trust.key_id
            or reference != trust.signature_reference
        ):
            raise TrustedPeerPathError("registry signature reference is not pinned")
        unsigned = dict(registry)
        del unsigned["signature_reference"]
        if _sha256(_canonical_json(unsigned)) != payload_sha256:
            raise TrustedPeerPathError("registry payload digest does not match")

        endpoints_raw = registry["endpoints"]
        if not isinstance(endpoints_raw, list) or not endpoints_raw:
            raise TrustedPeerPathError("registry.endpoints must be non-empty")
        endpoints: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(endpoints_raw):
            endpoint = _validate_endpoint(item, f"registry.endpoints[{index}]")
            endpoint_id = endpoint["endpoint_id"]
            if endpoint_id in endpoints:
                raise TrustedPeerPathError("registry has duplicate endpoint IDs")
            expected_pin = trust.known_host_pins.get(endpoint_id)
            if expected_pin is None:
                raise TrustedPeerPathError(
                    f"known-host pin missing for endpoint: {endpoint_id}"
                )
            if endpoint["known_host_pin"] != expected_pin:
                raise TrustedPeerPathError(
                    f"known-host pin mismatch for endpoint: {endpoint_id}"
                )
            if endpoint["network_label"] not in trust.allowed_network_labels:
                raise TrustedPeerPathError(
                    f"network label is not locally allowed: {endpoint['network_label']}"
                )
            endpoints[endpoint_id] = endpoint
        if set(trust.known_host_pins) != set(endpoints):
            raise TrustedPeerPathError(
                "known-host pin set does not exactly match registry endpoints"
            )

        paths_raw = registry["paths"]
        if not isinstance(paths_raw, list):
            raise TrustedPeerPathError("registry.paths must be an array")
        paths: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(paths_raw):
            entry = _validate_path(item, set(endpoints), f"registry.paths[{index}]")
            if entry["path_id"] in paths:
                raise TrustedPeerPathError("registry has duplicate path IDs")
            if entry["remote_path"] not in trust.allowed_remote_paths:
                raise TrustedPeerPathError(
                    f"path is outside the concrete local allowlist: {entry['path_id']}"
                )
            paths[entry["path_id"]] = entry

        return {
            "schema": REGISTRY_SCHEMA,
            "host_id": registry_host,
            "revision": revision,
            "published_at": registry["published_at"],
            "expires_at": registry["expires_at"],
            "endpoints": [endpoints[key] for key in sorted(endpoints)],
            "paths": [paths[key] for key in sorted(paths)],
            "signature_reference": {
                "algorithm": algorithm,
                "key_id": key_id,
                "ref": reference,
                "payload_sha256": payload_sha256,
                "reference_pinned": True,
                "cryptographic_signature_verified": False,
            },
            "registry_sha256": _sha256(_canonical_json(registry)),
            "network_contacted": False,
            "referenced_files_read": False,
        }

    def list_paths(self, host_id: str | None = None) -> dict[str, Any]:
        host_ids = [host_id] if host_id is not None else sorted(self.trusted_hosts)
        visible: list[dict[str, Any]] = []
        for candidate in host_ids:
            registry = self.validate(candidate)
            endpoints = {
                endpoint["endpoint_id"]: endpoint for endpoint in registry["endpoints"]
            }
            for entry in registry["paths"]:
                if self.local_peer_id not in entry["allowed_peer_ids"]:
                    continue
                visible.append(
                    {
                        "host_id": registry["host_id"],
                        "revision": registry["revision"],
                        **entry,
                        "endpoint": endpoints[entry["endpoint_id"]],
                    }
                )
        return {
            "schema": "system-gap.trusted-peer-paths.list.v2",
            "peer_id": self.local_peer_id,
            "count": len(visible),
            "paths": visible,
            "network_contacted": False,
            "referenced_files_read": False,
        }

    def resolve(self, host_id: str, path_id: str) -> dict[str, Any]:
        wanted = _safe_id(path_id, "path_id")
        registry = self.validate(host_id)
        for entry in registry["paths"]:
            if entry["path_id"] != wanted:
                continue
            if self.local_peer_id not in entry["allowed_peer_ids"]:
                raise TrustedPeerPathError("local peer is not allowed for this path")
            endpoint = next(
                endpoint
                for endpoint in registry["endpoints"]
                if endpoint["endpoint_id"] == entry["endpoint_id"]
            )
            return {
                "host_id": registry["host_id"],
                "peer_id": self.local_peer_id,
                "path": entry,
                "endpoint": endpoint,
                "signature_reference": registry["signature_reference"],
                "registry_sha256": registry["registry_sha256"],
                "revision": registry["revision"],
                "published_at": registry["published_at"],
                "expires_at": registry["expires_at"],
                "network_contacted": False,
                "referenced_files_read": False,
            }
        raise TrustedPeerPathError(f"path is not published: {wanted}")

    def _destination(self, raw: str | Path) -> Path:
        destination = _lexical_absolute(raw, "destination")
        _assert_no_reparse_components(destination)
        if destination.exists():
            raise TrustedPeerPathError(
                "destination already exists; overwrite is forbidden"
            )
        if not destination.parent.is_dir():
            raise TrustedPeerPathError(
                "destination parent must already exist for preparation"
            )
        if _overlap(destination, self.yard_root):
            raise TrustedPeerPathError("destination must be outside the sync yard")
        if not any(_within(destination, root) for root in self.destination_roots):
            raise TrustedPeerPathError(
                "destination is outside configured pull_destination_roots"
            )
        return destination

    def pull_plan(
        self,
        host_id: str,
        path_id: str,
        destination: str | Path,
    ) -> PullPlan:
        resolved = self.resolve(host_id, path_id)
        entry = resolved["path"]
        if entry["direct_pull"] is not True:
            raise TrustedPeerPathError("direct_pull=false blocks pull preparation")
        if entry["kind"] != "file":
            raise TrustedPeerPathError("only ordinary files can be prepared")
        destination_path = self._destination(destination)
        endpoint = resolved["endpoint"]
        core: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA,
            "status": "prepared-no-transfer",
            "executable": False,
            "network_contacted": False,
            "file_transfer_performed": False,
            "referenced_files_read": False,
            "registry": {
                "host_id": resolved["host_id"],
                "revision": resolved["revision"],
                "published_at": resolved["published_at"],
                "expires_at": resolved["expires_at"],
                "sha256": resolved["registry_sha256"],
                "signature_reference": resolved["signature_reference"],
            },
            "peer_id": self.local_peer_id,
            "source": {
                "path_id": entry["path_id"],
                "kind": entry["kind"],
                "metadata_type": entry["metadata_type"],
                "content_included": entry["content_included"],
                "remote_path": entry["remote_path"],
            },
            "endpoint": {
                "endpoint_id": endpoint["endpoint_id"],
                "transport": "sftp",
                "network_label": endpoint["network_label"],
                "host": endpoint["host"],
                "port": endpoint["port"],
                "username": endpoint["username"],
                "known_host_pin": endpoint["known_host_pin"],
            },
            "destination": str(destination_path),
            "constraints": {
                "read_only_remote_account_required": True,
                "single_file_get_only": True,
                "strict_host_key_checking_required": True,
                "no_overwrite": True,
                "shell_command_emitted": False,
                "credentials": "external-not-read",
                "provider_selected": False,
            },
            "remaining_activation_gates": [
                "verify detached signature through a separately reviewed verifier",
                "install the pinned host key in the chosen SSH client",
                "verify server-side read-only account and exact-path ACL",
                "authorize and test the selected direct or private-overlay route",
                "review a separate no-overwrite transfer executor",
                "verify destination ownership and permissions immediately before transfer",
            ],
        }
        plan_id = "sha256:" + _sha256(_canonical_json(core))
        return PullPlan({"plan_id": plan_id, **core})

    def publish(self, *_args: Any, **_kwargs: Any) -> None:
        raise TrustedPeerPathError(
            "registry publishing is intentionally unavailable in read-only preparation"
        )

    def pull(self, *_args: Any, **_kwargs: Any) -> None:
        raise TrustedPeerPathError(
            "live transfer is intentionally unavailable; use pull_plan"
        )


def _dump(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trusted-peer-paths",
        description="Validate trusted-peer metadata and prepare no-transfer receipts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "list"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", required=True)
        child.add_argument("--host-id", required=command == "validate")
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--config", required=True)
    resolve.add_argument("--host-id", required=True)
    resolve.add_argument("--path-id", required=True)
    plan = subparsers.add_parser("pull-plan")
    plan.add_argument("--config", required=True)
    plan.add_argument("--host-id", required=True)
    plan.add_argument("--path-id", required=True)
    plan.add_argument("--destination", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        registry = TrustedPeerPathRegistry.from_file(args.config)
        if args.command == "validate":
            result = registry.validate(args.host_id)
        elif args.command == "list":
            result = registry.list_paths(args.host_id)
        elif args.command == "resolve":
            result = registry.resolve(args.host_id, args.path_id)
        else:
            result = registry.pull_plan(
                args.host_id, args.path_id, args.destination
            ).as_dict()
        _dump(result)
        return 0
    except (TrustedPeerPathError, OSError, ValueError) as exc:
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
