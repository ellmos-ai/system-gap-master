"""Fail-closed conflict-copy reconciliation for sync yards.

The reconciler is deliberately policy-driven:

* roots are explicitly allowlisted;
* every mutable candidate needs an authoritative canonical mapping;
* only exact copies, append-only text supersets, non-overlapping three-way
  text merges, and an explicit JSON-object adapter can be automatic;
* leases, fingerprints, local backups, atomic replacement, verification and
  rollback protect the mutation boundary;
* directories literally named ``_archive`` are never walked, and a root may
  declare ``exempt_name_patterns`` (regexes matched against the root-relative
  path) so filenames that are by-design host-suffixed artifacts of the yard's
  own naming convention are never reported as conflict-copy candidates in the
  first place, not merely left unmapped.

Operational manifests remain in a local state directory.  Exported receipts
contain only root identifiers and salted path hashes, never absolute paths or
file content.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import datetime as dt
import difflib
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

if os.name == "nt":
    import msvcrt
else:
    import fcntl

CONFIG_SCHEMA = "system-gap.conflict-reconciler.config.v1"
SCAN_SCHEMA = "system-gap.conflict-reconciler.scan.v1"
PLAN_SCHEMA = "system-gap.conflict-reconciler.plan.v1"
MANIFEST_SCHEMA = "system-gap.conflict-reconciler.operation.v1"
RECEIPT_SCHEMA = "system-gap.conflict-reconciler.receipt.v1"

AUTHORITY_KINDS = {"manifest", "pointer", "registry", "writer-policy"}
SAFE_CLASSES = {"exact", "append-only-text", "three-way-text", "json-object"}
BLOCKED_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bak",
    ".bin",
    ".bmp",
    ".db",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lock",
    ".mov",
    ".mp3",
    ".mp4",
    ".msi",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".shm",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tif",
    ".tiff",
    ".wav",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "authorized_keys",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "known_hosts",
    "secrets.json",
}
SECRET_PATH_PARTS = {
    ".aws",
    ".azure",
    ".gnupg",
    ".kube",
    ".ssh",
    "keychain",
    "keychains",
}
SECRET_SUFFIXES = {
    ".cer",
    ".crt",
    ".der",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
}
SECRET_CONTENT = re.compile(
    rb"(?i)("
    rb"api[_-]?key|access[_-]?token|auth[_-]?token|bearer|client[_-]?secret|"
    rb"password|private[_-]?key|refresh[_-]?token|secret|token"
    rb")\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:-]{8,}|"
    rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|"
    rb"\bAKIA[0-9A-Z]{16}\b"
)
# Machine-regenerable artefacts. A conflict copy of a bytecode cache carries no
# information: the file is rebuilt on the next run, so neither merging nor human
# review is worth anyone's time. Observed 2026-08-01: a review queue of thirteen
# "undecidable" cases turned out to be entirely bytecode and VCS internals --
# noise that trains reviewers to ignore the queue.
REGENERABLE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
REGENERABLE_EXTENSIONS = {".pyc", ".pyo", ".pyd", ".class", ".o", ".obj"}

# Markers that a file legitimately differs per host. Such a pair is not a
# conflict to be merged: either both sides are kept under explicit per-host
# names, or -- better -- the file is made path-neutral so the split disappears.
# Merging them silently destroys one host's configuration.
HOST_SPECIFIC_MARKERS = (
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+"),
    re.compile(r"/Users/[^/\s\"']+"),
    re.compile(r"/home/[^/\s\"']+"),
)


def is_regenerable(path: Path) -> bool:
    """Whether a path is a machine-regenerable build artefact."""
    if path.suffix.lower() in REGENERABLE_EXTENSIONS:
        return True
    return any(part in REGENERABLE_DIR_NAMES for part in path.parts)


def host_specific_markers(text: str, known_hosts: Sequence[str] = ()) -> list[str]:
    """Report evidence that a file's content is host-specific.

    Returns short human-readable reasons, empty if none found. Callers should
    treat a non-empty result as a reason NOT to merge automatically: the two
    sides may both be correct, each for its own machine.
    """
    reasons: list[str] = []
    for host in known_hosts:
        if host and host in text:
            reasons.append(f"host name {host!r} in content")
            break
    for pattern in HOST_SPECIFIC_MARKERS:
        match = pattern.search(text)
        if match:
            reasons.append(f"absolute user path {match.group(0)!r}")
            break
    return reasons


def excerpt(text: str, edge: int = 2, width: int = 160) -> list[str]:
    """Beginning, middle and end of a text -- what is this file about?

    Before comparing two versions line by line, a reviewer needs the cheaper
    answer first: is merging this worth doing at all? A handful of opening
    sentences, one from the middle and the closing ones answer that in seconds,
    which matters when a single run surfaces dozens of candidates.
    """
    segments = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}|\n", text) if s.strip()]
    if not segments:
        return ["(empty)"]

    def clip(value: str) -> str:
        value = " ".join(value.split())
        return value if len(value) <= width else value[: width - 1] + "…"

    if len(segments) <= edge * 2 + 1:
        return [clip(s) for s in segments]
    lines = [f"START:  {clip(s)}" for s in segments[:edge]]
    lines.append(f"MIDDLE: {clip(segments[len(segments) // 2])}")
    lines += [f"END:    {clip(s)}" for s in segments[-edge:]]
    return lines


# Fields that mark a JSON document as claiming a specific kind/version. A
# filename pattern (e.g. a shared "-HOST" suffix) is not evidence that two
# files are the same document -- ticket T-20260729-04 found a real pair,
# components.json (schema_version "public-catalog-v1") and
# components-<HOST>.json (schema_version "skill-v1"), that are independent
# generators' outputs, not a conflict copy of one another. When one of these
# fields is present on BOTH sides with different values, the pair fails
# closed before any key-level merge is attempted -- regardless of whether
# their other top-level keys would otherwise merge cleanly. A field present
# on only one side is not a mismatch by this rule: that is the ordinary case
# of a genuine conflict copy where one side has not yet picked up a newer
# generator's added metadata field.
JSON_SCHEMA_MARKER_FIELDS = ("schema_version", "generated_by")


def json_schema_mismatch(left: Any, right: Any) -> str | None:
    """Whether two JSON objects declare themselves different kinds of document.

    Returns a short reason if a schema/generator marker is present on both
    sides with different values, else ``None``. This is deliberately narrower
    than "top-level keys differ" -- disjoint keys are the adapter's documented
    safe case (e.g. two hosts each adding their own settings section) and
    must keep merging cleanly.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    for field in JSON_SCHEMA_MARKER_FIELDS:
        if field in left and field in right and left[field] != right[field]:
            return f"structural-schema-mismatch:{field}"
    return None


DEFAULT_DETECTORS = (
    re.compile(
        r"^(?P<stem>.+?) \((?P<tag>[^)]*(?:conflicted copy|conflict copy|conflict)[^)]*)\)"
        r"(?P<suffix>\.[^.]*)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<stem>.+?)\.(?P<tag>sync-conflict-\d+)(?P<suffix>\.[^.]*)?$",
        re.IGNORECASE,
    ),
)
WINDOWS_OFFLINE_FLAGS = 0x1000 | 0x00400000 | 0x00040000


class ReconcilerError(RuntimeError):
    """Expected fail-closed reconciliation error."""


class LeaseBusy(ReconcilerError):
    """Raised when another writer owns the root lease."""


@dataclasses.dataclass(frozen=True)
class Fingerprint:
    sha256: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class RootPolicy:
    root_id: str
    path: Path
    archive_dir: str
    cloud_ready: bool
    known_hosts: tuple[str, ...]
    mappings: dict[str, dict[str, Any]]
    exempt_name_patterns: tuple[re.Pattern[str], ...] = ()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _signed_payload(
    value: Mapping[str, Any], secret: str, field: str = "integrity_hmac_sha256"
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(value))
    payload.pop(field, None)
    payload[field] = hmac.new(
        secret.encode("utf-8"), _canonical_json(payload), hashlib.sha256
    ).hexdigest()
    return payload


def _verify_signed_payload(
    value: Mapping[str, Any], secret: str, field: str = "integrity_hmac_sha256"
) -> bool:
    supplied = value.get(field)
    if not isinstance(supplied, str) or not re.fullmatch(r"[a-f0-9]{64}", supplied):
        return False
    expected = _signed_payload(value, secret, field)[field]
    return hmac.compare_digest(supplied, expected)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _is_allowed_platform_alias(path: Path, resolved: Path) -> bool:
    """Allow only macOS's fixed ``/var`` to ``/private/var`` system alias."""

    return (
        sys.platform == "darwin"
        and path.as_posix() == "/var"
        and resolved.as_posix() == "/private/var"
    )


def _assert_no_reparse_components(
    path: Path, *, allow_missing: bool = False
) -> None:
    """Reject real link/reparse components without confusing Windows 8.3 aliases."""

    lexical = Path(os.path.abspath(path))
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return
            raise ReconcilerError(f"protected path is missing: {lexical}")
        attributes = int(getattr(info, "st_file_attributes", 0))
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
            try:
                resolved = current.resolve(strict=True)
            except OSError:
                resolved = current
            if _is_allowed_platform_alias(current, resolved):
                continue
            raise ReconcilerError(f"symlink-or-reparse-path:{lexical}")


def _assert_plain_path(
    root: Path,
    path: Path,
    *,
    allow_missing: bool = False,
    require_file: bool = False,
    require_dir: bool = False,
) -> None:
    """Reject symlink/reparse traversal and non-regular mutation targets."""

    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    if not _is_relative_to(path, root):
        raise ReconcilerError("path escapes protected root")
    if not root.exists() or not root.is_dir() or _is_link_or_reparse(root):
        raise ReconcilerError(
            "protected root is missing, non-directory, or reparse-backed"
        )
    _assert_no_reparse_components(root)
    current = root
    relative = path.relative_to(root)
    for index, part in enumerate(relative.parts):
        current = current / part
        is_leaf = index == len(relative.parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return
            raise ReconcilerError(f"protected path is missing: {relative.as_posix()}")
        attributes = int(getattr(info, "st_file_attributes", 0))
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
            raise ReconcilerError(f"symlink-or-reparse-path:{relative.as_posix()}")
        if not is_leaf and not stat.S_ISDIR(info.st_mode):
            raise ReconcilerError(f"non-directory path component:{relative.as_posix()}")
    if require_file and not stat.S_ISREG(path.lstat().st_mode):
        raise ReconcilerError(f"non-regular-file:{relative.as_posix()}")
    if require_dir and not stat.S_ISDIR(path.lstat().st_mode):
        raise ReconcilerError(f"non-directory:{relative.as_posix()}")


def _mkdir_plain(root: Path, path: Path, mode: int = 0o700) -> None:
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    _assert_plain_path(root, root, require_dir=True)
    if not _is_relative_to(path, root):
        raise ReconcilerError("directory escapes protected root")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            os.mkdir(current, mode)
        except FileExistsError:
            pass
        _assert_plain_path(root, current, require_dir=True)


def _safe_relative(root: Path, raw: str) -> tuple[Path, str]:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReconcilerError(f"path escapes allowlisted root: {raw!r}")
    windows_devices = {
        "AUX",
        "CON",
        "NUL",
        "PRN",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    for part in relative.parts:
        device_stem = part.split(".", 1)[0].upper()
        if (
            "\x00" in part
            or ":" in part
            or part.rstrip(" .") != part
            or device_stem in windows_devices
        ):
            raise ReconcilerError(f"unsafe cross-platform path component: {raw!r}")
    root = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(root / relative))
    if not _is_relative_to(candidate, root):
        raise ReconcilerError(f"path escapes allowlisted root: {raw!r}")
    resolved_root = root.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    if not _is_relative_to(resolved, resolved_root):
        raise ReconcilerError(f"path resolves outside allowlisted root: {raw!r}")
    normalized = relative.as_posix()
    return candidate, normalized


def _open_readonly(path: Path) -> int:
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    return os.open(path, flags)


def _fingerprint_from_stat(digest: str, info: os.stat_result) -> Fingerprint:
    return Fingerprint(
        digest,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_dev,
        info.st_ino,
    )


def _metadata_unchanged(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _read_bytes_stable(
    path: Path, root: Path | None = None
) -> tuple[bytes, Fingerprint]:
    if root is not None:
        _assert_plain_path(root, path, require_file=True)
    descriptor = _open_readonly(path)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReconcilerError(f"non-regular-file:{path.name}")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    if not _metadata_unchanged(before, after) or len(data) != after.st_size:
        raise ReconcilerError(f"active writer detected for {path.name}")
    return data, _fingerprint_from_stat(_sha256_bytes(data), after)


def _fingerprint(path: Path, root: Path | None = None) -> Fingerprint:
    if root is not None:
        _assert_plain_path(root, path, require_file=True)
    descriptor = _open_readonly(path)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReconcilerError(f"non-regular-file:{path.name}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not _metadata_unchanged(before, after):
        raise ReconcilerError(f"active writer detected for {path.name}")
    return _fingerprint_from_stat(digest.hexdigest(), after)


def _atomic_write(
    path: Path,
    data: bytes,
    *,
    root: Path | None = None,
    mode: int | None = None,
    expected: Fingerprint | None = None,
) -> None:
    if root is not None:
        _assert_plain_path(root, path.parent, require_dir=True)
        _assert_plain_path(root, path, allow_missing=True)
    existing_mode = mode
    if path.exists() and existing_mode is None:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.reconcile-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if existing_mode is not None:
            os.chmod(temporary_path, existing_mode)
        if root is not None:
            _assert_plain_path(root, path.parent, require_dir=True)
            _assert_plain_path(root, path, allow_missing=True)
        if expected is not None:
            if not path.exists() or _fingerprint(path, root) != expected:
                raise ReconcilerError(f"compare-before-replace failed for {path.name}")
        os.replace(temporary_path, path)
        if root is not None:
            _assert_plain_path(root, path, require_file=True)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


def _write_new_file(
    path: Path,
    data: bytes,
    *,
    root: Path,
    mode: int,
) -> None:
    """Create a missing rollback target without overwriting a late writer."""

    _assert_plain_path(root, path.parent, require_dir=True)
    _assert_plain_path(root, path, allow_missing=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError as exc:
        raise ReconcilerError(
            f"rollback target appeared before restore: {path.name}"
        ) from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _assert_plain_path(root, path, require_file=True)


def _json_object_merge(left: Any, right: Any, pointer: str = "$") -> Any:
    if left == right:
        return copy.deepcopy(left)
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise ReconcilerError(f"semantic JSON collision at {pointer}")
    merged = copy.deepcopy(left)
    for key, value in right.items():
        child = f"{pointer}.{key}"
        if key not in merged:
            merged[key] = copy.deepcopy(value)
        else:
            merged[key] = _json_object_merge(merged[key], value, child)
    return merged


def _diff_edits(
    base: Sequence[str], changed: Sequence[str]
) -> list[tuple[int, int, list[str]]]:
    edits: list[tuple[int, int, list[str]]] = []
    matcher = difflib.SequenceMatcher(a=base, b=changed, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            edits.append((i1, i2, list(changed[j1:j2])))
    return edits


def _edits_overlap(
    left: tuple[int, int, list[str]], right: tuple[int, int, list[str]]
) -> bool:
    l_start, l_end, _ = left
    r_start, r_end, _ = right
    if l_start == l_end and r_start == r_end:
        return l_start == r_start
    if l_start == l_end:
        return r_start <= l_start <= r_end
    if r_start == r_end:
        return l_start <= r_start <= l_end
    return max(l_start, r_start) < min(l_end, r_end)


def _three_way_merge(
    base_data: bytes, canonical_data: bytes, conflict_data: bytes
) -> bytes:
    try:
        base = base_data.decode("utf-8").splitlines(keepends=True)
        canonical = canonical_data.decode("utf-8").splitlines(keepends=True)
        conflict = conflict_data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise ReconcilerError("three-way-text requires UTF-8 text") from exc
    ours = _diff_edits(base, canonical)
    theirs = _diff_edits(base, conflict)
    combined: list[tuple[int, int, list[str]]] = list(ours)
    for candidate in theirs:
        duplicate = False
        for existing in ours:
            if candidate == existing:
                duplicate = True
                break
            if _edits_overlap(candidate, existing):
                raise ReconcilerError("three-way text edits overlap")
        if not duplicate:
            combined.append(candidate)
    merged = list(base)
    for start, end, replacement in sorted(
        combined, key=lambda item: item[0], reverse=True
    ):
        merged[start:end] = replacement
    return "".join(merged).encode("utf-8")


def _looks_binary_or_blocked(path: Path, data: bytes) -> str | None:
    lower_name = path.name.lower()
    lower_parts = {part.lower() for part in path.parts}
    if lower_name in SECRET_FILENAMES or lower_name.startswith(".env."):
        return "secret-path"
    if lower_parts & SECRET_PATH_PARTS:
        return "secret-path"
    if path.suffix.lower() in SECRET_SUFFIXES:
        return "secret-path"
    if any(
        marker in lower_name
        for marker in ("credential", "private-key", "private_key", "secret", "token")
    ):
        return "secret-path"
    if path.suffix.lower() in BLOCKED_EXTENSIONS or lower_name.endswith(
        ("-wal", "-shm")
    ):
        return "binary-database-or-archive"
    if any(part.lower() == ".git" for part in path.parts):
        return "git-internal"
    if b"\0" in data[:8192]:
        return "binary-content"
    if SECRET_CONTENT.search(data):
        return "secret-content"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "non-utf8-content"
    return None


def _validate_result(path: Path, data: bytes) -> None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        json.loads(data.decode("utf-8"))
    elif suffix == ".toml":
        try:
            import tomllib  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ReconcilerError("TOML validation unavailable on this Python") from exc
        tomllib.loads(data.decode("utf-8"))


def _path_hash(salt: str, root_id: str, relative: str) -> str:
    return hashlib.sha256(f"{salt}\0{root_id}\0{relative}".encode("utf-8")).hexdigest()


class RootLease:
    """Atomic per-root lease used by every provider adapter."""

    def __init__(
        self,
        lease_path: Path,
        actor: str,
        ttl_seconds: int,
        allow_expired_takeover: bool,
    ) -> None:
        self.lease_path = lease_path
        self.actor = actor
        self.ttl_seconds = ttl_seconds
        self.allow_expired_takeover = allow_expired_takeover
        self.acquired = False
        self.token = uuid.uuid4().hex

    @staticmethod
    def _assert_guard_binding(binding: tuple[Path, int, int, int]) -> None:
        guard_path, descriptor, device, inode = binding
        if _is_link_or_reparse(guard_path):
            raise LeaseBusy("lease mutation guard became unsafe")
        descriptor_info = os.fstat(descriptor)
        path_info = guard_path.stat()
        if (
            descriptor_info.st_dev != device
            or descriptor_info.st_ino != inode
            or path_info.st_dev != device
            or path_info.st_ino != inode
        ):
            raise LeaseBusy("lease mutation guard identity changed")

    @contextlib.contextmanager
    def _mutation_guard(self) -> Iterator[tuple[Path, int, int, int]]:
        """Serialize every mutation of this lease generation.

        The guard is a persistent host-local file whose OS lock is released by
        the kernel on process exit.  Keeping it separate from the replaceable
        lease path closes the check/replace gap during expired takeover.
        """

        guard_path = self.lease_path.with_name(f".{self.lease_path.name}.guard")
        if guard_path.exists() and _is_link_or_reparse(guard_path):
            raise LeaseBusy("lease mutation guard is a symlink or reparse point")
        flags = os.O_RDWR | os.O_CREAT | int(getattr(os, "O_BINARY", 0))
        flags |= int(getattr(os, "O_NOFOLLOW", 0))
        try:
            descriptor = os.open(guard_path, flags, 0o600)
        except OSError as exc:
            raise LeaseBusy("lease mutation guard is unavailable") from exc
        locked = False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise LeaseBusy("lease mutation guard is not a regular file")
            binding = (guard_path, descriptor, info.st_dev, info.st_ino)
            self._assert_guard_binding(binding)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LeaseBusy("lease mutation already in progress") from exc
            locked = True
            self._assert_guard_binding(binding)
            if info.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            yield binding
        finally:
            if locked:
                with contextlib.suppress(OSError):
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _restore_displaced_lease(self, data: bytes) -> bool:
        """Best-effort no-overwrite restore after a failed identity proof."""

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= int(getattr(os, "O_BINARY", 0))
        flags |= int(getattr(os, "O_NOFOLLOW", 0))
        try:
            descriptor = os.open(self.lease_path, flags, 0o600)
        except OSError:
            return False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            return False
        if _is_link_or_reparse(self.lease_path):
            return False
        try:
            restored = self.lease_path.read_bytes()
        except OSError:
            return False
        return hmac.compare_digest(restored, data)

    def __enter__(self) -> "RootLease":
        self.lease_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "system-gap.conflict-reconciler.lease.v1",
            "actor": self.actor,
            "token": self.token,
            "pid": os.getpid(),
            "created_epoch": time.time(),
            "expires_epoch": time.time() + self.ttl_seconds,
        }
        with self._mutation_guard() as guard_binding:
            if self.lease_path.exists() and _is_link_or_reparse(self.lease_path):
                raise LeaseBusy("lease path is a symlink or reparse point")
            for attempt in range(2):
                try:
                    descriptor = os.open(
                        self.lease_path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump(payload, stream, sort_keys=True)
                        stream.flush()
                        os.fsync(stream.fileno())
                    self.acquired = True
                    return self
                except FileExistsError:
                    if attempt or not self.allow_expired_takeover:
                        raise LeaseBusy(f"lease busy: {self.lease_path.name}")
                    try:
                        observed, observed_fp = _read_bytes_stable(
                            self.lease_path, self.lease_path.parent
                        )
                    except (OSError, ReconcilerError) as exc:
                        raise LeaseBusy(
                            "lease ownership cannot be read stably"
                        ) from exc
                    quarantine_kind = "expired"
                    try:
                        existing = json.loads(observed.decode("utf-8"))
                        expired = float(existing["expires_epoch"]) < time.time()
                    except (
                        UnicodeDecodeError,
                        ValueError,
                        KeyError,
                        TypeError,
                        json.JSONDecodeError,
                    ):
                        quarantine_kind = "malformed"
                        age_seconds = time.time() - (
                            observed_fp.mtime_ns / 1_000_000_000
                        )
                        expired = age_seconds > self.ttl_seconds
                    if not expired:
                        reason = (
                            "malformed lease is too recent for quarantine"
                            if quarantine_kind == "malformed"
                            else f"lease busy: {self.lease_path.name}"
                        )
                        raise LeaseBusy(reason)
                    current, current_fp = _read_bytes_stable(
                        self.lease_path, self.lease_path.parent
                    )
                    if current_fp != observed_fp or not hmac.compare_digest(
                        current, observed
                    ):
                        raise LeaseBusy("expired lease changed during takeover")
                    self._assert_guard_binding(guard_binding)
                    quarantine = self.lease_path.with_suffix(
                        f".{quarantine_kind}-{uuid.uuid4().hex}.lock"
                    )
                    try:
                        os.replace(self.lease_path, quarantine)
                    except OSError as exc:
                        raise LeaseBusy("expired lease takeover lost a race") from exc
                    displaced = quarantine.read_bytes()
                    if not hmac.compare_digest(displaced, observed):
                        self._restore_displaced_lease(displaced)
                        raise LeaseBusy("expired lease takeover identity check failed")
        raise LeaseBusy("unable to acquire lease")

    def renew(self) -> None:
        """Renew only the lease token this instance still owns."""

        if not self.acquired:
            raise LeaseBusy("lease is not owned")
        with self._mutation_guard() as guard_binding:
            temporary = self.lease_path.with_name(
                f".{self.lease_path.name}.{self.token}.{uuid.uuid4().hex}.renew"
            )
            try:
                if _is_link_or_reparse(self.lease_path):
                    raise LeaseBusy("lease path became a symlink or reparse point")
                observed, observed_fp = _read_bytes_stable(
                    self.lease_path, self.lease_path.parent
                )
                existing = json.loads(observed.decode("utf-8"))
                expires = float(existing["expires_epoch"])
                if existing.get("token") != self.token:
                    raise LeaseBusy("lease ownership was lost")
                if expires <= time.time():
                    raise LeaseBusy("lease expired before renewal")
                payload = {
                    **existing,
                    "renewed_epoch": time.time(),
                    "expires_epoch": time.time() + self.ttl_seconds,
                }
                encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | int(getattr(os, "O_BINARY", 0))
                    | int(getattr(os, "O_NOFOLLOW", 0))
                )
                descriptor = os.open(temporary, flags, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                current, current_fp = _read_bytes_stable(
                    self.lease_path, self.lease_path.parent
                )
                current_payload = json.loads(current.decode("utf-8"))
                if (
                    current_fp != observed_fp
                    or not hmac.compare_digest(current, observed)
                    or current_payload.get("token") != self.token
                    or float(current_payload["expires_epoch"]) <= time.time()
                ):
                    raise LeaseBusy("lease changed during renewal")
                self._assert_guard_binding(guard_binding)
                if _is_link_or_reparse(self.lease_path):
                    raise LeaseBusy("lease path became unsafe during renewal")
                os.replace(temporary, self.lease_path)
                final, _ = _read_bytes_stable(self.lease_path, self.lease_path.parent)
                final_payload = json.loads(final.decode("utf-8"))
                if (
                    not hmac.compare_digest(final, encoded)
                    or final_payload.get("token") != self.token
                ):
                    raise LeaseBusy("lease ownership was lost during renewal")
            except LeaseBusy:
                self.acquired = False
                raise
            except (
                OSError,
                UnicodeDecodeError,
                ValueError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
            ) as exc:
                self.acquired = False
                raise LeaseBusy("lease ownership cannot be verified") from exc
            finally:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.acquired:
            try:
                with self._mutation_guard():
                    if _is_link_or_reparse(self.lease_path):
                        return
                    try:
                        existing = json.loads(
                            self.lease_path.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError):
                        existing = {}
                    if existing.get("token") == self.token:
                        with contextlib.suppress(FileNotFoundError):
                            self.lease_path.unlink()
            except LeaseBusy:
                pass
            finally:
                self.acquired = False


class ConflictCopyReconciler:
    """Policy-based reconciliation API."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = copy.deepcopy(dict(config))
        if self.config.get("schema") != CONFIG_SCHEMA:
            raise ReconcilerError(f"config schema must be {CONFIG_SCHEMA}")
        self.actor = str(self.config.get("actor") or "unassigned-adapter")
        self.mode = str(self.config.get("mode") or "observer")
        if self.mode not in {"observer", "mutating-owner"}:
            raise ReconcilerError("mode must be observer or mutating-owner")
        self.lease_seconds = int(self.config.get("lease_seconds", 900))
        if self.lease_seconds < 30:
            raise ReconcilerError("lease_seconds must be at least 30")
        self.allow_expired_takeover = bool(
            self.config.get("allow_expired_takeover", False)
        )
        raw_state = self.config.get("state_dir")
        if not raw_state:
            raise ReconcilerError("state_dir is required and must be local/non-synced")
        expanded_state = Path(str(raw_state)).expanduser()
        _assert_no_reparse_components(expanded_state, allow_missing=True)
        self.state_dir = expanded_state.resolve()
        raw_salt = self.config.get("receipt_salt")
        if not isinstance(raw_salt, str) or len(raw_salt) < 16:
            raise ReconcilerError(
                "receipt_salt must be a persistent host-local secret of at least 16 characters"
            )
        self.receipt_salt = raw_salt
        self.config_digest = _sha256_bytes(_canonical_json(self.config))
        self.max_files = int(self.config.get("max_files", 10000))
        if self.max_files < 1:
            raise ReconcilerError("max_files must be positive")
        self.max_file_bytes = int(self.config.get("max_file_bytes", 10 * 1024 * 1024))
        if self.max_file_bytes < 1:
            raise ReconcilerError("max_file_bytes must be positive")
        self.allowed_classes = set(self.config.get("allowed_classes", SAFE_CLASSES))
        if not self.allowed_classes <= SAFE_CLASSES:
            raise ReconcilerError("unknown automatic merge class")
        self.roots = self._load_roots(self.config.get("roots"))
        for root in self.roots.values():
            if _is_relative_to(self.state_dir, root.path) or _is_relative_to(
                root.path, self.state_dir
            ):
                raise ReconcilerError(
                    f"state_dir must not overlap synced root {root.root_id}"
                )

    @classmethod
    def from_file(cls, path: str | Path) -> "ConflictCopyReconciler":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)

    def _ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        _assert_plain_path(self.state_dir, self.state_dir, require_dir=True)
        with contextlib.suppress(OSError):
            os.chmod(self.state_dir, 0o700)

    def _state_path(
        self,
        raw: str,
        *,
        allow_missing: bool = False,
        require_file: bool = False,
    ) -> Path:
        self._ensure_state_dir()
        path, normalized = _safe_relative(self.state_dir, raw)
        if normalized in {"", "."}:
            raise ReconcilerError("state path must name a child")
        _assert_plain_path(
            self.state_dir,
            path,
            allow_missing=allow_missing,
            require_file=require_file,
        )
        return path

    def _require_mutating_owner(self) -> None:
        if self.mode != "mutating-owner":
            raise ReconcilerError(
                "observer mode is read-only; use the single configured mutating owner"
            )

    def _load_roots(self, roots: Any) -> dict[str, RootPolicy]:
        if not isinstance(roots, list) or not roots:
            raise ReconcilerError("at least one allowlisted root is required")
        result: dict[str, RootPolicy] = {}
        for raw in roots:
            if not isinstance(raw, dict):
                raise ReconcilerError("root entries must be objects")
            root_id = str(raw.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", root_id):
                raise ReconcilerError("root id must be path-neutral")
            raw_path = str(raw.get("path") or "").strip()
            if not raw_path:
                raise ReconcilerError("root path is required")
            expanded_path = Path(raw_path).expanduser()
            _assert_no_reparse_components(expanded_path)
            path = expanded_path.resolve()
            _assert_plain_path(path, path, require_dir=True)
            if root_id in result:
                raise ReconcilerError(f"duplicate root id: {root_id}")
            archive_dir = str(raw.get("archive_dir") or ".conflict-copy-archive")
            archive_path, archive_relative = _safe_relative(path, archive_dir)
            if Path(archive_relative).as_posix() in {"", "."}:
                raise ReconcilerError("archive_dir must be a child directory")
            _assert_plain_path(path, archive_path, allow_missing=True)
            mappings: dict[str, dict[str, Any]] = {}
            for mapping in raw.get("canonical_mappings", []):
                if not isinstance(mapping, dict):
                    raise ReconcilerError("canonical mapping must be an object")
                _, conflict = _safe_relative(path, str(mapping.get("conflict") or ""))
                _, canonical = _safe_relative(path, str(mapping.get("canonical") or ""))
                if conflict in {"", "."} or canonical in {"", "."}:
                    raise ReconcilerError("canonical mapping paths must name files")
                if conflict == canonical:
                    raise ReconcilerError("conflict and canonical paths must differ")
                for mapped_path in (conflict, canonical):
                    if _is_relative_to(
                        Path(mapped_path),
                        Path(archive_relative),
                    ):
                        raise ReconcilerError(
                            f"canonical mapping overlaps archive_dir: {mapped_path}"
                        )
                if conflict in mappings:
                    raise ReconcilerError(f"duplicate conflict mapping: {conflict}")
                authority = mapping.get("authority")
                if (
                    not isinstance(authority, dict)
                    or authority.get("kind") not in AUTHORITY_KINDS
                    or not str(authority.get("reference") or "").strip()
                ):
                    raise ReconcilerError(
                        f"{conflict}: canonical mapping lacks authoritative evidence"
                    )
                item = copy.deepcopy(mapping)
                item["conflict"] = conflict
                item["canonical"] = canonical
                base = item.get("base")
                if isinstance(base, dict):
                    base_path, base_relative = _safe_relative(
                        path, str(base.get("path") or "")
                    )
                    if base_relative in {"", "."}:
                        raise ReconcilerError("base mapping path must name a file")
                    if _is_relative_to(Path(base_relative), Path(archive_relative)):
                        raise ReconcilerError("base mapping overlaps archive_dir")
                    _assert_plain_path(path, base_path, allow_missing=True)
                mappings[conflict] = item
            exempt_patterns: list[re.Pattern[str]] = []
            for raw_pattern in raw.get("exempt_name_patterns", []):
                if not isinstance(raw_pattern, str) or not raw_pattern:
                    raise ReconcilerError(
                        "exempt_name_patterns entries must be non-empty strings"
                    )
                try:
                    exempt_patterns.append(re.compile(raw_pattern))
                except re.error as exc:
                    raise ReconcilerError(
                        f"exempt_name_patterns entry is not a valid regex: {raw_pattern!r}"
                    ) from exc
            result[root_id] = RootPolicy(
                root_id=root_id,
                path=path,
                archive_dir=archive_relative,
                cloud_ready=bool(raw.get("cloud_ready", False)),
                known_hosts=tuple(str(value) for value in raw.get("known_hosts", [])),
                mappings=mappings,
                exempt_name_patterns=tuple(exempt_patterns),
            )
        policies = list(result.values())
        for index, left in enumerate(policies):
            for right in policies[index + 1 :]:
                if _is_relative_to(left.path, right.path) or _is_relative_to(
                    right.path, left.path
                ):
                    raise ReconcilerError(
                        f"allowlisted roots overlap: {left.root_id}, {right.root_id}"
                    )
        return result

    def _detect_name(self, root: RootPolicy, relative: str) -> str | None:
        name = Path(relative).name
        for detector in DEFAULT_DETECTORS:
            if detector.match(name):
                return f"provider-pattern:{detector.pattern}"
        for host in root.known_hosts:
            escaped = re.escape(host)
            if re.match(rf"^.+-{escaped}(?:\.[^.]+)?$", name, flags=re.IGNORECASE):
                return "known-host-suffix"
        if relative in root.mappings:
            return "explicit-policy"
        return None

    def _iter_candidates(
        self, root: RootPolicy, exempted: list[str] | None = None
    ) -> Iterator[tuple[Path, str, str]]:
        if not root.path.is_dir():
            return
        _assert_plain_path(root.path, root.path, require_dir=True)
        count = 0
        archive_path, _ = _safe_relative(root.path, root.archive_dir)
        for directory, dirnames, filenames in os.walk(root.path):
            current = Path(directory)
            _assert_plain_path(root.path, current, require_dir=True)
            safe_dirnames: list[str] = []
            for value in dirnames:
                candidate_dir = current / value
                if value.lower() in {".git", "_archive"}:
                    continue
                if value in REGENERABLE_DIR_NAMES:
                    continue
                try:
                    _assert_plain_path(root.path, candidate_dir, require_dir=True)
                except ReconcilerError:
                    continue
                if _is_relative_to(candidate_dir, archive_path):
                    continue
                safe_dirnames.append(value)
            dirnames[:] = safe_dirnames
            for filename in filenames:
                count += 1
                if count > self.max_files:
                    raise ReconcilerError(
                        f"root {root.root_id} exceeds max_files={self.max_files}"
                    )
                path = current / filename
                if is_regenerable(path):
                    continue
                relative = path.relative_to(root.path).as_posix()
                if any(
                    pattern.search(relative) for pattern in root.exempt_name_patterns
                ):
                    if exempted is not None:
                        exempted.append(relative)
                    continue
                reason = self._detect_name(root, relative)
                if reason:
                    yield path, relative, reason

    def _cloud_blocker(self, root: RootPolicy, paths: Sequence[Path]) -> str | None:
        if not root.cloud_ready:
            return "cloud-readiness-not-attested"
        if os.name == "nt":
            try:
                import ctypes

                getter = ctypes.windll.kernel32.GetFileAttributesW
                getter.argtypes = [ctypes.c_wchar_p]
                getter.restype = ctypes.c_uint32
                for path in paths:
                    attributes = getter(str(path))
                    if attributes != 0xFFFFFFFF and attributes & WINDOWS_OFFLINE_FLAGS:
                        return "cloud-placeholder-or-recall-pending"
            except (AttributeError, OSError):
                return "cloud-attribute-check-unavailable"
        if sys.platform == "darwin" and any(
            path.name.endswith(".icloud") for path in paths
        ):
            return "icloud-placeholder"
        return None

    def _lock_blocker(self, root: RootPolicy, paths: Sequence[Path]) -> str | None:
        checked: set[Path] = set()
        for path in paths:
            current = path.parent
            while _is_relative_to(current, root.path):
                if current not in checked:
                    checked.add(current)
                    for lock in current.glob("LOCK*.txt"):
                        if lock.is_file():
                            return f"active-lock:{lock.name}"
                if current == root.path:
                    break
                current = current.parent
        return None

    def _git_blocker(self, root: RootPolicy, relatives: Sequence[str]) -> str | None:
        repositories: set[Path] = set()
        git_executable = shutil.which("git")
        if not git_executable:
            return "git-dirty-check-unavailable"
        absolute_paths = [root.path / relative for relative in relatives]
        for path in absolute_paths:
            current = path.parent
            while _is_relative_to(current, root.path):
                if (current / ".git").exists():
                    repositories.add(current)
                    break
                if current == root.path:
                    break
                current = current.parent
        for repository in sorted(repositories):
            repository_relatives = [
                path.relative_to(repository).as_posix()
                for path in absolute_paths
                if _is_relative_to(path, repository)
            ]
            try:
                for marker in (
                    "MERGE_HEAD",
                    "REBASE_HEAD",
                    "CHERRY_PICK_HEAD",
                    "rebase-apply",
                    "rebase-merge",
                ):
                    # argv-only call; repository and file paths already passed
                    # the allowlist/no-reparse gates above.
                    resolved = subprocess.run(  # noqa: S603
                        [
                            git_executable,
                            "-C",
                            str(repository),
                            "rev-parse",
                            "--git-path",
                            marker,
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if resolved.returncode != 0:
                        return "git-dirty-check-failed"
                    marker_path = Path(resolved.stdout.strip())
                    if not marker_path.is_absolute():
                        marker_path = repository / marker_path
                    if marker_path.exists():
                        return "git-operation-in-progress"
                run = subprocess.run(  # noqa: S603
                    [
                        git_executable,
                        "-C",
                        str(repository),
                        "status",
                        "--porcelain",
                        "--",
                        *repository_relatives,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                return "git-dirty-check-unavailable"
            if run.returncode != 0:
                return "git-dirty-check-failed"
            if run.stdout.strip():
                return "foreign-dirty-work"
        return None

    def _classify(
        self,
        root: RootPolicy,
        canonical_path: Path,
        conflict_path: Path,
        mapping: Mapping[str, Any],
    ) -> tuple[str | None, bytes | None, list[str], dict[str, Any] | None]:
        blockers: list[str] = []
        try:
            canonical, _ = _read_bytes_stable(canonical_path, root.path)
            conflict, _ = _read_bytes_stable(conflict_path, root.path)
        except (OSError, ReconcilerError) as exc:
            return None, None, [f"read-stability:{type(exc).__name__}"], None
        for path, data in ((canonical_path, canonical), (conflict_path, conflict)):
            reason = _looks_binary_or_blocked(path, data)
            if reason:
                blockers.append(reason)
        if blockers:
            return None, None, sorted(set(blockers)), None
        requested = str(mapping.get("adapter") or "auto")
        result: bytes | None = None
        merge_class: str | None = None
        base_fingerprint: dict[str, Any] | None = None
        try:
            if canonical == conflict:
                merge_class, result = "exact", canonical
            elif requested in {"auto", "append-only-text"} and conflict.startswith(
                canonical
            ):
                suffix = conflict[len(canonical) :]
                if (
                    not canonical
                    or canonical.endswith((b"\n", b"\r"))
                    or suffix.startswith((b"\n", b"\r"))
                ):
                    merge_class, result = "append-only-text", conflict
            if merge_class is None and requested == "json-object":
                left = json.loads(canonical.decode("utf-8"))
                right = json.loads(conflict.decode("utf-8"))
                mismatch = json_schema_mismatch(left, right)
                if mismatch:
                    raise ReconcilerError(mismatch)
                merged = _json_object_merge(left, right)
                result = (
                    json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                ).encode("utf-8")
                merge_class = "json-object"
            if merge_class is None and requested == "three-way-text":
                base = mapping.get("base")
                if not isinstance(base, dict):
                    raise ReconcilerError("three-way merge requires a base mapping")
                # The caller resolves and verifies the base before invoking this branch.
                raw_base = mapping.get("_resolved_base_data")
                if not isinstance(raw_base, bytes):
                    raise ReconcilerError("three-way merge base was not verified")
                result = _three_way_merge(raw_base, canonical, conflict)
                merge_class = "three-way-text"
                base_fingerprint = mapping.get("_base_fingerprint")
            if merge_class is None:
                blockers.append("semantic-or-unproven-conflict")
            elif merge_class not in self.allowed_classes:
                blockers.append(f"merge-class-not-allowed:{merge_class}")
            elif result is not None:
                _validate_result(canonical_path, result)
        except (UnicodeDecodeError, json.JSONDecodeError, ReconcilerError) as exc:
            # The message is kept: every ReconcilerError raised in this method
            # is a structural reason (a JSON pointer, a fixed phrase), never
            # file content, and an opaque "ReconcilerError" is exactly the
            # kind of unexplained blocker that invites a reviewer to force a
            # merge they shouldn't. Length-capped in case a future exception
            # message is unbounded.
            blockers.append(f"merge-validation:{type(exc).__name__}:{str(exc)[:200]}")
            merge_class, result = None, None
        return merge_class, result, sorted(set(blockers)), base_fingerprint

    def scan(self) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        exempted_by_policy: dict[str, list[str]] = {}
        for root in self.roots.values():
            root_exempted: list[str] = []
            for conflict_path, conflict_relative, reason in self._iter_candidates(
                root, root_exempted
            ):
                mapping = root.mappings.get(conflict_relative)
                item: dict[str, Any] = {
                    "root_id": root.root_id,
                    "conflict": conflict_relative,
                    "detection": reason,
                    "mapped": mapping is not None,
                }
                if mapping is None:
                    item["blockers"] = ["canonical-authority-missing"]
                    candidates.append(item)
                    continue
                canonical_path, canonical_relative = _safe_relative(
                    root.path, mapping["canonical"]
                )
                item["canonical"] = canonical_relative
                item["authority"] = copy.deepcopy(mapping["authority"])
                if not os.path.lexists(conflict_path) or not os.path.lexists(
                    canonical_path
                ):
                    item["blockers"] = ["canonical-or-conflict-missing"]
                    item["status"] = "blocked"
                    candidates.append(item)
                    continue
                try:
                    _assert_plain_path(root.path, conflict_path, require_file=True)
                    _assert_plain_path(root.path, canonical_path, require_file=True)
                except ReconcilerError as exc:
                    item["blockers"] = [str(exc).split(":", 1)[0]]
                    item["status"] = "blocked"
                    candidates.append(item)
                    continue
                blockers: list[str] = []
                cloud = self._cloud_blocker(root, [canonical_path, conflict_path])
                lock = self._lock_blocker(root, [canonical_path, conflict_path])
                git = self._git_blocker(root, [canonical_relative, conflict_relative])
                blockers.extend(value for value in (cloud, lock, git) if value)
                try:
                    canonical_fp = _fingerprint(canonical_path, root.path)
                    conflict_fp = _fingerprint(conflict_path, root.path)
                    item["canonical_fingerprint"] = canonical_fp.as_dict()
                    item["conflict_fingerprint"] = conflict_fp.as_dict()
                except OSError as exc:
                    blockers.append(f"fingerprint:{type(exc).__name__}")
                resolved_mapping = copy.deepcopy(mapping)
                base = resolved_mapping.get("base")
                if isinstance(base, dict):
                    try:
                        base_path, base_relative = _safe_relative(
                            root.path, str(base.get("path") or "")
                        )
                        _assert_plain_path(root.path, base_path, require_file=True)
                        base_data, base_fp = _read_bytes_stable(base_path, root.path)
                        if base_fp.sha256 != str(base.get("sha256") or ""):
                            raise ReconcilerError("base hash mismatch")
                        resolved_mapping["_resolved_base_data"] = base_data
                        resolved_mapping["_base_fingerprint"] = {
                            **base_fp.as_dict(),
                            "path": base_relative,
                        }
                    except (OSError, ReconcilerError) as exc:
                        blockers.append(f"base-proof:{type(exc).__name__}")
                if (
                    item.get("canonical_fingerprint", {}).get("size", 0)
                    > self.max_file_bytes
                    or item.get("conflict_fingerprint", {}).get("size", 0)
                    > self.max_file_bytes
                ):
                    merge_class, result, class_blockers, base_fp = (
                        None,
                        None,
                        ["file-size-limit"],
                        None,
                    )
                else:
                    merge_class, result, class_blockers, base_fp = self._classify(
                        root, canonical_path, conflict_path, resolved_mapping
                    )
                blockers.extend(class_blockers)
                item["merge_class"] = merge_class
                if result is not None:
                    item["result_sha256"] = _sha256_bytes(result)
                    item["result_size"] = len(result)
                if base_fp:
                    item["base_fingerprint"] = base_fp
                item["blockers"] = sorted(set(blockers))
                item["status"] = "ready" if not item["blockers"] else "blocked"
                candidates.append(item)
            if root_exempted:
                exempted_by_policy[root.root_id] = sorted(root_exempted)
        return {
            "schema": SCAN_SCHEMA,
            "created_at": _utc_now(),
            "actor": self.actor,
            "candidates": candidates,
            "exempted_by_policy": exempted_by_policy,
        }

    def plan(self, scan: Mapping[str, Any] | None = None) -> dict[str, Any]:
        current_scan = self.scan()
        scan_data = dict(scan or current_scan)
        if scan_data.get("schema") != SCAN_SCHEMA:
            raise ReconcilerError("invalid scan schema")
        if scan is not None and (
            scan_data.get("actor") != self.actor
            or scan_data.get("candidates") != current_scan.get("candidates")
        ):
            raise ReconcilerError("supplied scan does not match a fresh readback")
        scan_data = current_scan
        plan = {
            "schema": PLAN_SCHEMA,
            "created_at": _utc_now(),
            "actor": self.actor,
            "mode": self.mode,
            "config_sha256": self.config_digest,
            "items": copy.deepcopy(scan_data.get("candidates", [])),
        }
        return _signed_payload(plan, self.receipt_salt)

    def _verify_plan(self, plan: Mapping[str, Any]) -> None:
        if plan.get("schema") != PLAN_SCHEMA:
            raise ReconcilerError("invalid plan schema")
        if plan.get("actor") != self.actor or plan.get("mode") != self.mode:
            raise ReconcilerError("plan actor or mode does not match current config")
        if plan.get("config_sha256") != self.config_digest:
            raise ReconcilerError("plan does not match current config")
        if not _verify_signed_payload(plan, self.receipt_salt):
            raise ReconcilerError("plan integrity check failed")

    def _lease(self, root_id: str) -> RootLease:
        self._ensure_state_dir()
        lease_dir = self._state_path("leases", allow_missing=True)
        _mkdir_plain(self.state_dir, lease_dir)
        _assert_plain_path(self.state_dir, lease_dir, require_dir=True)
        scope = os.path.normcase(str(self.roots[root_id].path))
        name = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24] + ".lock"
        return RootLease(
            lease_dir / name,
            self.actor,
            self.lease_seconds,
            self.allow_expired_takeover,
        )

    def _recompute_result(
        self, root: RootPolicy, item: Mapping[str, Any]
    ) -> tuple[Path, Path, bytes]:
        conflict_path, conflict_relative = _safe_relative(
            root.path, str(item["conflict"])
        )
        mapping = copy.deepcopy(root.mappings[conflict_relative])
        if str(item.get("canonical")) != str(mapping["canonical"]):
            raise ReconcilerError("plan canonical does not match authority mapping")
        canonical_path, canonical_relative = _safe_relative(
            root.path, str(item["canonical"])
        )
        _assert_plain_path(root.path, canonical_path, require_file=True)
        _assert_plain_path(root.path, conflict_path, require_file=True)
        blockers = [
            self._cloud_blocker(root, [canonical_path, conflict_path]),
            self._lock_blocker(root, [canonical_path, conflict_path]),
            self._git_blocker(root, [canonical_relative, conflict_relative]),
        ]
        if any(blockers):
            raise ReconcilerError("safety gate changed after plan")
        expected_canonical = item.get("canonical_fingerprint") or {}
        expected_conflict = item.get("conflict_fingerprint") or {}
        current_canonical = _fingerprint(canonical_path, root.path)
        current_conflict = _fingerprint(conflict_path, root.path)
        if current_canonical.as_dict() != expected_canonical:
            raise ReconcilerError("canonical changed after plan")
        if current_conflict.as_dict() != expected_conflict:
            raise ReconcilerError("conflict copy changed after plan")
        if (
            current_canonical.size > self.max_file_bytes
            or current_conflict.size > self.max_file_bytes
        ):
            raise ReconcilerError("file-size-limit")
        base = mapping.get("base")
        if isinstance(base, dict):
            base_path, base_relative = _safe_relative(root.path, str(base["path"]))
            _assert_plain_path(root.path, base_path, require_file=True)
            base_data, base_fp = _read_bytes_stable(base_path, root.path)
            if base_fp.sha256 != str(base.get("sha256") or ""):
                raise ReconcilerError("base changed after plan")
            mapping["_resolved_base_data"] = base_data
            mapping["_base_fingerprint"] = {**base_fp.as_dict(), "path": base_relative}
        merge_class, result, blockers, _ = self._classify(
            root, canonical_path, conflict_path, mapping
        )
        if blockers or merge_class != item.get("merge_class") or result is None:
            raise ReconcilerError("merge classification changed after plan")
        if _sha256_bytes(result) != item.get("result_sha256"):
            raise ReconcilerError("merge result changed after plan")
        return canonical_path, conflict_path, result

    def _redacted_receipt(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        records = []
        for record in manifest["records"]:
            records.append(
                {
                    "root_id": record["root_id"],
                    "canonical_id": _path_hash(
                        self.receipt_salt,
                        record["root_id"],
                        record["canonical"],
                    ),
                    "conflict_id": _path_hash(
                        self.receipt_salt,
                        record["root_id"],
                        record["conflict"],
                    ),
                    "merge_class": record["merge_class"],
                    "result_sha256": record["result_sha256"],
                    "verified": record.get("verified", False),
                    "rolled_back": record.get("rolled_back", False),
                }
            )
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "operation_id": manifest["operation_id"],
            "actor": manifest["actor"],
            "created_at": manifest["created_at"],
            "status": manifest["status"],
            "records": records,
        }
        receipt["integrity_sha256"] = _sha256_bytes(_canonical_json(receipt))
        return receipt

    def _manifest_path(self, operation_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise ReconcilerError("invalid operation id")
        operations = self._state_path("operations", allow_missing=True)
        _mkdir_plain(self.state_dir, operations)
        _assert_plain_path(self.state_dir, operations, require_dir=True)
        return self._state_path(
            f"operations/{operation_id}.json",
            allow_missing=True,
        )

    def _save_manifest(self, manifest: Mapping[str, Any]) -> Path:
        path = self._manifest_path(str(manifest["operation_id"]))
        signed_manifest = _signed_payload(manifest, self.receipt_salt)
        _atomic_write(
            path,
            (json.dumps(signed_manifest, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
            root=self.state_dir,
            mode=0o600,
        )
        receipt = self._redacted_receipt(signed_manifest)
        receipts = self._state_path("receipts", allow_missing=True)
        _mkdir_plain(self.state_dir, receipts)
        _assert_plain_path(self.state_dir, receipts, require_dir=True)
        receipt_path = self._state_path(
            f"receipts/{manifest['operation_id']}.json",
            allow_missing=True,
        )
        _atomic_write(
            receipt_path,
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            root=self.state_dir,
            mode=0o600,
        )
        return receipt_path

    def _load_manifest(self, operation_id: str) -> dict[str, Any]:
        path = self._manifest_path(operation_id)
        try:
            manifest_data, _ = _read_bytes_stable(path, self.state_dir)
            manifest = json.loads(manifest_data.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReconcilerError("operation manifest unavailable") from exc
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise ReconcilerError("invalid operation manifest")
        if not _verify_signed_payload(manifest, self.receipt_salt):
            raise ReconcilerError("operation manifest integrity check failed")
        if manifest.get("operation_id") != operation_id:
            raise ReconcilerError(
                "operation manifest identity does not match requested operation"
            )
        if manifest.get("actor") != self.actor:
            raise ReconcilerError("operation manifest actor does not match config")
        if manifest.get("config_sha256") != self.config_digest:
            raise ReconcilerError("operation manifest does not match current config")
        return manifest

    @staticmethod
    def _file_metadata(path: Path) -> dict[str, int]:
        info = path.stat()
        return {
            "mode": stat.S_IMODE(info.st_mode),
            "mtime_ns": info.st_mtime_ns,
        }

    @staticmethod
    def _restore_metadata(path: Path, metadata: Mapping[str, Any]) -> None:
        with contextlib.suppress(OSError):
            os.chmod(path, int(metadata["mode"]))
        with contextlib.suppress(OSError):
            os.utime(
                path,
                ns=(int(metadata["mtime_ns"]), int(metadata["mtime_ns"])),
            )

    def _write_private_backup(self, relative: str, data: bytes) -> Path:
        backup = self._state_path(relative, allow_missing=True)
        _mkdir_plain(self.state_dir, backup.parent)
        _assert_plain_path(self.state_dir, backup.parent, require_dir=True)
        _atomic_write(
            backup,
            data,
            root=self.state_dir,
            mode=0o600,
        )
        return backup

    def _archive_conflict(
        self,
        root: RootPolicy,
        conflict_path: Path,
        archive_path: Path,
        expected: Fingerprint,
    ) -> None:
        """Create a no-overwrite recoverable archive, then remove the source."""

        _assert_plain_path(root.path, conflict_path, require_file=True)
        _assert_plain_path(root.path, archive_path.parent, require_dir=True)
        _assert_plain_path(root.path, archive_path, allow_missing=True)
        if archive_path.exists():
            raise ReconcilerError("archive destination already exists")
        data, current = _read_bytes_stable(conflict_path, root.path)
        if current != expected:
            raise ReconcilerError("conflict copy changed before archive")
        descriptor = os.open(
            archive_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_BINARY", 0)),
            stat.S_IMODE(conflict_path.stat().st_mode),
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                archive_path.unlink()
            raise
        _assert_plain_path(root.path, archive_path, require_file=True)
        if _fingerprint(archive_path, root.path).sha256 != expected.sha256:
            with contextlib.suppress(FileNotFoundError):
                archive_path.unlink()
            raise ReconcilerError("archive hash verification failed")
        if _fingerprint(conflict_path, root.path) != expected:
            with contextlib.suppress(FileNotFoundError):
                archive_path.unlink()
            raise ReconcilerError("conflict copy changed during archive")
        conflict_path.unlink()

    def _record_paths(
        self, record: Mapping[str, Any]
    ) -> tuple[RootPolicy, Path, Path, Path, Path, Path]:
        root_id = str(record.get("root_id") or "")
        if root_id not in self.roots:
            raise ReconcilerError("operation manifest has unknown root")
        root = self.roots[root_id]
        canonical, canonical_relative = _safe_relative(
            root.path, str(record.get("canonical") or "")
        )
        conflict, conflict_relative = _safe_relative(
            root.path, str(record.get("conflict") or "")
        )
        mapping = root.mappings.get(conflict_relative)
        if not mapping or mapping.get("canonical") != canonical_relative:
            raise ReconcilerError(
                "operation record no longer matches authority mapping"
            )
        archive, archive_relative = _safe_relative(
            root.path, str(record.get("archive") or "")
        )
        expected_archive_prefix = Path(root.archive_dir) / str(
            record.get("operation_id") or ""
        )
        if not _is_relative_to(Path(archive_relative), expected_archive_prefix):
            raise ReconcilerError("operation archive path is outside operation scope")
        canonical_backup = self._state_path(
            str(record.get("canonical_backup") or ""),
            require_file=True,
        )
        conflict_backup = self._state_path(
            str(record.get("conflict_backup") or ""),
            require_file=True,
        )
        return (
            root,
            canonical,
            conflict,
            archive,
            canonical_backup,
            conflict_backup,
        )

    def apply(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        self._require_mutating_owner()
        self._verify_plan(plan)
        raw_items = plan.get("items", [])
        if not isinstance(raw_items, list) or any(
            not isinstance(item, dict) for item in raw_items
        ):
            raise ReconcilerError("plan items must be objects")
        ready = [item for item in raw_items if item.get("status") == "ready"]
        operation_id = uuid.uuid4().hex
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "operation_id": operation_id,
            "actor": self.actor,
            "config_sha256": self.config_digest,
            "created_at": _utc_now(),
            "status": "applying",
            "records": [],
        }
        leases: dict[str, RootLease] = {}
        try:
            for root_id in sorted({str(item["root_id"]) for item in ready}):
                if root_id not in self.roots:
                    raise ReconcilerError("plan contains unknown root")
                lease = self._lease(root_id)
                lease.__enter__()
                leases[root_id] = lease
            for item in ready:
                root = self.roots[str(item["root_id"])]
                lease = leases[root.root_id]
                lease.renew()
                canonical_path, conflict_path, result = self._recompute_result(
                    root, item
                )
                canonical_data, canonical_fp = _read_bytes_stable(
                    canonical_path, root.path
                )
                conflict_data, conflict_fp = _read_bytes_stable(
                    conflict_path, root.path
                )
                if canonical_fp.as_dict() != item["canonical_fingerprint"]:
                    raise ReconcilerError("canonical changed before backup")
                if conflict_fp.as_dict() != item["conflict_fingerprint"]:
                    raise ReconcilerError("conflict copy changed before backup")
                canonical_backup_relative = (
                    Path("backups")
                    / operation_id
                    / root.root_id
                    / "canonical"
                    / str(item["canonical"])
                ).as_posix()
                conflict_backup_relative = (
                    Path("backups")
                    / operation_id
                    / root.root_id
                    / "conflict"
                    / str(item["conflict"])
                ).as_posix()
                canonical_backup = self._write_private_backup(
                    canonical_backup_relative, canonical_data
                )
                conflict_backup = self._write_private_backup(
                    conflict_backup_relative, conflict_data
                )
                if (
                    _fingerprint(canonical_backup, self.state_dir).sha256
                    != canonical_fp.sha256
                ):
                    raise ReconcilerError("canonical backup hash verification failed")
                if (
                    _fingerprint(conflict_backup, self.state_dir).sha256
                    != conflict_fp.sha256
                ):
                    raise ReconcilerError("conflict backup hash verification failed")
                archive_root, _ = _safe_relative(root.path, root.archive_dir)
                _mkdir_plain(root.path, archive_root)
                archive_path = archive_root / operation_id / str(item["conflict"])
                _mkdir_plain(root.path, archive_path.parent)
                _assert_plain_path(root.path, archive_path, allow_missing=True)
                if archive_path.exists():
                    raise ReconcilerError("archive destination already exists")
                record = {
                    "operation_id": operation_id,
                    "root_id": root.root_id,
                    "canonical": str(item["canonical"]),
                    "conflict": str(item["conflict"]),
                    "archive": archive_path.relative_to(root.path).as_posix(),
                    "canonical_before": item["canonical_fingerprint"],
                    "conflict_before": item["conflict_fingerprint"],
                    "merge_class": item["merge_class"],
                    "result_sha256": item["result_sha256"],
                    "canonical_backup": canonical_backup.relative_to(
                        self.state_dir
                    ).as_posix(),
                    "conflict_backup": conflict_backup.relative_to(
                        self.state_dir
                    ).as_posix(),
                    "canonical_metadata": self._file_metadata(canonical_path),
                    "conflict_metadata": self._file_metadata(conflict_path),
                    "stage": "backed-up",
                    "archive_created": False,
                    "verified": False,
                    "rolled_back": False,
                }
                manifest["records"].append(record)
                self._save_manifest(manifest)
                lease.renew()
                if (
                    _fingerprint(canonical_path, root.path) != canonical_fp
                    or _fingerprint(conflict_path, root.path) != conflict_fp
                ):
                    raise ReconcilerError("source changed after backup")
                if item["merge_class"] != "exact":
                    _atomic_write(
                        canonical_path,
                        result,
                        root=root.path,
                        mode=int(record["canonical_metadata"]["mode"]),
                    )
                record["stage"] = "canonical-written"
                self._save_manifest(manifest)
                lease.renew()
                self._archive_conflict(
                    root,
                    conflict_path,
                    archive_path,
                    conflict_fp,
                )
                record["archive_created"] = True
                record["stage"] = "conflict-archived"
                self._save_manifest(manifest)
                lease.renew()
                self._verify_record(root, record)
                record["verified"] = True
                record["stage"] = "verified"
                self._save_manifest(manifest)
            manifest["status"] = "applied"
            receipt_path = self._save_manifest(manifest)
            return {
                "operation_id": operation_id,
                "status": "applied",
                "applied": len(ready),
                "blocked": len(plan.get("items", [])) - len(ready),
                "receipt": receipt_path.relative_to(self.state_dir).as_posix(),
                "receipt_data": self._redacted_receipt(manifest),
            }
        except BaseException as exc:
            manifest["status"] = "apply-failed"
            manifest["error_type"] = type(exc).__name__
            with contextlib.suppress(Exception):
                self._save_manifest(manifest)
            with contextlib.suppress(Exception):
                self._rollback_manifest(manifest, leases)
            if isinstance(exc, ReconcilerError):
                raise
            if not isinstance(exc, Exception):
                raise
            raise ReconcilerError(f"apply failed: {type(exc).__name__}") from exc
        finally:
            for lease in reversed(list(leases.values())):
                lease.__exit__(None, None, None)

    def _verify_record(self, root: RootPolicy, record: Mapping[str, Any]) -> None:
        canonical, _ = _safe_relative(root.path, str(record["canonical"]))
        conflict, _ = _safe_relative(root.path, str(record["conflict"]))
        archive, _ = _safe_relative(root.path, str(record["archive"]))
        _assert_plain_path(root.path, canonical, require_file=True)
        _assert_plain_path(root.path, archive, require_file=True)
        if (
            not canonical.is_file()
            or _fingerprint(canonical, root.path).sha256 != record["result_sha256"]
        ):
            raise ReconcilerError("canonical verification failed")
        if conflict.exists():
            raise ReconcilerError("conflict copy still present")
        if not archive.is_file():
            raise ReconcilerError("recoverable archive missing")
        if (
            _fingerprint(archive, root.path).sha256
            != record["conflict_before"]["sha256"]
        ):
            raise ReconcilerError("archive hash verification failed")
        canonical_data, _ = _read_bytes_stable(canonical, root.path)
        _validate_result(canonical, canonical_data)

    def verify(self, operation_id: str) -> dict[str, Any]:
        manifest = self._load_manifest(operation_id)
        if manifest["status"] == "rolled-back":
            raise ReconcilerError("rolled-back operation cannot be verified as applied")
        leases: dict[str, RootLease] = {}
        try:
            for root_id in sorted(
                {str(record["root_id"]) for record in manifest["records"]}
            ):
                lease = self._lease(root_id)
                lease.__enter__()
                leases[root_id] = lease
            for record in manifest["records"]:
                lease = leases[str(record["root_id"])]
                lease.renew()
                self._verify_record(self.roots[record["root_id"]], record)
                record["verified"] = True
                record["stage"] = "verified"
            manifest["status"] = "verified"
            self._save_manifest(manifest)
            return self._redacted_receipt(manifest)
        finally:
            for lease in reversed(list(leases.values())):
                lease.__exit__(None, None, None)

    def _inspect_rollback_record(
        self,
        record: Mapping[str, Any],
        lease: RootLease | None,
    ) -> dict[str, Any]:
        """Bind every rollback input to a stable fingerprint."""

        (
            root,
            canonical,
            conflict,
            archive,
            canonical_backup,
            conflict_backup,
        ) = self._record_paths(record)
        if lease:
            lease.renew()
        _assert_plain_path(root.path, canonical, require_file=True)
        _assert_plain_path(root.path, conflict, allow_missing=True)
        _assert_plain_path(root.path, archive, allow_missing=True)
        canonical_fp = _fingerprint(canonical, root.path)
        allowed_canonical_hashes = {
            str(record["canonical_before"]["sha256"]),
            str(record["result_sha256"]),
        }
        if canonical_fp.sha256 not in allowed_canonical_hashes:
            raise ReconcilerError("rollback would overwrite a changed canonical file")
        canonical_backup_data, canonical_backup_fp = _read_bytes_stable(
            canonical_backup, self.state_dir
        )
        conflict_backup_data, conflict_backup_fp = _read_bytes_stable(
            conflict_backup, self.state_dir
        )
        if canonical_backup_fp.sha256 != str(
            record["canonical_before"]["sha256"]
        ) or conflict_backup_fp.sha256 != str(record["conflict_before"]["sha256"]):
            raise ReconcilerError("rollback backup integrity check failed")
        conflict_exists = conflict.exists()
        archive_exists = archive.exists()
        if not conflict_exists and not archive_exists:
            raise ReconcilerError(
                "rollback source and recoverable archive are both missing"
            )
        conflict_fp = _fingerprint(conflict, root.path) if conflict_exists else None
        archive_fp = _fingerprint(archive, root.path) if archive_exists else None
        expected_conflict_hash = str(record["conflict_before"]["sha256"])
        if conflict_fp and conflict_fp.sha256 != expected_conflict_hash:
            raise ReconcilerError("rollback would overwrite a changed conflict copy")
        if (
            archive_fp
            and archive_fp.sha256 != expected_conflict_hash
            and (not conflict_exists or record.get("archive_created"))
        ):
            raise ReconcilerError("rollback would touch a changed recoverable archive")
        active_paths = [canonical]
        if conflict_exists:
            active_paths.append(conflict)
        if archive_exists:
            active_paths.append(archive)
        safety = [
            self._cloud_blocker(root, active_paths),
            self._lock_blocker(root, active_paths),
        ]
        if any(safety):
            raise ReconcilerError("rollback safety gate is not green")
        return {
            "root": root,
            "canonical": canonical,
            "conflict": conflict,
            "archive": archive,
            "canonical_backup_data": canonical_backup_data,
            "conflict_backup_data": conflict_backup_data,
            "canonical_fp": canonical_fp,
            "canonical_backup_fp": canonical_backup_fp,
            "conflict_backup_fp": conflict_backup_fp,
            "conflict_exists": conflict_exists,
            "conflict_fp": conflict_fp,
            "archive_exists": archive_exists,
            "archive_fp": archive_fp,
        }

    @staticmethod
    def _rollback_bindings_match(
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        *,
        include_canonical: bool,
    ) -> bool:
        keys = [
            "root",
            "canonical",
            "conflict",
            "archive",
            "canonical_backup_fp",
            "conflict_backup_fp",
            "conflict_exists",
            "conflict_fp",
            "archive_exists",
            "archive_fp",
        ]
        if include_canonical:
            keys.append("canonical_fp")
        return all(before[key] == after[key] for key in keys)

    def _rollback_manifest(
        self,
        manifest: dict[str, Any],
        leases: Mapping[str, RootLease] | None = None,
    ) -> None:
        """Preflight all records, then bind again immediately before mutation."""

        prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw_record in reversed(manifest["records"]):
            if not isinstance(raw_record, dict):
                raise ReconcilerError("operation record must be an object")
            record = raw_record
            if record.get("rolled_back"):
                continue
            root_id = str(record.get("root_id") or "")
            lease = leases[root_id] if leases else None
            prepared.append((record, self._inspect_rollback_record(record, lease)))

        for record, snapshot in prepared:
            root = snapshot["root"]
            lease = leases[root.root_id] if leases else None
            current = self._inspect_rollback_record(record, lease)
            if not self._rollback_bindings_match(
                snapshot, current, include_canonical=True
            ):
                raise ReconcilerError("rollback inputs changed after preflight")
            canonical = current["canonical"]
            conflict = current["conflict"]
            archive = current["archive"]
            _atomic_write(
                canonical,
                current["canonical_backup_data"],
                root=root.path,
                mode=int(record["canonical_metadata"]["mode"]),
                expected=current["canonical_fp"],
            )
            rebound = self._inspect_rollback_record(record, lease)
            if not self._rollback_bindings_match(
                snapshot, rebound, include_canonical=False
            ):
                raise ReconcilerError("rollback inputs changed after canonical restore")
            if rebound["canonical_fp"].sha256 != str(
                record["canonical_before"]["sha256"]
            ):
                raise ReconcilerError("canonical rollback verification failed")
            if _fingerprint(canonical, root.path) != rebound["canonical_fp"]:
                raise ReconcilerError("canonical changed before metadata restore")
            self._restore_metadata(canonical, record["canonical_metadata"])
            before_conflict = self._inspect_rollback_record(record, lease)
            if not self._rollback_bindings_match(
                snapshot, before_conflict, include_canonical=False
            ):
                raise ReconcilerError("rollback inputs changed before conflict restore")
            if before_conflict["canonical_fp"].sha256 != str(
                record["canonical_before"]["sha256"]
            ):
                raise ReconcilerError("canonical changed before conflict restore")
            if not snapshot["conflict_exists"]:
                _write_new_file(
                    conflict,
                    before_conflict["conflict_backup_data"],
                    root=root.path,
                    mode=int(record["conflict_metadata"]["mode"]),
                )
                restored_conflict = _fingerprint(conflict, root.path)
                if restored_conflict.sha256 != str(record["conflict_before"]["sha256"]):
                    raise ReconcilerError("conflict rollback verification failed")
                self._restore_metadata(conflict, record["conflict_metadata"])
            # Keep the recoverable archive as immutable rollback evidence.
            # A cleanup policy may remove it later only under a separate,
            # explicitly reviewed lifecycle.
            record["archive_retained"] = archive.exists()
            record["rolled_back"] = True
            record["verified"] = False
            record["stage"] = "rolled-back"
        manifest["status"] = "rolled-back"
        self._save_manifest(manifest)

    def rollback(self, operation_id: str) -> dict[str, Any]:
        self._require_mutating_owner()
        manifest = self._load_manifest(operation_id)
        leases: dict[str, RootLease] = {}
        try:
            for root_id in sorted(
                {record["root_id"] for record in manifest["records"]}
            ):
                lease = self._lease(root_id)
                lease.__enter__()
                leases[root_id] = lease
            self._rollback_manifest(manifest, leases)
            return self._redacted_receipt(manifest)
        finally:
            for lease in reversed(list(leases.values())):
                lease.__exit__(None, None, None)

    def reconcile(self) -> dict[str, Any]:
        return self.apply(self.plan())


def run_canary() -> dict[str, Any]:
    """Run a real temporary apply/verify/idempotency/rollback canary."""
    with tempfile.TemporaryDirectory(prefix="system-gap-canary-") as temporary:
        base = Path(temporary)
        root = base / "yard"
        state = base / "state"
        root.mkdir()
        canonical = root / "notes.md"
        conflict = root / "notes (host conflicted copy).md"
        canonical.write_text("alpha\n", encoding="utf-8")
        conflict.write_text("alpha\nbeta\n", encoding="utf-8")
        config = {
            "schema": CONFIG_SCHEMA,
            "actor": "temporary-canary",
            "mode": "mutating-owner",
            "state_dir": str(state),
            "receipt_salt": "temporary-canary-secret",
            "roots": [
                {
                    "id": "canary",
                    "path": str(root),
                    "archive_dir": ".archive",
                    "cloud_ready": True,
                    "canonical_mappings": [
                        {
                            "conflict": conflict.name,
                            "canonical": canonical.name,
                            "authority": {
                                "kind": "writer-policy",
                                "reference": "synthetic-canary",
                            },
                            "adapter": "append-only-text",
                        }
                    ],
                }
            ],
        }
        reconciler = ConflictCopyReconciler(config)
        applied = reconciler.reconcile()
        verified = reconciler.verify(applied["operation_id"])
        second_plan = reconciler.plan()
        reconciler.rollback(applied["operation_id"])
        restored = (
            canonical.read_text(encoding="utf-8") == "alpha\n" and conflict.is_file()
        )
        return {
            "schema": "system-gap.conflict-reconciler.canary.v1",
            "status": "pass"
            if verified["status"] == "verified"
            and not second_plan["items"]
            and restored
            else "fail",
            "applied": applied["applied"],
            "idempotent": not second_plan["items"],
            "rollback_restored": restored,
        }


def _dump(value: Mapping[str, Any], output: str | None) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        _atomic_write(Path(output), payload.encode("utf-8"))
    else:
        print(payload, end="")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("scan", "plan", "reconcile"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", required=True)
        child.add_argument("--output")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--config", required=True)
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--output")
    for command in ("verify", "rollback"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", required=True)
        child.add_argument("--operation-id", required=True)
        child.add_argument("--output")
    canary = subparsers.add_parser("canary")
    canary.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.command == "canary":
            result = run_canary()
        else:
            reconciler = ConflictCopyReconciler.from_file(args.config)
            if args.command == "scan":
                result = reconciler.scan()
            elif args.command == "plan":
                result = reconciler.plan()
            elif args.command == "apply":
                plan_data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
                result = reconciler.apply(plan_data)
            elif args.command == "reconcile":
                result = reconciler.reconcile()
            elif args.command == "verify":
                result = reconciler.verify(args.operation_id)
            else:
                result = reconciler.rollback(args.operation_id)
        _dump(result, args.output)
        return 0 if result.get("status") not in {"fail", "apply-failed"} else 1
    except (OSError, ValueError, ReconcilerError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "schema": "system-gap.conflict-reconciler.error.v1",
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
