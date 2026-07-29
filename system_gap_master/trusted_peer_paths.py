"""Signed, host-owned path registries for direct trusted-peer pulls.

The shared yard is treated as semi-trusted.  Registry documents contain exact
paths and connection metadata, but never file content or key material.  Each
document is HMAC-authenticated with a host-local key reference.  Consumers
verify the signature, host slot, peer authorization and monotonic revision
before resolving a path or producing an SFTP pull plan.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

if os.name == "nt":
    import msvcrt
else:
    import fcntl


LOCAL_CONFIG_SCHEMA = "system-gap.trusted-peer-paths.local-config.v1"
ENTRIES_SCHEMA = "system-gap.trusted-peer-paths.entries.v1"
REGISTRY_SCHEMA = "system-gap.trusted-peer-paths.registry.v1"
ERROR_SCHEMA = "system-gap.trusted-peer-paths.error.v1"
STATE_SCHEMA = "system-gap.trusted-peer-paths.validation-state.v1"
SIGNATURE_ALGORITHM = "hmac-sha256"

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
DNS_RE = re.compile(
    r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
)
SHA256_RE = re.compile(r"[a-f0-9]{64}")
SFTP_SAFE_REMOTE_RE = re.compile(r"/[A-Za-z0-9._/@+,=:() -]{1,4095}")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
WINDOWS_REPARSE_ATTRIBUTE = 0x400
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_KEY_BYTES = 4096
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", "-wal", "-shm")


class TrustedPeerPathError(RuntimeError):
    """Expected fail-closed registry or pull error."""


@dataclasses.dataclass(frozen=True)
class TrustedHost:
    host_id: str
    key_id: str
    verification_key_ref: Path
    min_revision: int


@dataclasses.dataclass(frozen=True)
class PullPlan:
    host_id: str
    path_id: str
    kind: str
    local_path: str
    remote_path: str
    destination: str
    endpoint_id: str
    argv: tuple[str, ...]
    batch_commands: tuple[str, ...]
    executable: bool
    blocker: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "system-gap.trusted-peer-paths.pull-plan.v1",
            "status": "ready" if self.executable else "blocked",
            "host_id": self.host_id,
            "path_id": self.path_id,
            "kind": self.kind,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
            "destination": self.destination,
            "endpoint_id": self.endpoint_id,
            "transport": "sftp",
            "argv": list(self.argv),
            "batch_commands": list(self.batch_commands),
            "executable": self.executable,
            "blocker": self.blocker,
            "safety": {
                "shell": False,
                "strict_host_key_checking": True,
                "batch_mode": True,
                "no_overwrite": True,
            },
        }


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrustedPeerPathError(f"{label} must be a JSON object")
    return value


def _expect_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise TrustedPeerPathError(
            f"{label} is missing required keys: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise TrustedPeerPathError(
            f"{label} contains unknown keys: {', '.join(sorted(unknown))}"
        )


def _safe_id(value: Any, label: str) -> str:
    result = str(value or "")
    if not ID_RE.fullmatch(result):
        raise TrustedPeerPathError(f"{label} must be a path-neutral identifier")
    return result


def _safe_text(value: Any, label: str, maximum: int = 512) -> str:
    result = str(value or "")
    if not result or len(result) > maximum or CONTROL_RE.search(result):
        raise TrustedPeerPathError(f"{label} contains invalid text")
    return result


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & WINDOWS_REPARSE_ATTRIBUTE)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_absolute(raw: str | Path, label: str) -> Path:
    raw_text = os.fspath(raw)
    if (
        not isinstance(raw_text, str)
        or not raw_text
        or len(raw_text) > 4096
        or CONTROL_RE.search(raw_text)
    ):
        raise TrustedPeerPathError(f"{label} contains invalid path text")
    expanded = Path(raw_text).expanduser()
    if not expanded.is_absolute():
        raise TrustedPeerPathError(f"{label} must be absolute")
    # Do not compare this spelling with Path.resolve(): Windows 8.3 aliases
    # (for example RUNNER~1 in CI) legitimately resolve to a different string.
    # Every existing component is checked with lstat/reparse attributes by the
    # caller before reads, directory creation, writes, or process execution.
    return Path(os.path.abspath(expanded))


def _assert_plain_existing(path: Path, *, directory: bool | None = None) -> None:
    if not path.exists():
        raise TrustedPeerPathError(f"path does not exist: {path}")
    anchor = Path(path.anchor)
    current = anchor
    for part in path.parts[1:]:
        current = current / part
        if _is_link_or_reparse(current):
            raise TrustedPeerPathError(
                f"symlink, junction or reparse path is not allowed: {current}"
            )
    if directory is True and not path.is_dir():
        raise TrustedPeerPathError(f"directory required: {path}")
    if directory is False and not path.is_file():
        raise TrustedPeerPathError(f"regular file required: {path}")


def _mkdir_plain(root: Path, target: Path) -> None:
    if not _is_relative_to(target, root):
        raise TrustedPeerPathError("directory creation escaped its trusted root")
    _assert_plain_existing(root, directory=True)
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        if os.path.lexists(current):
            _assert_plain_existing(current, directory=True)
            continue
        os.mkdir(current, 0o700)
        _assert_plain_existing(current, directory=True)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    _assert_plain_existing(path, directory=False)
    if path.stat().st_size > MAX_REGISTRY_BYTES:
        raise TrustedPeerPathError(f"{label} exceeds {MAX_REGISTRY_BYTES} bytes")
    try:
        return _expect_object(json.loads(path.read_text(encoding="utf-8")), label)
    except UnicodeDecodeError as exc:
        raise TrustedPeerPathError(f"{label} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise TrustedPeerPathError(f"{label} is invalid JSON: {exc}") from exc


def _atomic_write(path: Path, payload: bytes) -> None:
    _assert_plain_existing(path.parent, directory=True)
    if os.path.lexists(path) and _is_link_or_reparse(path):
        raise TrustedPeerPathError(f"refusing to replace link or reparse path: {path}")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        with path.open("rb") as handle:
            if _sha256_bytes(handle.read()) != _sha256_bytes(payload):
                raise TrustedPeerPathError("atomic write readback failed")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_key(path: Path, yard_root: Path) -> bytes:
    if _is_relative_to(path, yard_root) or _is_relative_to(yard_root, path):
        raise TrustedPeerPathError("key references must not overlap the synced yard")
    _assert_plain_existing(path, directory=False)
    size = path.stat().st_size
    if size < 32 or size > MAX_KEY_BYTES:
        raise TrustedPeerPathError("verification/signing key file must contain 32-4096 bytes")
    key = path.read_bytes().rstrip(b"\r\n")
    if len(key) < 32:
        raise TrustedPeerPathError("verification/signing key must be at least 32 bytes")
    return key


def _sign_registry(payload: Mapping[str, Any], key: bytes) -> str:
    unsigned = copy.deepcopy(dict(payload))
    unsigned.pop("signature", None)
    return hmac.new(key, _canonical_json(unsigned), hashlib.sha256).hexdigest()


def _validate_network_host(value: Any) -> str:
    host = str(value or "")
    if CONTROL_RE.search(host) or host.startswith("-"):
        raise TrustedPeerPathError("endpoint host is invalid")
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        if not DNS_RE.fullmatch(host) or ".." in host:
            raise TrustedPeerPathError("endpoint host must be a DNS name or IP address")
    return host


def _validate_exact_local_path(value: Any) -> str:
    path = str(value or "")
    if (
        not path
        or len(path) > 4096
        or CONTROL_RE.search(path)
        or ".." in PurePosixPath(path).parts
        or ".." in PureWindowsPath(path).parts
    ):
        raise TrustedPeerPathError("local_path must be an absolute traversal-free path")
    if not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()):
        raise TrustedPeerPathError("local_path must be an absolute POSIX or Windows path")
    return path


def _validate_remote_path(value: Any) -> str:
    path = str(value or "")
    parts = path.split("/")
    if (
        not SFTP_SAFE_REMOTE_RE.fullmatch(path)
        or not PurePosixPath(path).is_absolute()
        or ".." in parts
        or any(character in path for character in "*?[]{}")
    ):
        raise TrustedPeerPathError(
            "remote_path must be an absolute, traversal-free, non-globbing SFTP path "
            "using the conservative portable character set"
        )
    return path


def _looks_like_sqlite_path(path: str) -> bool:
    return path.lower().endswith(SQLITE_SUFFIXES)


def _validate_endpoint(raw: Any) -> dict[str, Any]:
    endpoint = _expect_object(raw, "endpoint")
    _expect_keys(
        endpoint,
        required={"endpoint_id", "transport", "network", "host", "port", "username"},
        optional={"description"},
        label="endpoint",
    )
    endpoint_id = _safe_id(endpoint["endpoint_id"], "endpoint_id")
    transport = str(endpoint["transport"])
    if transport != "sftp":
        raise TrustedPeerPathError("only the sftp transport is supported")
    network = str(endpoint["network"])
    if network not in {"tailscale", "lan"}:
        raise TrustedPeerPathError("endpoint network must be tailscale or lan")
    host = _validate_network_host(endpoint["host"])
    port = endpoint["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise TrustedPeerPathError("endpoint port must be an integer from 1 to 65535")
    username = str(endpoint["username"] or "")
    if not USERNAME_RE.fullmatch(username):
        raise TrustedPeerPathError("endpoint username is invalid")
    result: dict[str, Any] = {
        "endpoint_id": endpoint_id,
        "transport": transport,
        "network": network,
        "host": host,
        "port": port,
        "username": username,
    }
    if "description" in endpoint:
        result["description"] = _safe_text(
            endpoint["description"], "endpoint description", 256
        )
    return result


def _validate_path_entry(raw: Any, endpoint_ids: set[str]) -> dict[str, Any]:
    item = _expect_object(raw, "path entry")
    _expect_keys(
        item,
        required={
            "path_id",
            "kind",
            "local_path",
            "remote_path",
            "endpoint_id",
            "allowed_peer_ids",
            "direct_pull",
        },
        optional={"adapter", "description"},
        label="path entry",
    )
    path_id = _safe_id(item["path_id"], "path_id")
    kind = str(item["kind"])
    if kind not in {"file", "directory", "database/sqlite"}:
        raise TrustedPeerPathError(
            "path kind must be file, directory, or database/sqlite"
        )
    direct_pull = item["direct_pull"]
    if not isinstance(direct_pull, bool):
        raise TrustedPeerPathError(f"path {path_id} direct_pull must be boolean")
    endpoint_id = _safe_id(item["endpoint_id"], "endpoint_id")
    if endpoint_id not in endpoint_ids:
        raise TrustedPeerPathError(f"path {path_id} references an unknown endpoint")
    peers = item["allowed_peer_ids"]
    if not isinstance(peers, list) or not peers:
        raise TrustedPeerPathError(f"path {path_id} must allow at least one peer")
    allowed = [_safe_id(peer, "allowed_peer_id") for peer in peers]
    if len(set(allowed)) != len(allowed):
        raise TrustedPeerPathError(f"path {path_id} contains duplicate peer IDs")
    local_path = _validate_exact_local_path(item["local_path"])
    remote_path = _validate_remote_path(item["remote_path"])
    sqlite_path = _looks_like_sqlite_path(local_path) or _looks_like_sqlite_path(
        remote_path
    )
    adapter = item.get("adapter")
    if kind == "database/sqlite":
        if direct_pull:
            raise TrustedPeerPathError(
                f"path {path_id}: database/sqlite requires direct_pull=false"
            )
        if adapter != "sqlite-transit-sync":
            raise TrustedPeerPathError(
                f"path {path_id}: database/sqlite requires adapter=sqlite-transit-sync"
            )
    elif sqlite_path:
        raise TrustedPeerPathError(
            f"path {path_id}: SQLite, -wal and -shm paths must use "
            "kind=database/sqlite"
        )
    elif adapter is not None:
        adapter = _safe_id(adapter, "adapter")
    result: dict[str, Any] = {
        "path_id": path_id,
        "kind": kind,
        "local_path": local_path,
        "remote_path": remote_path,
        "endpoint_id": endpoint_id,
        "allowed_peer_ids": allowed,
        "direct_pull": direct_pull,
    }
    if adapter is not None:
        result["adapter"] = adapter
    if "description" in item:
        result["description"] = _safe_text(
            item["description"], "path description", 512
        )
    return result


def _validate_registry_shape(
    raw: Mapping[str, Any], expected_host_id: str
) -> dict[str, Any]:
    _expect_keys(
        raw,
        required={
            "schema",
            "host_id",
            "revision",
            "published_at",
            "endpoints",
            "paths",
            "signature",
        },
        optional=set(),
        label="registry",
    )
    if raw["schema"] != REGISTRY_SCHEMA:
        raise TrustedPeerPathError(f"registry schema must be {REGISTRY_SCHEMA}")
    host_id = _safe_id(raw["host_id"], "registry host_id")
    if host_id != expected_host_id:
        raise TrustedPeerPathError("registry host_id does not match its yard slot")
    revision = raw["revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise TrustedPeerPathError("registry revision must be a positive integer")
    published_at = _safe_text(raw["published_at"], "published_at", 64)
    try:
        parsed_time = dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrustedPeerPathError("published_at must be an ISO-8601 timestamp") from exc
    if parsed_time.tzinfo is None:
        raise TrustedPeerPathError("published_at must include a timezone")
    raw_endpoints = raw["endpoints"]
    if not isinstance(raw_endpoints, list) or not raw_endpoints:
        raise TrustedPeerPathError("registry must publish at least one endpoint")
    endpoints = [_validate_endpoint(endpoint) for endpoint in raw_endpoints]
    endpoint_ids = [endpoint["endpoint_id"] for endpoint in endpoints]
    if len(set(endpoint_ids)) != len(endpoint_ids):
        raise TrustedPeerPathError("registry endpoint IDs must be unique")
    raw_paths = raw["paths"]
    if not isinstance(raw_paths, list):
        raise TrustedPeerPathError("registry paths must be an array")
    paths = [_validate_path_entry(item, set(endpoint_ids)) for item in raw_paths]
    path_ids = [item["path_id"] for item in paths]
    if len(set(path_ids)) != len(path_ids):
        raise TrustedPeerPathError("registry path IDs must be unique")
    signature = _expect_object(raw["signature"], "signature")
    _expect_keys(
        signature,
        required={"algorithm", "key_id", "value"},
        optional=set(),
        label="signature",
    )
    if signature["algorithm"] != SIGNATURE_ALGORITHM:
        raise TrustedPeerPathError(
            f"signature algorithm must be {SIGNATURE_ALGORITHM}"
        )
    key_id = _safe_id(signature["key_id"], "signature key_id")
    value = str(signature["value"])
    if not SHA256_RE.fullmatch(value):
        raise TrustedPeerPathError("signature value must be a lowercase SHA-256 HMAC")
    return {
        "schema": REGISTRY_SCHEMA,
        "host_id": host_id,
        "revision": revision,
        "published_at": published_at,
        "endpoints": endpoints,
        "paths": paths,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "key_id": key_id,
            "value": value,
        },
    }


def _sftp_quote(path: str) -> str:
    if CONTROL_RE.search(path):
        raise TrustedPeerPathError("SFTP batch path contains control characters")
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


@contextlib.contextmanager
def _local_guard(state_root: Path, guard_id: str):
    guard_root = state_root / "guards"
    _mkdir_plain(state_root, guard_root)
    guard_path = guard_root / f"{_safe_id(guard_id, 'guard_id')}.lock"
    if os.path.lexists(guard_path):
        _assert_plain_existing(guard_path, directory=False)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(guard_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TrustedPeerPathError("local guard must be a regular file")
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class TrustedPeerPathRegistry:
    """Publish, verify, resolve and pull from signed host-owned registries."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = copy.deepcopy(_expect_object(config, "local config"))
        _expect_keys(
            self.config,
            required={
                "schema",
                "yard_root",
                "local_host_id",
                "local_peer_id",
                "state_dir",
                "trusted_hosts",
                "pull_destination_roots",
                "ssh",
            },
            optional={"publisher"},
            label="local config",
        )
        if self.config["schema"] != LOCAL_CONFIG_SCHEMA:
            raise TrustedPeerPathError(
                f"local config schema must be {LOCAL_CONFIG_SCHEMA}"
            )
        self.yard_root = _lexical_absolute(self.config["yard_root"], "yard_root")
        _assert_plain_existing(self.yard_root, directory=True)
        self.local_host_id = _safe_id(
            self.config["local_host_id"], "local_host_id"
        )
        self.local_peer_id = _safe_id(
            self.config["local_peer_id"], "local_peer_id"
        )
        self.state_dir = _lexical_absolute(self.config["state_dir"], "state_dir")
        if _is_relative_to(self.state_dir, self.yard_root) or _is_relative_to(
            self.yard_root, self.state_dir
        ):
            raise TrustedPeerPathError("state_dir must not overlap the synced yard")
        if self.state_dir.exists():
            _assert_plain_existing(self.state_dir, directory=True)
        else:
            parent = self.state_dir.parent
            _assert_plain_existing(parent, directory=True)
            os.mkdir(self.state_dir, 0o700)
            _assert_plain_existing(self.state_dir, directory=True)
        self.publisher = self._load_publisher(self.config.get("publisher"))
        self.trusted_hosts = self._load_trusted_hosts(self.config["trusted_hosts"])
        roots = self.config["pull_destination_roots"]
        if not isinstance(roots, list):
            raise TrustedPeerPathError("pull_destination_roots must be an array")
        self.pull_destination_roots = tuple(
            _lexical_absolute(root, "pull destination root") for root in roots
        )
        for root in self.pull_destination_roots:
            _assert_plain_existing(root, directory=True)
            if _is_relative_to(root, self.yard_root) or _is_relative_to(
                self.yard_root, root
            ):
                raise TrustedPeerPathError(
                    "pull destination roots must not overlap the synced yard"
                )
        self.ssh = self._load_ssh(self.config["ssh"])

    @classmethod
    def from_file(cls, path: str | Path) -> "TrustedPeerPathRegistry":
        config_path = _lexical_absolute(path, "config path")
        return cls(_read_json(config_path, "local config"))

    def _load_publisher(self, raw: Any) -> dict[str, Any] | None:
        if raw is None:
            return None
        publisher = _expect_object(raw, "publisher")
        _expect_keys(
            publisher,
            required={"key_id", "signing_key_ref", "endpoints"},
            optional=set(),
            label="publisher",
        )
        key_id = _safe_id(publisher["key_id"], "publisher key_id")
        key_ref = _lexical_absolute(
            publisher["signing_key_ref"], "signing_key_ref"
        )
        endpoints_raw = publisher["endpoints"]
        if not isinstance(endpoints_raw, list) or not endpoints_raw:
            raise TrustedPeerPathError("publisher must define at least one endpoint")
        endpoints = [_validate_endpoint(endpoint) for endpoint in endpoints_raw]
        endpoint_ids = [endpoint["endpoint_id"] for endpoint in endpoints]
        if len(set(endpoint_ids)) != len(endpoint_ids):
            raise TrustedPeerPathError("publisher endpoint IDs must be unique")
        return {"key_id": key_id, "key_ref": key_ref, "endpoints": endpoints}

    def _load_trusted_hosts(self, raw: Any) -> dict[str, TrustedHost]:
        if not isinstance(raw, list):
            raise TrustedPeerPathError("trusted_hosts must be an array")
        result: dict[str, TrustedHost] = {}
        for item_raw in raw:
            item = _expect_object(item_raw, "trusted host")
            _expect_keys(
                item,
                required={
                    "host_id",
                    "key_id",
                    "verification_key_ref",
                    "min_revision",
                },
                optional=set(),
                label="trusted host",
            )
            host_id = _safe_id(item["host_id"], "trusted host_id")
            if host_id in result:
                raise TrustedPeerPathError(f"duplicate trusted host: {host_id}")
            min_revision = item["min_revision"]
            if (
                not isinstance(min_revision, int)
                or isinstance(min_revision, bool)
                or min_revision < 1
            ):
                raise TrustedPeerPathError("trusted host min_revision must be positive")
            result[host_id] = TrustedHost(
                host_id=host_id,
                key_id=_safe_id(item["key_id"], "trusted host key_id"),
                verification_key_ref=_lexical_absolute(
                    item["verification_key_ref"], "verification_key_ref"
                ),
                min_revision=min_revision,
            )
        return result

    def _load_ssh(self, raw: Any) -> dict[str, Any]:
        ssh = _expect_object(raw, "ssh")
        _expect_keys(
            ssh,
            required={
                "known_hosts_ref",
                "sftp_executable_ref",
                "connect_timeout_seconds",
            },
            optional=set(),
            label="ssh",
        )
        known_hosts_ref = _lexical_absolute(
            ssh["known_hosts_ref"], "known_hosts_ref"
        )
        if _is_relative_to(known_hosts_ref, self.yard_root):
            raise TrustedPeerPathError("known_hosts_ref must be host-local")
        sftp_executable_ref = _lexical_absolute(
            ssh["sftp_executable_ref"], "sftp_executable_ref"
        )
        if _is_relative_to(sftp_executable_ref, self.yard_root):
            raise TrustedPeerPathError("sftp_executable_ref must be host-local")
        _assert_plain_existing(sftp_executable_ref, directory=False)
        timeout = ssh["connect_timeout_seconds"]
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or not 1 <= timeout <= 120
        ):
            raise TrustedPeerPathError(
                "connect_timeout_seconds must be an integer from 1 to 120"
            )
        return {
            "known_hosts_ref": known_hosts_ref,
            "sftp_executable_ref": sftp_executable_ref,
            "connect_timeout_seconds": timeout,
        }

    def _registry_path(self, host_id: str, *, create: bool = False) -> Path:
        safe_host = _safe_id(host_id, "host_id")
        hosts_root = self.yard_root / "hosts"
        if create:
            _mkdir_plain(self.yard_root, hosts_root)
            host_root = hosts_root / safe_host
            _mkdir_plain(self.yard_root, host_root)
            registry_root = host_root / "trusted-peer-paths"
            _mkdir_plain(self.yard_root, registry_root)
        else:
            _assert_plain_existing(hosts_root, directory=True)
            host_root = hosts_root / safe_host
            _assert_plain_existing(host_root, directory=True)
            registry_root = host_root / "trusted-peer-paths"
            _assert_plain_existing(registry_root, directory=True)
        return registry_root / "registry.json"

    def _trust_for(self, host_id: str) -> TrustedHost:
        if host_id == self.local_host_id and self.publisher is not None:
            return TrustedHost(
                host_id=host_id,
                key_id=self.publisher["key_id"],
                verification_key_ref=self.publisher["key_ref"],
                min_revision=1,
            )
        try:
            return self.trusted_hosts[host_id]
        except KeyError as exc:
            raise TrustedPeerPathError(f"host is not trusted: {host_id}") from exc

    def _state_path(self, host_id: str) -> Path:
        root = self.state_dir / "trusted-peer-paths"
        _mkdir_plain(self.state_dir, root)
        return root / f"{_safe_id(host_id, 'host_id')}.json"

    def _record_revision(self, registry: Mapping[str, Any]) -> None:
        host_id = str(registry["host_id"])
        revision = int(registry["revision"])
        digest = _sha256_bytes(_canonical_json(registry))
        guard_id = f"validate-{_sha256_bytes(host_id.encode('utf-8'))[:32]}"
        with _local_guard(self.state_dir, guard_id):
            path = self._state_path(host_id)
            if path.exists():
                state = _read_json(path, "validation state")
                _expect_keys(
                    state,
                    required={
                        "schema",
                        "host_id",
                        "revision",
                        "registry_sha256",
                    },
                    optional=set(),
                    label="validation state",
                )
                if state["schema"] != STATE_SCHEMA or state["host_id"] != host_id:
                    raise TrustedPeerPathError("validation state identity mismatch")
                seen = state["revision"]
                if not isinstance(seen, int) or isinstance(seen, bool):
                    raise TrustedPeerPathError(
                        "validation state revision is invalid"
                    )
                if revision < seen:
                    raise TrustedPeerPathError(
                        f"registry replay detected for {host_id}: {revision} < {seen}"
                    )
                if revision == seen and state["registry_sha256"] != digest:
                    raise TrustedPeerPathError(
                        f"registry equivocation detected for {host_id} "
                        f"revision {revision}"
                    )
                if revision == seen:
                    return
            payload = {
                "schema": STATE_SCHEMA,
                "host_id": host_id,
                "revision": revision,
                "registry_sha256": digest,
            }
            encoded = (
                json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
                + b"\n"
            )
            _atomic_write(path, encoded)

    def publish(self, entries: Mapping[str, Any]) -> dict[str, Any]:
        if self.publisher is None:
            raise TrustedPeerPathError("publisher configuration is required")
        source = _expect_object(entries, "entries")
        _expect_keys(
            source,
            required={"schema", "revision", "paths"},
            optional=set(),
            label="entries",
        )
        if source["schema"] != ENTRIES_SCHEMA:
            raise TrustedPeerPathError(f"entries schema must be {ENTRIES_SCHEMA}")
        revision = source["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise TrustedPeerPathError("entries revision must be a positive integer")
        endpoint_ids = {
            endpoint["endpoint_id"] for endpoint in self.publisher["endpoints"]
        }
        raw_paths = source["paths"]
        if not isinstance(raw_paths, list):
            raise TrustedPeerPathError("entries paths must be an array")
        paths = [_validate_path_entry(item, endpoint_ids) for item in raw_paths]
        path_ids = [item["path_id"] for item in paths]
        if len(set(path_ids)) != len(path_ids):
            raise TrustedPeerPathError("entries path IDs must be unique")
        guard_id = (
            f"publish-{_sha256_bytes(self.local_host_id.encode('utf-8'))[:32]}"
        )
        with _local_guard(self.state_dir, guard_id):
            output_path = self._registry_path(self.local_host_id, create=True)
            if output_path.exists():
                existing = self.validate(self.local_host_id, record=False)
                if revision <= existing["revision"]:
                    raise TrustedPeerPathError(
                        "publish revision must be greater than the existing "
                        "signed registry"
                    )
            payload: dict[str, Any] = {
                "schema": REGISTRY_SCHEMA,
                "host_id": self.local_host_id,
                "revision": revision,
                "published_at": _utc_now(),
                "endpoints": copy.deepcopy(self.publisher["endpoints"]),
                "paths": paths,
            }
            key = _read_key(self.publisher["key_ref"], self.yard_root)
            payload["signature"] = {
                "algorithm": SIGNATURE_ALGORITHM,
                "key_id": self.publisher["key_id"],
                "value": _sign_registry(payload, key),
            }
            normalized = _validate_registry_shape(payload, self.local_host_id)
            encoded = (
                json.dumps(normalized, indent=2, sort_keys=True).encode("utf-8")
                + b"\n"
            )
            _atomic_write(output_path, encoded)
            verified = self.validate(self.local_host_id)
        return {
            "schema": "system-gap.trusted-peer-paths.publish-result.v1",
            "status": "published",
            "host_id": self.local_host_id,
            "revision": verified["revision"],
            "registry": str(output_path),
            "path_count": len(verified["paths"]),
        }

    def validate(self, host_id: str, *, record: bool = True) -> dict[str, Any]:
        safe_host = _safe_id(host_id, "host_id")
        trust = self._trust_for(safe_host)
        path = self._registry_path(safe_host)
        registry = _validate_registry_shape(
            _read_json(path, "trusted-peer registry"), safe_host
        )
        if registry["signature"]["key_id"] != trust.key_id:
            raise TrustedPeerPathError("registry key_id does not match the trust pin")
        if registry["revision"] < trust.min_revision:
            raise TrustedPeerPathError(
                f"registry revision is below pinned minimum {trust.min_revision}"
            )
        key = _read_key(trust.verification_key_ref, self.yard_root)
        expected = _sign_registry(registry, key)
        if not hmac.compare_digest(registry["signature"]["value"], expected):
            raise TrustedPeerPathError("registry signature verification failed")
        if record:
            self._record_revision(registry)
        return registry

    def list_paths(self, host_id: str | None = None) -> dict[str, Any]:
        host_ids = (
            [host_id]
            if host_id is not None
            else sorted(set(self.trusted_hosts) | ({self.local_host_id} if self.publisher else set()))
        )
        visible: list[dict[str, Any]] = []
        for candidate in host_ids:
            registry = self.validate(str(candidate))
            endpoints = {
                endpoint["endpoint_id"]: endpoint for endpoint in registry["endpoints"]
            }
            for item in registry["paths"]:
                if self.local_peer_id not in item["allowed_peer_ids"]:
                    continue
                visible.append(
                    {
                        "host_id": registry["host_id"],
                        "revision": registry["revision"],
                        **copy.deepcopy(item),
                        "endpoint": copy.deepcopy(endpoints[item["endpoint_id"]]),
                    }
                )
        return {
            "schema": "system-gap.trusted-peer-paths.list.v1",
            "peer_id": self.local_peer_id,
            "count": len(visible),
            "paths": visible,
        }

    def resolve(self, host_id: str, path_id: str) -> dict[str, Any]:
        registry = self.validate(host_id)
        safe_path_id = _safe_id(path_id, "path_id")
        for item in registry["paths"]:
            if item["path_id"] != safe_path_id:
                continue
            if self.local_peer_id not in item["allowed_peer_ids"]:
                raise TrustedPeerPathError(
                    f"peer {self.local_peer_id} is not authorized for {safe_path_id}"
                )
            endpoint = next(
                endpoint
                for endpoint in registry["endpoints"]
                if endpoint["endpoint_id"] == item["endpoint_id"]
            )
            return {
                "schema": "system-gap.trusted-peer-paths.resolution.v1",
                "host_id": registry["host_id"],
                "revision": registry["revision"],
                "peer_id": self.local_peer_id,
                "path": copy.deepcopy(item),
                "endpoint": copy.deepcopy(endpoint),
                "verified": True,
            }
        raise TrustedPeerPathError(f"unknown path_id for {host_id}: {safe_path_id}")

    def _destination(self, raw: str | Path) -> Path:
        destination = _lexical_absolute(raw, "destination")
        if not self.pull_destination_roots:
            raise TrustedPeerPathError("no pull_destination_roots are configured")
        if not any(
            _is_relative_to(destination, root) for root in self.pull_destination_roots
        ):
            raise TrustedPeerPathError("destination is outside configured pull roots")
        _assert_plain_existing(destination.parent, directory=True)
        if os.path.lexists(destination):
            raise TrustedPeerPathError(
                "destination already exists; pulls never overwrite by default"
            )
        if CONTROL_RE.search(destination.as_posix()):
            raise TrustedPeerPathError("destination contains control characters")
        return destination

    def pull_plan(
        self, host_id: str, path_id: str, destination: str | Path
    ) -> PullPlan:
        resolution = self.resolve(host_id, path_id)
        item = resolution["path"]
        endpoint = resolution["endpoint"]
        known_hosts = self.ssh["known_hosts_ref"]
        _assert_plain_existing(known_hosts, directory=False)
        destination_path = self._destination(destination)
        sftp = self.ssh["sftp_executable_ref"]
        _assert_plain_existing(sftp, directory=False)
        executable = item["kind"] == "file" and item["direct_pull"]
        blocker = None
        if item["kind"] == "database/sqlite" or _looks_like_sqlite_path(
            item["remote_path"]
        ):
            blocker = (
                "R9: SQLite and -wal/-shm paths require sqlite-transit-sync "
                "through db-transit/<namespace>; direct pull is forbidden"
            )
        elif not item["direct_pull"]:
            blocker = "publisher-disabled-direct-pull"
        elif item["kind"] != "file":
            blocker = "directory-pull-requires-reviewed-adapter"
        target = f"{endpoint['username']}@{endpoint['host']}"
        argv = (
            str(sftp),
            "-q",
            "-b",
            "<HOST_LOCAL_BATCH_FILE>",
            "-F",
            "none",
            "-P",
            str(endpoint["port"]),
            "-oBatchMode=yes",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile={known_hosts}",
            "-oGlobalKnownHostsFile=none",
            "-oClearAllForwardings=yes",
            f"-oConnectTimeout={self.ssh['connect_timeout_seconds']}",
            target,
        )
        batch_commands = (
            f"get {_sftp_quote(item['remote_path'])} "
            f"{_sftp_quote('<HOST_LOCAL_TEMP_FILE>')}",
            "bye",
        )
        if not executable:
            argv = ()
            batch_commands = ()
        return PullPlan(
            host_id=resolution["host_id"],
            path_id=item["path_id"],
            kind=item["kind"],
            local_path=item["local_path"],
            remote_path=item["remote_path"],
            destination=str(destination_path),
            endpoint_id=endpoint["endpoint_id"],
            argv=argv,
            batch_commands=batch_commands,
            executable=executable,
            blocker=blocker,
        )

    def pull(
        self,
        host_id: str,
        path_id: str,
        destination: str | Path,
        *,
        apply: bool = False,
    ) -> dict[str, Any]:
        plan = self.pull_plan(host_id, path_id, destination)
        if not apply:
            result = plan.as_dict()
            result["status"] = "dry-run"
            return result
        if not plan.executable:
            raise TrustedPeerPathError(
                f"pull cannot execute safely: {plan.blocker or 'unknown blocker'}"
            )
        destination_path = Path(plan.destination)
        self._destination(destination_path)
        endpoint = self.resolve(host_id, path_id)["endpoint"]
        with tempfile.TemporaryDirectory(
            prefix=".trusted-peer-pull-", dir=destination_path.parent
        ) as temporary_dir:
            temporary_root = Path(temporary_dir)
            _assert_plain_existing(temporary_root, directory=True)
            part = temporary_root / "download.part"
            batch = temporary_root / "pull.batch"
            batch_payload = (
                f"get {_sftp_quote(plan.remote_path)} {_sftp_quote(part.as_posix())}\n"
                "bye\n"
            ).encode("utf-8")
            _atomic_write(batch, batch_payload)
            argv = [
                str(plan.argv[0]),
                "-q",
                "-b",
                str(batch),
                "-F",
                "none",
                "-P",
                str(endpoint["port"]),
                "-oBatchMode=yes",
                "-oStrictHostKeyChecking=yes",
                f"-oUserKnownHostsFile={self.ssh['known_hosts_ref']}",
                "-oGlobalKnownHostsFile=none",
                "-oClearAllForwardings=yes",
                f"-oConnectTimeout={self.ssh['connect_timeout_seconds']}",
                f"{endpoint['username']}@{endpoint['host']}",
            ]
            try:
                completed = subprocess.run(
                    argv,
                    shell=False,
                    check=False,
                    capture_output=True,
                    timeout=self.ssh["connect_timeout_seconds"] + 30,
                )
            except subprocess.TimeoutExpired as exc:
                raise TrustedPeerPathError("SFTP pull timed out") from exc
            if completed.returncode != 0:
                raise TrustedPeerPathError(
                    f"SFTP pull failed with exit code {completed.returncode}"
                )
            _assert_plain_existing(part, directory=False)
            size = part.stat().st_size
            self._install_no_overwrite(part, destination_path)
        return {
            "schema": "system-gap.trusted-peer-paths.pull-result.v1",
            "status": "pulled",
            "host_id": plan.host_id,
            "path_id": plan.path_id,
            "destination": plan.destination,
            "bytes": size,
            "transport": "sftp",
            "overwritten": False,
        }

    def _install_no_overwrite(self, source: Path, destination: Path) -> None:
        self._destination(destination)
        try:
            os.link(source, destination)
        except OSError:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as output:
                    with source.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
                raise
        _assert_plain_existing(destination, directory=False)
        if source.stat().st_size != destination.stat().st_size:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            raise TrustedPeerPathError("download install size verification failed")


def _dump(value: Mapping[str, Any], output: str | None) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        output_path = _lexical_absolute(output, "output path")
        _atomic_write(output_path, payload.encode("utf-8"))
    else:
        print(payload, end="")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--config", required=True)
    publish_parser.add_argument("--entries", required=True)
    publish_parser.add_argument("--output")
    for command in ("validate", "list"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", required=True)
        child.add_argument("--host-id")
        child.add_argument("--output")
    for command in ("resolve", "pull-plan", "pull"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", required=True)
        child.add_argument("--host-id", required=True)
        child.add_argument("--path-id", required=True)
        if command in {"pull-plan", "pull"}:
            child.add_argument("--destination", required=True)
        if command == "pull":
            child.add_argument("--apply", action="store_true")
        child.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        registry = TrustedPeerPathRegistry.from_file(args.config)
        if args.command == "publish":
            entries_path = _lexical_absolute(args.entries, "entries path")
            result = registry.publish(_read_json(entries_path, "entries"))
        elif args.command == "validate":
            if not args.host_id:
                raise TrustedPeerPathError("validate requires --host-id")
            validated = registry.validate(args.host_id)
            result = {
                "schema": "system-gap.trusted-peer-paths.validation.v1",
                "status": "valid",
                "host_id": validated["host_id"],
                "revision": validated["revision"],
                "path_count": len(validated["paths"]),
                "signature_verified": True,
            }
        elif args.command == "list":
            result = registry.list_paths(args.host_id)
        elif args.command == "resolve":
            result = registry.resolve(args.host_id, args.path_id)
        elif args.command == "pull-plan":
            result = registry.pull_plan(
                args.host_id, args.path_id, args.destination
            ).as_dict()
        else:
            result = registry.pull(
                args.host_id,
                args.path_id,
                args.destination,
                apply=args.apply,
            )
        _dump(result, args.output)
        return 0
    except (
        OSError,
        ValueError,
        TrustedPeerPathError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema": ERROR_SCHEMA,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
