"""Manifest-driven lifecycle manager for a system-gap-master yard.

The manager deliberately owns only paths declared by ``template/YARD_TEMPLATE.json``.
Everything else in a yard is instance-, host-, actor- or tool-owned and remains
untouched.  Existing files are never adopted by name alone: exact template bytes
may be adopted safely, a prior instance-state hash may authorize an update, and
all other collisions block the plan.

The read-only commands (``doctor``, ``inventory``, ``retention-plan`` and
``plan``) are suitable for an existing private yard.  ``upgrade`` requires a
saved, hash-bound plan and keeps local backups plus an integrity-protected
operation manifest outside the yard.  ``rollback`` restores only files that
still match the operation's recorded post-write hashes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

TEMPLATE_SCHEMA = "system-gap.yard-template.v1"
INSTANCE_SCHEMA = "system-gap.yard-instance.v1"
PLAN_SCHEMA = "system-gap.yard-plan.v1"
OPERATION_SCHEMA = "system-gap.yard-operation.v1"
INVENTORY_SCHEMA = "system-gap.yard-inventory.v1"
RETENTION_SCHEMA = "system-gap.yard-retention-plan.v1"
DOCTOR_SCHEMA = "system-gap.yard-doctor.v1"
ERROR_SCHEMA = "system-gap.yard-error.v1"

TEMPLATE_MANIFEST = "YARD_TEMPLATE.json"
INSTANCE_STATE = ".system-gap-instance.json"
ALLOWED_FILE_MODES = {"managed", "seed-once"}
ALLOWED_OWNERSHIP = {"repo", "instance", "host", "actor", "tool"}


class InstanceManagerError(RuntimeError):
    """Raised when a lifecycle safety boundary is not satisfied."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _payload_digest(value: Mapping[str, Any], digest_key: str) -> str:
    unsigned = dict(value)
    unsigned.pop(digest_key, None)
    return hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def _attach_digest(value: Mapping[str, Any], digest_key: str) -> dict[str, Any]:
    result = dict(value)
    result[digest_key] = _payload_digest(result, digest_key)
    return result


def _verify_digest(value: Mapping[str, Any], digest_key: str) -> bool:
    current = value.get(digest_key)
    return isinstance(current, str) and current == _payload_digest(value, digest_key)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _exclusive_write(path: Path, data: bytes) -> None:
    """Create ``path`` without replacing a late writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if created and path.exists():
            path.unlink()
        raise


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write(
        path,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        ),
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstanceManagerError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise InstanceManagerError(f"{label} must be a JSON object: {path}")
    return value


def _absolute_dir(raw: str | Path, label: str, *, must_exist: bool = True) -> Path:
    text = str(raw).strip()
    if not text:
        raise InstanceManagerError(f"{label} must be a non-empty path")
    if text.replace("/", "\\").startswith("\\\\"):
        raise InstanceManagerError(f"{label} must be host-local; UNC is forbidden")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise InstanceManagerError(f"{label} must be absolute")
    path = path.resolve(strict=False)
    if must_exist and not path.is_dir():
        raise InstanceManagerError(f"{label} must be an existing directory: {path}")
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _overlaps(left: Path, right: Path) -> bool:
    left_cmp = Path(os.path.normcase(os.path.abspath(left.resolve(strict=False))))
    right_cmp = Path(os.path.normcase(os.path.abspath(right.resolve(strict=False))))
    return _is_relative_to(left_cmp, right_cmp) or _is_relative_to(right_cmp, left_cmp)


def _normalise_relative(raw: Any, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise InstanceManagerError(f"{label} must be a non-empty relative path")
    if "\\" in raw:
        raise InstanceManagerError(f"{label} must use forward slashes: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InstanceManagerError(f"unsafe {label}: {raw!r}")
    return path.as_posix()


def _target(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    resolved_parent = candidate.parent.resolve(strict=False)
    if not _is_relative_to(resolved_parent, root.resolve(strict=False)):
        raise InstanceManagerError(f"path escapes managed root: {relative}")
    return candidate


def _path_has_link_boundary(root: Path, relative: str) -> bool:
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            try:
                mode = cursor.lstat().st_mode
            except OSError:
                return True
            if stat.S_ISLNK(mode):
                return True
    return False


@dataclass(frozen=True)
class YardTemplate:
    root: Path
    version: str
    directories: tuple[dict[str, Any], ...]
    files: tuple[dict[str, Any], ...]
    protected_patterns: tuple[str, ...]
    manifest_sha256: str


def load_template(template_root: str | Path) -> YardTemplate:
    root = _absolute_dir(template_root, "template_root")
    manifest_path = root / TEMPLATE_MANIFEST
    raw = _load_json(manifest_path, "yard template manifest")
    if raw.get("schema") != TEMPLATE_SCHEMA:
        raise InstanceManagerError("unsupported yard template schema")
    version = raw.get("template_version")
    if not isinstance(version, str) or not version.strip():
        raise InstanceManagerError("template_version must be a non-empty string")

    raw_directories = raw.get("directories")
    raw_files = raw.get("files")
    if not isinstance(raw_directories, list) or not isinstance(raw_files, list):
        raise InstanceManagerError("template directories/files must be arrays")

    directories: list[dict[str, Any]] = []
    seen_dirs: set[str] = set()
    for index, entry in enumerate(raw_directories):
        if not isinstance(entry, dict):
            raise InstanceManagerError(f"directories[{index}] must be an object")
        relative = _normalise_relative(entry.get("path"), f"directories[{index}].path")
        ownership = entry.get("ownership")
        if ownership not in ALLOWED_OWNERSHIP:
            raise InstanceManagerError(f"invalid ownership for {relative}: {ownership!r}")
        if relative in seen_dirs:
            raise InstanceManagerError(f"duplicate directory entry: {relative}")
        seen_dirs.add(relative)
        directories.append(
            {
                "path": relative,
                "ownership": ownership,
                "required": bool(entry.get("required", True)),
            }
        )

    files: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for index, entry in enumerate(raw_files):
        if not isinstance(entry, dict):
            raise InstanceManagerError(f"files[{index}] must be an object")
        relative = _normalise_relative(entry.get("path"), f"files[{index}].path")
        mode = entry.get("mode")
        if mode not in ALLOWED_FILE_MODES:
            raise InstanceManagerError(f"invalid file mode for {relative}: {mode!r}")
        if relative in seen_files:
            raise InstanceManagerError(f"duplicate file entry: {relative}")
        source = _target(root, relative)
        if _path_has_link_boundary(root, relative) or not source.is_file():
            raise InstanceManagerError(f"template source is missing or unsafe: {relative}")
        seen_files.add(relative)
        files.append(
            {"path": relative, "mode": mode, "sha256": _sha256_file(source)}
        )

    for entry in files:
        parent = PurePosixPath(entry["path"]).parent
        if parent != PurePosixPath(".") and parent.as_posix() not in seen_dirs:
            raise InstanceManagerError(
                f"template file parent must be declared as a directory: {entry['path']}"
            )

    patterns = raw.get("protected_patterns", [])
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise InstanceManagerError("protected_patterns must be an array of strings")

    return YardTemplate(
        root=root,
        version=version,
        directories=tuple(directories),
        files=tuple(files),
        protected_patterns=tuple(patterns),
        manifest_sha256=_sha256_file(manifest_path),
    )


def _load_instance_state(yard: Path) -> dict[str, Any] | None:
    path = yard / INSTANCE_STATE
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise InstanceManagerError("instance state is not a regular file")
    state = _load_json(path, "instance state")
    if state.get("schema") != INSTANCE_SCHEMA or not _verify_digest(
        state, "integrity_sha256"
    ):
        raise InstanceManagerError("instance state is invalid or integrity check failed")
    if not isinstance(state.get("instance_id"), str) or not state["instance_id"]:
        raise InstanceManagerError("instance state has no stable instance_id")
    if not isinstance(state.get("files"), dict):
        raise InstanceManagerError("instance state files must be an object")
    return state


def build_plan(yard_root: str | Path, template_root: str | Path) -> dict[str, Any]:
    yard = _absolute_dir(yard_root, "yard_root")
    template = load_template(template_root)
    if _overlaps(yard, template.root):
        raise InstanceManagerError("yard_root and template_root must not overlap")
    state = _load_instance_state(yard)
    state_path = yard / INSTANCE_STATE
    state_sha256 = _sha256_file(state_path) if state is not None else None
    state_files = state.get("files", {}) if state else {}
    actions: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for entry in template.directories:
        relative = entry["path"]
        target = _target(yard, relative)
        if _path_has_link_boundary(yard, relative):
            blockers.append({"path": relative, "reason": "link-boundary"})
        elif target.exists() and not target.is_dir():
            blockers.append({"path": relative, "reason": "expected-directory"})
        elif not target.exists() and entry["required"]:
            actions.append(
                {
                    "action": "create-directory",
                    "path": relative,
                    "ownership": entry["ownership"],
                }
            )
        elif not target.exists():
            warnings.append(
                {
                    "path": relative,
                    "kind": "optional-tool-zone-absent",
                    "ownership": entry["ownership"],
                }
            )

    for entry in template.files:
        relative = entry["path"]
        target = _target(yard, relative)
        source_sha = entry["sha256"]
        if _path_has_link_boundary(yard, relative):
            blockers.append({"path": relative, "reason": "link-boundary"})
            continue
        if not target.exists():
            actions.append(
                {
                    "action": "create-file",
                    "path": relative,
                    "mode": entry["mode"],
                    "source_sha256": source_sha,
                    "target_sha256": None,
                }
            )
            continue
        if not target.is_file():
            blockers.append({"path": relative, "reason": "expected-file"})
            continue
        target_sha = _sha256_file(target)
        if target_sha == source_sha:
            actions.append(
                {
                    "action": "adopt-exact" if relative not in state_files else "unchanged",
                    "path": relative,
                    "mode": entry["mode"],
                    "source_sha256": source_sha,
                    "target_sha256": target_sha,
                }
            )
            continue
        if entry["mode"] == "seed-once":
            actions.append(
                {
                    "action": "preserve-instance-file",
                    "path": relative,
                    "mode": entry["mode"],
                    "source_sha256": source_sha,
                    "target_sha256": target_sha,
                }
            )
            continue
        previous = state_files.get(relative)
        previous_applied = previous.get("applied_sha256") if isinstance(previous, dict) else None
        if previous_applied and previous_applied == target_sha:
            actions.append(
                {
                    "action": "update-file",
                    "path": relative,
                    "mode": entry["mode"],
                    "source_sha256": source_sha,
                    "target_sha256": target_sha,
                }
            )
        else:
            blockers.append(
                {
                    "path": relative,
                    "reason": "untracked-or-modified-managed-file",
                    "source_sha256": source_sha,
                    "target_sha256": target_sha,
                }
            )

    legacy = yard / "_transit"
    if legacy.exists():
        warnings.append(
            {
                "path": "_transit",
                "kind": "legacy-transit-review-required",
                "migration_target": "db-transit/<namespace>",
                "automatic_migration": False,
            }
        )

    summary: dict[str, int] = {}
    for action in actions:
        summary[action["action"]] = summary.get(action["action"], 0) + 1
    plan: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "created_at": _utc_now(),
        "yard_root": str(yard),
        "template_root": str(template.root),
        "template_version": template.version,
        "template_manifest_sha256": template.manifest_sha256,
        "instance_state_present": state is not None,
        "instance_state_sha256": state_sha256,
        "actions": actions,
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
    }
    return _attach_digest(plan, "plan_sha256")


def _verify_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != PLAN_SCHEMA or not _verify_digest(plan, "plan_sha256"):
        raise InstanceManagerError("plan is invalid or integrity check failed")
    for key in ("yard_root", "template_root", "template_version", "actions"):
        if key not in plan:
            raise InstanceManagerError(f"plan is missing {key}")
    if plan.get("blockers"):
        raise InstanceManagerError("plan contains blockers; upgrade is forbidden")


def _default_state_dir(yard: Path) -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        root = Path(base).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "system-gap-master" / "state"
    else:
        root = Path.home() / ".local" / "state" / "system-gap-master"
    yard_id = hashlib.sha256(str(yard).encode("utf-8")).hexdigest()[:20]
    return (root / "instance-manager" / yard_id).resolve(strict=False)


def _resolve_state_dir(raw: str | Path | None, yard: Path, template: Path) -> Path:
    state = (
        _absolute_dir(raw, "state_dir", must_exist=False)
        if raw is not None
        else _default_state_dir(yard)
    )
    if _overlaps(state, yard) or _overlaps(state, template):
        raise InstanceManagerError("state_dir must stay outside yard_root and template_root")
    state.mkdir(parents=True, exist_ok=True)
    return state


def _copy_verified(
    source: Path, target: Path, expected_sha: str, *, replace: bool
) -> str:
    data = source.read_bytes()
    actual = _sha256_bytes(data)
    if actual != expected_sha:
        raise InstanceManagerError(f"template source changed after planning: {source}")
    if replace:
        _atomic_write(target, data)
    else:
        _exclusive_write(target, data)
    if _sha256_file(target) != expected_sha:
        raise InstanceManagerError(f"post-write hash mismatch: {target}")
    return expected_sha


def _build_instance_state(
    yard: Path,
    template: YardTemplate,
    operation_id: str,
    previous_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for entry in template.files:
        relative = entry["path"]
        target = _target(yard, relative)
        if target.is_file() and not target.is_symlink():
            files[relative] = {
                "mode": entry["mode"],
                "template_sha256": entry["sha256"],
                "applied_sha256": _sha256_file(target),
            }
    state = {
        "schema": INSTANCE_SCHEMA,
        "instance_id": (
            str(previous_state["instance_id"])
            if previous_state is not None
            else uuid.uuid4().hex
        ),
        "template_version": template.version,
        "template_manifest_sha256": template.manifest_sha256,
        "last_operation_id": operation_id,
        "updated_at": _utc_now(),
        "files": files,
    }
    return _attach_digest(state, "integrity_sha256")


def apply_plan(plan_path: str | Path, state_dir: str | Path | None = None) -> dict[str, Any]:
    plan_file = Path(plan_path).expanduser().resolve(strict=True)
    plan = _load_json(plan_file, "yard plan")
    _verify_plan(plan)
    yard = _absolute_dir(str(plan["yard_root"]), "yard_root")
    template = load_template(str(plan["template_root"]))
    if template.version != plan["template_version"] or template.manifest_sha256 != plan.get(
        "template_manifest_sha256"
    ):
        raise InstanceManagerError("template changed after planning")
    local_state = _resolve_state_dir(state_dir, yard, template.root)

    operation_id = uuid.uuid4().hex
    operation_dir = local_state / "operations" / operation_id
    backup_dir = operation_dir / "backups"
    operation_dir.mkdir(parents=True, exist_ok=False)
    backup_dir.mkdir()

    state_path = yard / INSTANCE_STATE
    previous_state = state_path.read_bytes() if state_path.is_file() and not state_path.is_symlink() else None
    if state_path.exists() and previous_state is None:
        raise InstanceManagerError("instance state path is unsafe")
    previous_state_data = _load_instance_state(yard) if previous_state is not None else None
    previous_state_sha = _sha256_bytes(previous_state) if previous_state is not None else None
    if previous_state_sha != plan.get("instance_state_sha256"):
        raise InstanceManagerError("instance state changed after planning")

    records: list[dict[str, Any]] = []
    created_dirs: list[str] = []
    try:
        for action in plan["actions"]:
            kind = action.get("action")
            relative = _normalise_relative(action.get("path"), "plan action path")
            target = _target(yard, relative)
            if _path_has_link_boundary(yard, relative):
                raise InstanceManagerError(f"link boundary appeared after planning: {relative}")
            if kind == "create-directory":
                if target.exists():
                    if not target.is_dir():
                        raise InstanceManagerError(f"directory target changed: {relative}")
                    continue
                target.mkdir(parents=True, exist_ok=False)
                created_dirs.append(relative)
            elif kind in {"create-file", "update-file"}:
                expected_target = action.get("target_sha256")
                if kind == "create-file" and target.exists():
                    raise InstanceManagerError(f"create target appeared after planning: {relative}")
                if kind == "update-file":
                    if not target.is_file() or _sha256_file(target) != expected_target:
                        raise InstanceManagerError(f"update target changed after planning: {relative}")
                    backup = backup_dir.joinpath(*PurePosixPath(relative).parts)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, backup)
                    if _sha256_file(backup) != expected_target:
                        raise InstanceManagerError(f"backup verification failed: {relative}")
                source = _target(template.root, relative)
                after_sha = _copy_verified(
                    source,
                    target,
                    str(action["source_sha256"]),
                    replace=kind == "update-file",
                )
                records.append(
                    {
                        "action": kind,
                        "path": relative,
                        "before_sha256": expected_target,
                        "after_sha256": after_sha,
                    }
                )
            elif kind in {"adopt-exact", "unchanged", "preserve-instance-file"}:
                expected = action.get("target_sha256")
                if not target.is_file() or _sha256_file(target) != expected:
                    raise InstanceManagerError(f"preserved target changed after planning: {relative}")
            else:
                raise InstanceManagerError(f"unsupported plan action: {kind!r}")

        state = _build_instance_state(
            yard, template, operation_id, previous_state_data
        )
        current_state_sha = _sha256_file(state_path) if state_path.is_file() else None
        if current_state_sha != previous_state_sha:
            raise InstanceManagerError("instance state changed before commit")
        state_bytes = (
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        if previous_state is None:
            _exclusive_write(state_path, state_bytes)
        else:
            _atomic_write(state_path, state_bytes)
        operation: dict[str, Any] = {
            "schema": OPERATION_SCHEMA,
            "operation_id": operation_id,
            "created_at": _utc_now(),
            "status": "applied",
            "yard_root": str(yard),
            "template_root": str(template.root),
            "template_version": template.version,
            "plan_sha256": plan["plan_sha256"],
            "previous_instance_state_sha256": (
                previous_state_sha
            ),
            "records": records,
            "created_directories": created_dirs,
        }
        if previous_state is not None:
            _atomic_write(operation_dir / "previous-instance-state.json", previous_state)
        operation = _attach_digest(operation, "integrity_sha256")
        _write_json(operation_dir / "operation.json", operation)
        return {
            "schema": OPERATION_SCHEMA,
            "operation_id": operation_id,
            "status": "applied",
            "yard_root": str(yard),
            "template_version": template.version,
            "changed_paths": len(records) + len(created_dirs),
            "state_dir": str(local_state),
        }
    except Exception:
        # The operation did not reach a durable manifest. Revert the bounded
        # records accumulated so far before surfacing the failure.
        for record in reversed(records):
            relative = record["path"]
            target = _target(yard, relative)
            if record["action"] == "create-file" and target.is_file():
                if _sha256_file(target) == record["after_sha256"]:
                    target.unlink()
            elif record["action"] == "update-file":
                backup = backup_dir.joinpath(*PurePosixPath(relative).parts)
                if backup.is_file() and target.is_file() and _sha256_file(target) == record["after_sha256"]:
                    _atomic_write(target, backup.read_bytes())
        for relative in reversed(created_dirs):
            target = _target(yard, relative)
            if target.is_dir() and not any(target.iterdir()):
                target.rmdir()
        if previous_state is None:
            if state_path.is_file():
                state_path.unlink()
        else:
            _atomic_write(state_path, previous_state)
        raise


def rollback_operation(
    operation_id: str,
    yard_root: str | Path,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    if not operation_id or any(ch not in "0123456789abcdef" for ch in operation_id):
        raise InstanceManagerError("operation_id must be lowercase hexadecimal")
    yard = _absolute_dir(yard_root, "yard_root")
    # The template path is recovered from the operation after locating the
    # default state directory. Explicit state_dir is required for custom paths.
    local_state = (
        _absolute_dir(state_dir, "state_dir")
        if state_dir is not None
        else _default_state_dir(yard)
    )
    if _overlaps(local_state, yard):
        raise InstanceManagerError("state_dir must stay outside yard_root")
    operation_dir = local_state / "operations" / operation_id
    operation_path = operation_dir / "operation.json"
    operation = _load_json(operation_path, "operation manifest")
    if operation.get("schema") != OPERATION_SCHEMA or operation.get("operation_id") != operation_id:
        raise InstanceManagerError("operation manifest identity mismatch")
    if not _verify_digest(operation, "integrity_sha256"):
        raise InstanceManagerError("operation manifest integrity check failed")
    if operation.get("status") != "applied":
        raise InstanceManagerError("operation is not in applied state")
    if operation.get("yard_root") != str(yard):
        raise InstanceManagerError("operation belongs to another yard")

    # Full preflight: no path is changed before every post-write binding and
    # backup has been verified.
    for record in operation["records"]:
        relative = _normalise_relative(record.get("path"), "operation path")
        target = _target(yard, relative)
        if _path_has_link_boundary(yard, relative) or not target.is_file():
            raise InstanceManagerError(f"rollback target is missing or unsafe: {relative}")
        if _sha256_file(target) != record.get("after_sha256"):
            raise InstanceManagerError(f"rollback target changed after upgrade: {relative}")
        if record["action"] == "update-file":
            backup = operation_dir / "backups" / Path(*PurePosixPath(relative).parts)
            if not backup.is_file() or _sha256_file(backup) != record.get("before_sha256"):
                raise InstanceManagerError(f"rollback backup is missing or changed: {relative}")

    state_path = yard / INSTANCE_STATE
    current_state = _load_instance_state(yard)
    if current_state is None or current_state.get("last_operation_id") != operation_id:
        raise InstanceManagerError("instance state no longer points to this operation")

    previous_state_path = operation_dir / "previous-instance-state.json"
    previous_sha = operation.get("previous_instance_state_sha256")
    previous_state_data: bytes | None = None
    if previous_sha is not None:
        if not previous_state_path.is_file():
            raise InstanceManagerError("previous instance state backup is missing")
        previous_state_data = previous_state_path.read_bytes()
        if _sha256_bytes(previous_state_data) != previous_sha:
            raise InstanceManagerError("previous instance state backup changed")

    for record in reversed(operation["records"]):
        relative = record["path"]
        target = _target(yard, relative)
        if record["action"] == "create-file":
            target.unlink()
        elif record["action"] == "update-file":
            backup = operation_dir / "backups" / Path(*PurePosixPath(relative).parts)
            _atomic_write(target, backup.read_bytes())
            if _sha256_file(target) != record["before_sha256"]:
                raise InstanceManagerError(f"rollback verification failed: {relative}")

    for relative in reversed(operation.get("created_directories", [])):
        target = _target(yard, _normalise_relative(relative, "created directory"))
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()

    if previous_sha is None:
        state_path.unlink()
    else:
        assert previous_state_data is not None
        _atomic_write(state_path, previous_state_data)

    operation["status"] = "rolled-back"
    operation["rolled_back_at"] = _utc_now()
    operation = _attach_digest(operation, "integrity_sha256")
    _write_json(operation_path, operation)
    return {
        "schema": OPERATION_SCHEMA,
        "operation_id": operation_id,
        "status": "rolled-back",
        "yard_root": str(yard),
        "restored_paths": len(operation["records"]),
    }


def inventory_yard(yard_root: str | Path, template_root: str | Path) -> dict[str, Any]:
    yard = _absolute_dir(yard_root, "yard_root")
    template = load_template(template_root)
    known = {entry["path"]: entry for entry in template.directories}
    items: list[dict[str, Any]] = []
    for child in sorted(yard.iterdir(), key=lambda item: item.name.casefold()):
        relative = child.name
        manifest_entry = known.get(relative)
        if child.is_symlink():
            kind = "link"
        elif child.is_dir():
            kind = "directory"
        elif child.is_file():
            kind = "file"
        else:
            kind = "other"
        items.append(
            {
                "path": relative,
                "kind": kind,
                "ownership": manifest_entry["ownership"] if manifest_entry else "unmanaged",
                "required": manifest_entry["required"] if manifest_entry else False,
            }
        )
    return {
        "schema": INVENTORY_SCHEMA,
        "created_at": _utc_now(),
        "yard_root": str(yard),
        "template_version": template.version,
        "items": items,
        "summary": {
            "top_level_items": len(items),
            "managed_top_level_directories": len(known),
            "unmanaged_top_level_items": sum(item["ownership"] == "unmanaged" for item in items),
        },
    }


def retention_plan(
    yard_root: str | Path,
    *,
    now: dt.datetime | None = None,
    host_file_days: int = 90,
    message_days: int = 7,
    archive_days: int = 365,
    legacy_host_slots: Sequence[str] = (),
) -> dict[str, Any]:
    yard = _absolute_dir(yard_root, "yard_root")
    reference = now or dt.datetime.now(dt.timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=dt.timezone.utc)
    candidates: list[dict[str, Any]] = []

    def age_days(path: Path) -> int:
        modified = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
        return max(0, (reference - modified).days)

    slot_roots: list[tuple[Path, str]] = []
    hosts = yard / "hosts"
    if hosts.is_dir() and not hosts.is_symlink():
        for host in sorted(hosts.iterdir(), key=lambda item: item.name.casefold()):
            if not host.is_dir() or host.is_symlink() or host.name.startswith("_"):
                continue
            slot_roots.append((host, f"hosts/{host.name}"))

    seen_slot_paths = {path.resolve(strict=False) for path, _ in slot_roots}
    for raw in legacy_host_slots:
        relative = _normalise_relative(raw, "legacy_host_slot")
        slot = _target(yard, relative)
        if slot.is_dir() and not slot.is_symlink() and slot.resolve(strict=False) not in seen_slot_paths:
            slot_roots.append((slot, relative))
            seen_slot_paths.add(slot.resolve(strict=False))

    for slot, relative in slot_roots:
        files = [item for item in slot.iterdir() if item.is_file() and not item.is_symlink()]
        if len(files) > 100:
            candidates.append(
                {
                    "path": relative,
                    "kind": "flat-slot-index-needed",
                    "file_count": len(files),
                    "safe_to_apply": False,
                }
            )
        for item in files:
            age = age_days(item)
            if age >= host_file_days and item.name.lower() != "readme.md":
                candidates.append(
                    {
                        "path": f"{relative}/{item.name}",
                        "kind": "review-host-artifact",
                        "age_days": age,
                        "safe_to_apply": False,
                        "reason": "writer-reader-and-references-must-be-proven",
                    }
                )

    messages = yard / "messages"
    if messages.is_dir() and not messages.is_symlink():
        for item in sorted(messages.iterdir(), key=lambda value: value.name.casefold()):
            if item.is_file() and not item.is_symlink() and item.name.lower() != "readme.md":
                age = age_days(item)
                if age >= message_days:
                    candidates.append(
                        {
                            "path": f"messages/{item.name}",
                            "kind": "review-stale-message",
                            "age_days": age,
                            "safe_to_apply": False,
                            "reason": "recipient-ack-required",
                        }
                    )

    archive = yard / "_archive"
    if archive.is_dir() and not archive.is_symlink():
        for item in sorted(archive.iterdir(), key=lambda value: value.name.casefold()):
            if item.name.lower() == "readme.md" or item.is_symlink():
                continue
            age = age_days(item)
            if age >= archive_days:
                candidates.append(
                    {
                        "path": f"_archive/{item.name}",
                        "kind": "review-old-archive",
                        "age_days": age,
                        "safe_to_apply": False,
                        "reason": "explicit-owner-approval-required",
                    }
                )

    return {
        "schema": RETENTION_SCHEMA,
        "created_at": _utc_now(),
        "yard_root": str(yard),
        "policy": {
            "host_file_days": host_file_days,
            "message_days": message_days,
            "archive_days": archive_days,
            "legacy_host_slots": list(legacy_host_slots),
            "automatic_mutation": False,
        },
        "candidates": candidates,
        "summary": {"review_candidates": len(candidates), "safe_to_apply": 0},
    }


def doctor_yard(yard_root: str | Path, template_root: str | Path) -> dict[str, Any]:
    plan = build_plan(yard_root, template_root)
    yard = Path(str(plan["yard_root"]))
    findings: list[dict[str, Any]] = list(plan["warnings"])
    hosts = yard / "hosts"
    if hosts.is_dir() and not hosts.is_symlink():
        for host in sorted(hosts.iterdir(), key=lambda item: item.name.casefold()):
            if host.is_dir() and not host.is_symlink() and not host.name.startswith("_"):
                file_count = sum(
                    child.is_file() and not child.is_symlink() for child in host.iterdir()
                )
                if file_count > 100:
                    findings.append(
                        {
                            "path": f"hosts/{host.name}",
                            "kind": "flat-slot-over-threshold",
                            "file_count": file_count,
                            "threshold": 100,
                        }
                    )
    if plan["blockers"]:
        status = "blocked"
    elif findings or any(
        action["action"] in {"create-directory", "create-file", "update-file"}
        for action in plan["actions"]
    ):
        status = "needs-attention"
    else:
        status = "ok"
    return {
        "schema": DOCTOR_SCHEMA,
        "created_at": _utc_now(),
        "yard_root": str(yard),
        "template_version": plan["template_version"],
        "status": status,
        "blockers": plan["blockers"],
        "findings": findings,
        "plan_summary": plan["summary"],
        "mutation_performed": False,
    }


def _dump(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _default_template_root() -> Path:
    return Path(__file__).resolve().parent.parent / "template"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yard-instance-manager",
        description=(
            "Manifest-driven doctor/plan/upgrade/rollback for a system-gap-master "
            "yard. Only declared template paths can be changed."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_roots(command: argparse.ArgumentParser) -> None:
        command.add_argument("--yard-root", required=True)
        command.add_argument("--template-root", default=str(_default_template_root()))

    for name in ("doctor", "inventory", "plan"):
        command = subparsers.add_parser(name)
        add_roots(command)
        if name == "plan":
            command.add_argument("--output")

    retention = subparsers.add_parser("retention-plan")
    retention.add_argument("--yard-root", required=True)
    retention.add_argument("--host-file-days", type=int, default=90)
    retention.add_argument("--message-days", type=int, default=7)
    retention.add_argument("--archive-days", type=int, default=365)
    retention.add_argument(
        "--legacy-host-slot",
        action="append",
        default=[],
        help="relative legacy host-slot path to inspect (repeatable)",
    )

    upgrade = subparsers.add_parser("upgrade")
    upgrade.add_argument("--plan", required=True)
    upgrade.add_argument("--state-dir")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--operation-id", required=True)
    rollback.add_argument("--yard-root", required=True)
    rollback.add_argument("--state-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor_yard(args.yard_root, args.template_root)
        elif args.command == "inventory":
            result = inventory_yard(args.yard_root, args.template_root)
        elif args.command == "retention-plan":
            for label in ("host_file_days", "message_days", "archive_days"):
                if getattr(args, label) < 0:
                    raise InstanceManagerError(f"{label} must be non-negative")
            result = retention_plan(
                args.yard_root,
                host_file_days=args.host_file_days,
                message_days=args.message_days,
                archive_days=args.archive_days,
                legacy_host_slots=args.legacy_host_slot,
            )
        elif args.command == "plan":
            result = build_plan(args.yard_root, args.template_root)
            if args.output:
                output = Path(args.output).expanduser().resolve(strict=False)
                _write_json(output, result)
                result = {
                    "schema": PLAN_SCHEMA,
                    "plan_sha256": result["plan_sha256"],
                    "output": str(output),
                    "blockers": len(result["blockers"]),
                }
        elif args.command == "upgrade":
            result = apply_plan(args.plan, args.state_dir)
        else:
            result = rollback_operation(
                args.operation_id, args.yard_root, args.state_dir
            )
        _dump(result)
        if args.command == "doctor" and result.get("status") == "blocked":
            return 2
        return 0
    except (InstanceManagerError, OSError, ValueError) as exc:
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
