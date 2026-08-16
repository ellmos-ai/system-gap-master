"""Fail-closed, single-file SFTP executor for trusted-peer pull plans.

The existing :mod:`trusted_peer_paths` module remains a network-free planner.
This module is an additive execution boundary: it re-runs that planner, verifies
the registry and a short-lived one-shot grant, resolves credentials only from a
host-local configuration, pins the presented SSH host key, streams one regular
file into an exclusive staging file, and commits without replacing a target.

Nothing in the sync yard can select an identity file, a known-hosts file, an
allowed-signers file, a destination root, or an executable.  Receipts and the
attempt ledger are host-local and never contain credential paths or content.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .trusted_peer_paths import (
    ERROR_SCHEMA,
    NETWORK_LABELS,
    SHA256_RE,
    TrustedPeerPathError,
    TrustedPeerPathRegistry,
    _assert_no_reparse_components,
    _canonical_json,
    _comparison_path,
    _expect_int,
    _expect_keys,
    _expect_object,
    _expect_string,
    _is_link_or_reparse,
    _lexical_absolute,
    _parse_time,
    _read_json,
    _safe_host_id,
    _safe_id,
    _sha256,
    _validate_signature_ref,
    _within,
)

EXECUTOR_CONFIG_SCHEMA = "system-gap.trusted-peer-sftp-executor.config.v1"
GRANT_SCHEMA = "system-gap.trusted-peer-transfer-grant.v1"
RECEIPT_SCHEMA = "system-gap.trusted-peer-transfer-receipt.v1"
ATTEMPT_SCHEMA = "system-gap.trusted-peer-transfer-attempt.v1"


class TrustedPeerSftpError(TrustedPeerPathError):
    """Raised when an execution gate or transfer boundary fails closed."""


class SignatureVerifier(Protocol):
    """Verify canonical bytes without returning or exposing key material."""

    def __call__(
        self,
        payload: bytes,
        verifier: Mapping[str, Any],
        tool: Mapping[str, Any],
    ) -> None: ...


class DownloadTransport(Protocol):
    """Download exactly one remote file into an already-open local sink."""

    def __call__(
        self,
        endpoint: Mapping[str, Any],
        profile: Mapping[str, Any],
        remote_path: str,
        sink: Any,
        max_bytes: int,
        timeout_seconds: int,
        required_paramiko_version: str,
    ) -> int: ...


@dataclass(frozen=True)
class ExecutionResult:
    """A redacted local transfer receipt."""

    value: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.value))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _sha256_file(path: Path, *, maximum: int = 128 * 1024 * 1024) -> str:
    size = path.stat().st_size
    if size > maximum:
        raise TrustedPeerSftpError(f"pinned executable exceeds {maximum} bytes")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_local_file(path: Path, label: str) -> Path:
    _assert_no_reparse_components(path)
    try:
        before = path.lstat()
    except OSError as exc:
        raise TrustedPeerSftpError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(before.st_mode) or _is_link_or_reparse(path):
        raise TrustedPeerSftpError(f"{label} must be a regular non-link file")
    _assert_private_local_path(path, label)
    return path


def _windows_current_user_sid() -> str:
    from ctypes import wintypes

    token_query = 0x0008
    token_user = 1
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    handle = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(handle)
    ):
        raise TrustedPeerSftpError("cannot inspect the current Windows token")
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(
            handle, token_user, None, 0, ctypes.byref(needed)
        )
        if not needed.value:
            raise TrustedPeerSftpError("cannot size the current Windows token")
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            handle,
            token_user,
            buffer,
            needed,
            ctypes.byref(needed),
        ):
            raise TrustedPeerSftpError("cannot read the current Windows token")
        sid_pointer = ctypes.c_void_p.from_buffer(buffer).value
        return _windows_sid_string(sid_pointer)
    finally:
        kernel32.CloseHandle(handle)


def _windows_sid_string(sid_pointer: int | None) -> str:
    from ctypes import wintypes

    if not sid_pointer:
        raise TrustedPeerSftpError("Windows ACL contains an invalid SID")
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    value = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid_pointer), ctypes.byref(value)
    ):
        raise TrustedPeerSftpError("cannot convert a Windows ACL SID")
    try:
        return value.value
    finally:
        kernel32.LocalFree(value)


def _windows_acl(path: Path) -> tuple[str, set[str]]:
    """Return owner and basic allow-ACE SIDs; reject ambiguous ACL forms."""

    from ctypes import wintypes

    se_file_object = 1
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    access_allowed_ace_type = 0
    access_denied_ace_type = 1

    class ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    class ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.POINTER(ACL)),
        ctypes.POINTER(ctypes.POINTER(ACL)),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAce.argtypes = [
        ctypes.POINTER(ACL),
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    owner = ctypes.c_void_p()
    dacl = ctypes.POINTER(ACL)()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        se_file_object,
        owner_security_information | dacl_security_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0:
        raise TrustedPeerSftpError(f"cannot inspect Windows ACL for {path}")
    try:
        if not dacl:
            raise TrustedPeerSftpError("Windows path has an unrestricted null DACL")
        allowed: set[str] = set()
        for index in range(dacl.contents.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                raise TrustedPeerSftpError("cannot inspect a Windows ACL entry")
            header = ctypes.cast(ace_pointer, ctypes.POINTER(ACE_HEADER)).contents
            if header.AceType == access_denied_ace_type:
                continue
            if header.AceType != access_allowed_ace_type:
                raise TrustedPeerSftpError(
                    "Windows path uses an unsupported non-basic allow ACE"
                )
            sid_address = ace_pointer.value + ACCESS_ALLOWED_ACE.SidStart.offset
            allowed.add(_windows_sid_string(sid_address))
        return _windows_sid_string(owner.value), allowed
    finally:
        kernel32.LocalFree(descriptor)


def _assert_private_local_path(path: Path, label: str) -> None:
    """Require a local path to be private to user/admin/system principals."""

    _assert_no_reparse_components(path)
    mode = path.stat().st_mode
    if os.name != "nt":
        if mode & 0o077:
            raise TrustedPeerSftpError(
                f"{label} permissions must exclude group and other"
            )
        return
    owner, allowed = _windows_acl(path)
    current = _windows_current_user_sid()
    trusted = {current, "S-1-5-18", "S-1-5-32-544"}
    if owner not in {current, "S-1-5-32-544"}:
        raise TrustedPeerSftpError(f"{label} has an untrusted Windows owner")
    if not allowed or not allowed.issubset(trusted):
        raise TrustedPeerSftpError(f"{label} has an overbroad Windows DACL")


def _inside_any(path: Path, roots: Sequence[Path]) -> bool:
    return any(_within(path, root) for root in roots)


def _write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    _assert_no_reparse_components(path, include_leaf=False)
    if not path.parent.is_dir():
        raise TrustedPeerSftpError(f"local state directory is missing: {path.parent}")
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise TrustedPeerSftpError(f"one-shot state already exists: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _windows_directory_handle(path: Path) -> tuple[int, tuple[int, int]]:
    from ctypes import wintypes

    file_list_directory = 0x0001
    file_read_attributes = 0x0080
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    directory_attribute = 0x00000010
    reparse_attribute = 0x00000400

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
    ]
    handle = kernel32.CreateFileW(
        str(path),
        file_list_directory | file_read_attributes,
        share_all,
        None,
        open_existing,
        backup_semantics | open_reparse_point,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise TrustedPeerSftpError("cannot pin the destination directory")
    information = BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        kernel32.CloseHandle(handle)
        raise TrustedPeerSftpError("cannot inspect the pinned destination directory")
    if not information.dwFileAttributes & directory_attribute:
        kernel32.CloseHandle(handle)
        raise TrustedPeerSftpError("pinned destination parent is not a directory")
    if information.dwFileAttributes & reparse_attribute:
        kernel32.CloseHandle(handle)
        raise TrustedPeerSftpError("pinned destination parent is a reparse point")
    identity = (
        information.dwVolumeSerialNumber,
        (information.nFileIndexHigh << 32) | information.nFileIndexLow,
    )
    return int(handle), identity


def _windows_create_stage(path: Path) -> int:
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    delete_access = 0x00010000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    create_new = 1
    normal_attribute = 0x00000080
    temporary_attribute = 0x00000100
    open_reparse_point = 0x00200000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        generic_read | generic_write | delete_access,
        share_all,
        None,
        create_new,
        normal_attribute | temporary_attribute | open_reparse_point,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise TrustedPeerSftpError("cannot create exclusive staging file")
    try:
        return msvcrt.open_osfhandle(int(handle), os.O_BINARY | os.O_RDWR)
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def _windows_rename_handle_no_replace(
    descriptor: int, parent_handle: int, destination_name: str
) -> None:
    import msvcrt
    from ctypes import wintypes

    file_rename_information = 10
    error_file_exists = 80
    error_already_exists = 183

    class FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_long),
            ("Information", ctypes.c_size_t),
        ]

    if Path(destination_name).name != destination_name:
        raise TrustedPeerSftpError("destination name is not a single component")
    encoded = destination_name.encode("utf-16-le")
    offset = FILE_RENAME_INFO.FileName.offset
    # Win32 validates the complete structure size (including its aligned
    # one-WCHAR tail), not merely the byte offset plus the variable name.
    buffer = ctypes.create_string_buffer(ctypes.sizeof(FILE_RENAME_INFO) + len(encoded))
    info = ctypes.cast(buffer, ctypes.POINTER(FILE_RENAME_INFO)).contents
    info.ReplaceIfExists = False
    info.RootDirectory = wintypes.HANDLE(parent_handle)
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_int,
    ]
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
    source_handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    io_status = IO_STATUS_BLOCK()
    status = ntdll.NtSetInformationFile(
        source_handle,
        ctypes.byref(io_status),
        buffer,
        len(buffer),
        file_rename_information,
    )
    if status < 0:
        error = int(ntdll.RtlNtStatusToDosError(status))
        if error in {error_file_exists, error_already_exists}:
            raise TrustedPeerSftpError("destination appeared before commit")
        raise TrustedPeerSftpError(
            f"secure Windows no-replace commit failed with error {error}"
        )


class _PinnedDestinationDirectory:
    """Pin a private destination parent and commit relative to its handle."""

    def __init__(self, parent: Path) -> None:
        self.parent = parent
        self.handle: int | None = None
        self.identity: tuple[int, int] | None = None

    def __enter__(self) -> "_PinnedDestinationDirectory":
        _assert_no_reparse_components(self.parent)
        _assert_private_local_path(self.parent, "destination parent")
        if os.name == "nt":
            self.handle, self.identity = _windows_directory_handle(self.parent)
        else:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            self.handle = os.open(self.parent, flags)
            opened = os.fstat(self.handle)
            if not stat.S_ISDIR(opened.st_mode):
                raise TrustedPeerSftpError("pinned destination parent is not a directory")
            self.identity = (opened.st_dev, opened.st_ino)
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.handle is None:
            return
        if os.name == "nt":
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(wintypes.HANDLE(self.handle))
        else:
            os.close(self.handle)
        self.handle = None

    def create_stage(self) -> tuple[int, str, Path]:
        if self.handle is None:
            raise TrustedPeerSftpError("destination directory is not pinned")
        for _ in range(32):
            name = f".trusted-peer-sftp-{secrets.token_hex(16)}.part"
            path = self.parent / name
            try:
                if os.name == "nt":
                    from ctypes import wintypes

                    descriptor = _windows_create_stage(path)
                    comparison_handle, comparison_identity = _windows_directory_handle(
                        self.parent
                    )
                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel32.CloseHandle(wintypes.HANDLE(comparison_handle))
                    if comparison_identity != self.identity:
                        os.close(descriptor)
                        path.unlink(missing_ok=True)
                        raise TrustedPeerSftpError(
                            "destination parent changed before staging"
                        )
                else:
                    flags = (
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                    )
                    descriptor = os.open(name, flags, 0o600, dir_fd=self.handle)
                return descriptor, name, path
            except FileExistsError:
                continue
        raise TrustedPeerSftpError("cannot allocate a unique staging name")

    def assert_path_still_same(self) -> None:
        """Fail if the named parent no longer resolves to the pinned directory."""

        if self.handle is None or self.identity is None:
            raise TrustedPeerSftpError("destination directory is not pinned")
        _assert_no_reparse_components(self.parent)
        _assert_private_local_path(self.parent, "destination parent")
        if os.name == "nt":
            from ctypes import wintypes

            comparison_handle, comparison_identity = _windows_directory_handle(
                self.parent
            )
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(wintypes.HANDLE(comparison_handle))
        else:
            current = self.parent.stat(follow_symlinks=False)
            comparison_identity = (current.st_dev, current.st_ino)
        if comparison_identity != self.identity:
            raise TrustedPeerSftpError("destination parent changed during transfer")

    def commit(self, descriptor: int, staging_name: str, destination_name: str) -> None:
        if self.handle is None:
            raise TrustedPeerSftpError("destination directory is not pinned")
        if os.name == "nt":
            _windows_rename_handle_no_replace(
                descriptor, self.handle, destination_name
            )
            return
        try:
            os.link(
                staging_name,
                destination_name,
                src_dir_fd=self.handle,
                dst_dir_fd=self.handle,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise TrustedPeerSftpError("destination appeared before commit") from exc
        except OSError as exc:
            raise TrustedPeerSftpError(
                "filesystem cannot commit atomically without replacement"
            ) from exc
        os.unlink(staging_name, dir_fd=self.handle)

    def discard_open(
        self, descriptor: int, staging_name: str, staging_path: Path
    ) -> None:
        """Delete an uncommitted stage while its handle is still authoritative."""

        if os.name != "nt":
            if self.handle is None:
                raise TrustedPeerSftpError("destination directory is not pinned")
            os.unlink(staging_name, dir_fd=self.handle)
            return
        import msvcrt
        from ctypes import wintypes

        file_disposition_info = 4

        class FILE_DISPOSITION_INFO(ctypes.Structure):
            _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        disposition = FILE_DISPOSITION_INFO(True)
        source_handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        if not kernel32.SetFileInformationByHandle(
            source_handle,
            file_disposition_info,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise TrustedPeerSftpError(
                "cannot securely discard the uncommitted staging file"
            )

    def cleanup(self, staging_name: str, staging_path: Path) -> None:
        try:
            if os.name == "nt":
                staging_path.unlink(missing_ok=True)
            elif self.handle is not None:
                os.unlink(staging_name, dir_fd=self.handle)
        except OSError:
            pass


def _openssh_verify(
    payload: bytes,
    verifier: Mapping[str, Any],
    tool: Mapping[str, Any],
) -> None:
    executable = Path(str(tool["path"]))
    expected = str(tool["sha256"])
    if _sha256_file(executable) != expected:
        raise TrustedPeerSftpError("ssh-keygen executable hash mismatch")
    if verifier["algorithm"] != "external-ssh-signature":
        raise TrustedPeerSftpError(
            "live execution currently requires external-ssh-signature"
        )
    command = [
        str(executable),
        "-Y",
        "verify",
        "-f",
        str(verifier["allowed_signers_file"]),
        "-I",
        str(verifier["signer_identity"]),
        "-n",
        str(verifier["namespace"]),
        "-s",
        str(verifier["signature_file"]),
    ]
    safe_env = {"PATH": os.defpath}
    environment_allowlist = (
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
    )
    safe_env.update(
        {name: os.environ[name] for name in environment_allowlist if name in os.environ}
    )
    try:
        completed = subprocess.run(
            command,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
            shell=False,
            env=safe_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TrustedPeerSftpError("detached signature verifier failed") from exc
    if completed.returncode != 0:
        raise TrustedPeerSftpError("detached signature verification failed")


def _host_key_pin(key: Any) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _paramiko_download(
    endpoint: Mapping[str, Any],
    profile: Mapping[str, Any],
    remote_path: str,
    sink: Any,
    max_bytes: int,
    timeout_seconds: int,
    required_paramiko_version: str,
) -> int:
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise TrustedPeerSftpError(
            "install the trusted-peer-sftp optional dependency"
        ) from exc
    if paramiko.__version__ != required_paramiko_version:
        raise TrustedPeerSftpError(
            "installed Paramiko version does not match the host-local pin"
        )

    known_hosts = paramiko.HostKeys(str(profile["known_hosts_file"]))
    host = str(endpoint["host"])
    port = int(endpoint["port"])
    names = [host]
    if port != 22:
        names.insert(0, f"[{host}]:{port}")
    expected_pin = str(endpoint["known_host_pin"])
    expected_key = None
    for name in names:
        entries = known_hosts.lookup(name) or {}
        for key in entries.values():
            if _host_key_pin(key) == expected_pin:
                expected_key = key
                break
        if expected_key is not None:
            break
    if expected_key is None:
        raise TrustedPeerSftpError("known_hosts does not contain the pinned host key")

    try:
        private_key = paramiko.PKey.from_path(str(profile["identity_file"]))
    except Exception as exc:
        raise TrustedPeerSftpError("cannot load the configured identity file") from exc

    sock = None
    transport = None
    sftp = None
    try:
        sock = socket.create_connection(
            (host, port),
            timeout=timeout_seconds,
            source_address=(str(profile["source_address"]), 0),
        )
        transport = paramiko.Transport(sock)
        transport.banner_timeout = timeout_seconds
        transport.auth_timeout = timeout_seconds
        transport.connect(
            hostkey=expected_key,
            username=str(endpoint["username"]),
            pkey=private_key,
        )
        sftp = paramiko.SFTPClient.from_transport(transport)
        attrs = sftp.lstat(remote_path)
        if not stat.S_ISREG(attrs.st_mode):
            raise TrustedPeerSftpError("remote object is not a regular file")
        if attrs.st_size is None or attrs.st_size < 0:
            raise TrustedPeerSftpError("remote file size is unavailable")
        if attrs.st_size > max_bytes:
            raise TrustedPeerSftpError("remote file exceeds the transfer limit")
        total = 0
        with sftp.open(remote_path, "rb") as source:
            while True:
                chunk = source.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise TrustedPeerSftpError("remote stream exceeds the transfer limit")
                sink.write(chunk)
        if total != attrs.st_size:
            raise TrustedPeerSftpError("remote file changed size during transfer")
        return total
    except TrustedPeerSftpError:
        raise
    except Exception as exc:
        raise TrustedPeerSftpError("SFTP transfer failed") from exc
    finally:
        if sftp is not None:
            sftp.close()
        if transport is not None:
            transport.close()
        elif sock is not None:
            sock.close()


class TrustedPeerSftpExecutor:
    """Execute a single signed, one-shot trusted-peer pull."""

    def __init__(
        self,
        planner: TrustedPeerPathRegistry,
        config: Mapping[str, Any],
        *,
        clock: Callable[[], datetime] | None = None,
        signature_verifier: SignatureVerifier | None = None,
        transport: DownloadTransport | None = None,
    ) -> None:
        self.planner = planner
        self._clock = clock or _utc_now
        self._signature_verifier = signature_verifier or _openssh_verify
        self._transport = transport or _paramiko_download
        raw = _expect_object(config, "executor config")
        _expect_keys(
            raw,
            required={
                "schema",
                "state_root",
                "receipt_root",
                "credential_roots",
                "ssh_keygen",
                "required_paramiko_version",
                "auth_profiles",
                "signature_verifiers",
                "max_transfer_bytes",
                "max_grant_ttl_seconds",
                "connect_timeout_seconds",
            },
            label="executor config",
        )
        if raw["schema"] != EXECUTOR_CONFIG_SCHEMA:
            raise TrustedPeerSftpError(
                f"executor config.schema must be {EXECUTOR_CONFIG_SCHEMA}"
            )
        self.state_root = self._local_directory(raw["state_root"], "state_root")
        self.receipt_root = self._local_directory(
            raw["receipt_root"], "receipt_root"
        )
        if _comparison_path(self.state_root) == _comparison_path(self.receipt_root):
            raise TrustedPeerSftpError("state_root and receipt_root must be distinct")
        roots = raw["credential_roots"]
        if not isinstance(roots, list) or not roots:
            raise TrustedPeerSftpError("credential_roots must be non-empty")
        self.credential_roots = tuple(
            self._local_directory(value, f"credential_roots[{index}]")
            for index, value in enumerate(roots)
        )
        self.max_transfer_bytes = _expect_int(
            raw["max_transfer_bytes"], "max_transfer_bytes", 1, 2**40
        )
        self.max_grant_ttl_seconds = _expect_int(
            raw["max_grant_ttl_seconds"], "max_grant_ttl_seconds", 1, 86400
        )
        self.connect_timeout_seconds = _expect_int(
            raw["connect_timeout_seconds"], "connect_timeout_seconds", 1, 300
        )
        self.required_paramiko_version = _expect_string(
            raw["required_paramiko_version"], "required_paramiko_version", 32
        )
        self.ssh_keygen = self._load_tool(raw["ssh_keygen"])
        self.auth_profiles = self._load_auth_profiles(raw["auth_profiles"])
        self.signature_verifiers = self._load_signature_verifiers(
            raw["signature_verifiers"]
        )
        for index, destination_root in enumerate(self.planner.destination_roots):
            _assert_private_local_path(
                destination_root, f"planner.destination_roots[{index}]"
            )

    @classmethod
    def from_files(
        cls,
        planner_config: str | Path,
        executor_config: str | Path,
        **kwargs: Any,
    ) -> "TrustedPeerSftpExecutor":
        planner = TrustedPeerPathRegistry.from_file(planner_config, clock=kwargs.get("clock"))
        config_path = _lexical_absolute(executor_config, "executor config path")
        return cls(planner, _read_json(config_path, "executor config"), **kwargs)

    def _local_directory(self, value: Any, label: str) -> Path:
        path = _lexical_absolute(value, label)
        _assert_no_reparse_components(path)
        if not path.is_dir() or _is_link_or_reparse(path):
            raise TrustedPeerSftpError(f"{label} must be an existing local directory")
        if _within(path, self.planner.yard_root) or _within(
            self.planner.yard_root, path
        ):
            raise TrustedPeerSftpError(f"{label} must be outside the sync yard")
        _assert_private_local_path(path, label)
        return path

    def _credential_file(self, value: Any, label: str) -> Path:
        path = _strict_local_file(_lexical_absolute(value, label), label)
        if not _inside_any(path, self.credential_roots):
            raise TrustedPeerSftpError(f"{label} is outside credential_roots")
        _assert_private_local_path(path, label)
        return path

    def _load_tool(self, value: Any) -> dict[str, Any]:
        raw = _expect_object(value, "ssh_keygen")
        _expect_keys(raw, required={"path", "sha256"}, label="ssh_keygen")
        path = _strict_local_file(
            _lexical_absolute(raw["path"], "ssh_keygen.path"), "ssh_keygen.path"
        )
        digest = _expect_string(raw["sha256"], "ssh_keygen.sha256", 64)
        if not SHA256_RE.fullmatch(digest):
            raise TrustedPeerSftpError("ssh_keygen.sha256 is invalid")
        return {"path": str(path), "sha256": digest}

    def _load_auth_profiles(self, value: Any) -> dict[tuple[str, str, str], dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise TrustedPeerSftpError("auth_profiles must be non-empty")
        result: dict[tuple[str, str, str], dict[str, Any]] = {}
        for index, item in enumerate(value):
            label = f"auth_profiles[{index}]"
            raw = _expect_object(item, label)
            _expect_keys(
                raw,
                required={
                    "host_id",
                    "endpoint_id",
                    "username",
                    "identity_file",
                    "known_hosts_file",
                    "remote_account_mode",
                    "network_label",
                    "remote_host",
                    "source_address",
                    "allowed_remote_cidrs",
                    "allowed_source_cidrs",
                },
                label=label,
            )
            host_id = _safe_host_id(raw["host_id"], f"{label}.host_id")
            endpoint_id = _safe_id(raw["endpoint_id"], f"{label}.endpoint_id")
            username = _expect_string(raw["username"], f"{label}.username", 64)
            if raw["remote_account_mode"] != "read-only":
                raise TrustedPeerSftpError(f"{label}.remote_account_mode must be read-only")
            network_label = _expect_string(
                raw["network_label"], f"{label}.network_label", 32
            )
            if network_label not in NETWORK_LABELS:
                raise TrustedPeerSftpError(f"{label}.network_label is invalid")
            remote_host = _expect_string(
                raw["remote_host"], f"{label}.remote_host", 253
            )
            try:
                remote_address = ipaddress.ip_address(remote_host)
                source_address = ipaddress.ip_address(
                    _expect_string(
                        raw["source_address"], f"{label}.source_address", 64
                    )
                )
            except ValueError as exc:
                raise TrustedPeerSftpError(
                    f"{label} route endpoints must be literal IP addresses"
                ) from exc
            remote_cidrs = self._cidrs(
                raw["allowed_remote_cidrs"], f"{label}.allowed_remote_cidrs"
            )
            source_cidrs = self._cidrs(
                raw["allowed_source_cidrs"], f"{label}.allowed_source_cidrs"
            )
            if not any(remote_address in network for network in remote_cidrs):
                raise TrustedPeerSftpError(f"{label}.remote_host is outside route policy")
            if not any(source_address in network for network in source_cidrs):
                raise TrustedPeerSftpError(f"{label}.source_address is outside route policy")
            if source_address.is_unspecified or source_address.is_multicast:
                raise TrustedPeerSftpError(f"{label}.source_address is not bindable")
            key = (host_id, endpoint_id, username)
            if key in result:
                raise TrustedPeerSftpError("duplicate auth profile")
            result[key] = {
                "identity_file": str(
                    self._credential_file(raw["identity_file"], f"{label}.identity_file")
                ),
                "known_hosts_file": str(
                    self._credential_file(
                        raw["known_hosts_file"], f"{label}.known_hosts_file"
                    )
                ),
                "remote_account_mode": "read-only",
                "network_label": network_label,
                "remote_host": str(remote_address),
                "source_address": str(source_address),
                "allowed_remote_cidrs": tuple(str(network) for network in remote_cidrs),
                "allowed_source_cidrs": tuple(str(network) for network in source_cidrs),
            }
        return result

    @staticmethod
    def _cidrs(value: Any, label: str) -> tuple[Any, ...]:
        if not isinstance(value, list) or not value:
            raise TrustedPeerSftpError(f"{label} must be a non-empty array")
        result = []
        for index, raw in enumerate(value):
            text = _expect_string(raw, f"{label}[{index}]", 64)
            try:
                result.append(ipaddress.ip_network(text, strict=True))
            except ValueError as exc:
                raise TrustedPeerSftpError(f"{label}[{index}] is invalid") from exc
        if len({str(item) for item in result}) != len(result):
            raise TrustedPeerSftpError(f"{label} has duplicates")
        return tuple(result)

    def _load_signature_verifiers(self, value: Any) -> dict[tuple[str, str, str], dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise TrustedPeerSftpError("signature_verifiers must be non-empty")
        result: dict[tuple[str, str, str], dict[str, Any]] = {}
        for index, item in enumerate(value):
            label = f"signature_verifiers[{index}]"
            raw = _expect_object(item, label)
            _expect_keys(
                raw,
                required={
                    "purpose",
                    "algorithm",
                    "key_id",
                    "reference",
                    "signer_identity",
                    "namespace",
                    "signature_file",
                    "allowed_signers_file",
                },
                label=label,
            )
            purpose = _expect_string(raw["purpose"], f"{label}.purpose", 32)
            if purpose not in {"registry", "grant"}:
                raise TrustedPeerSftpError(f"{label}.purpose is invalid")
            algorithm = _expect_string(raw["algorithm"], f"{label}.algorithm", 64)
            if algorithm != "external-ssh-signature":
                raise TrustedPeerSftpError(
                    f"{label}.algorithm must be external-ssh-signature"
                )
            key_id = _safe_id(raw["key_id"], f"{label}.key_id")
            reference = _validate_signature_ref(raw["reference"], f"{label}.reference")
            key = (purpose, key_id, reference)
            if key in result:
                raise TrustedPeerSftpError("duplicate signature verifier")
            namespace = _expect_string(raw["namespace"], f"{label}.namespace", 64)
            expected_namespace = (
                "system-gap-registry" if purpose == "registry" else "system-gap-transfer-grant"
            )
            if namespace != expected_namespace:
                raise TrustedPeerSftpError(f"{label}.namespace is not purpose-bound")
            result[key] = {
                "purpose": purpose,
                "algorithm": algorithm,
                "key_id": key_id,
                "reference": reference,
                "signer_identity": _expect_string(
                    raw["signer_identity"], f"{label}.signer_identity", 128
                ),
                "namespace": namespace,
                "signature_file": str(
                    self._credential_file(
                        raw["signature_file"], f"{label}.signature_file"
                    )
                ),
                "allowed_signers_file": str(
                    self._credential_file(
                        raw["allowed_signers_file"],
                        f"{label}.allowed_signers_file",
                    )
                ),
            }
        return result

    def _verify_signed_object(
        self,
        value: Mapping[str, Any],
        *,
        purpose: str,
    ) -> None:
        signature = _expect_object(value["signature_reference"], "signature_reference")
        _expect_keys(
            signature,
            required={"algorithm", "key_id", "ref", "payload_sha256"},
            label="signature_reference",
        )
        algorithm = _expect_string(signature["algorithm"], "signature.algorithm", 64)
        key_id = _safe_id(signature["key_id"], "signature.key_id")
        reference = _validate_signature_ref(signature["ref"], "signature.ref")
        digest = _expect_string(signature["payload_sha256"], "signature.payload_sha256", 64)
        unsigned = dict(value)
        del unsigned["signature_reference"]
        payload = _canonical_json(unsigned)
        if not SHA256_RE.fullmatch(digest) or _sha256(payload) != digest:
            raise TrustedPeerSftpError("signed payload digest mismatch")
        verifier = self.signature_verifiers.get((purpose, key_id, reference))
        if verifier is None or verifier["algorithm"] != algorithm:
            raise TrustedPeerSftpError("no exact host-local signature verifier is pinned")
        for index, root in enumerate(self.credential_roots):
            _assert_private_local_path(root, f"credential_roots[{index}]")
        self._credential_file(
            verifier["signature_file"], f"{purpose} signature_file"
        )
        self._credential_file(
            verifier["allowed_signers_file"], f"{purpose} allowed_signers_file"
        )
        executable = _strict_local_file(
            Path(self.ssh_keygen["path"]), "ssh_keygen.path"
        )
        if _sha256_file(executable) != self.ssh_keygen["sha256"]:
            raise TrustedPeerSftpError("ssh-keygen executable hash mismatch")
        self._signature_verifier(payload, verifier, self.ssh_keygen)

    def _load_grant(self, authorization: str | Path) -> dict[str, Any]:
        path = _strict_local_file(
            _lexical_absolute(authorization, "authorization path"),
            "authorization grant",
        )
        if _within(path, self.planner.yard_root):
            raise TrustedPeerSftpError("authorization grant must be host-local")
        grant = _read_json(path, "authorization grant")
        _expect_keys(
            grant,
            required={
                "schema",
                "grant_id",
                "host_id",
                "peer_id",
                "endpoint_id",
                "path_id",
                "destination",
                "network_label",
                "registry_sha256",
                "registry_revision",
                "plan_id",
                "not_before",
                "expires_at",
                "one_shot_id",
                "max_bytes",
                "signature_reference",
            },
            label="authorization grant",
        )
        if grant["schema"] != GRANT_SCHEMA:
            raise TrustedPeerSftpError(f"grant.schema must be {GRANT_SCHEMA}")
        grant["grant_id"] = _safe_id(grant["grant_id"], "grant.grant_id")
        grant["host_id"] = _safe_host_id(grant["host_id"], "grant.host_id")
        grant["peer_id"] = _safe_host_id(grant["peer_id"], "grant.peer_id")
        grant["endpoint_id"] = _safe_id(grant["endpoint_id"], "grant.endpoint_id")
        grant["path_id"] = _safe_id(grant["path_id"], "grant.path_id")
        grant["one_shot_id"] = _safe_id(grant["one_shot_id"], "grant.one_shot_id")
        grant["destination"] = str(
            _lexical_absolute(grant["destination"], "grant.destination")
        )
        network = _expect_string(grant["network_label"], "grant.network_label", 32)
        if network not in NETWORK_LABELS:
            raise TrustedPeerSftpError("grant.network_label is invalid")
        registry_hash = _expect_string(
            grant["registry_sha256"], "grant.registry_sha256", 64
        )
        if not SHA256_RE.fullmatch(registry_hash):
            raise TrustedPeerSftpError("grant.registry_sha256 is invalid")
        grant["registry_revision"] = _expect_int(
            grant["registry_revision"], "grant.registry_revision", 1, 2**63 - 1
        )
        plan_id = _expect_string(grant["plan_id"], "grant.plan_id", 71)
        if not plan_id.startswith("sha256:") or not SHA256_RE.fullmatch(plan_id[7:]):
            raise TrustedPeerSftpError("grant.plan_id is invalid")
        grant["max_bytes"] = _expect_int(
            grant["max_bytes"], "grant.max_bytes", 1, self.max_transfer_bytes
        )
        not_before = _parse_time(grant["not_before"], "grant.not_before")
        expires_at = _parse_time(grant["expires_at"], "grant.expires_at")
        now = self._clock().astimezone(timezone.utc)
        if not_before > now or expires_at <= now:
            raise TrustedPeerSftpError("authorization grant is not currently valid")
        lifetime = (expires_at - not_before).total_seconds()
        if lifetime <= 0 or lifetime > self.max_grant_ttl_seconds:
            raise TrustedPeerSftpError("authorization grant lifetime exceeds policy")
        self._verify_signed_object(grant, purpose="grant")
        return grant

    def _verify_registry_signature(self, host_id: str, expected_sha256: str) -> None:
        registry_path = self.planner._registry_path(host_id)
        registry = _read_json(registry_path, "registry")
        if _sha256(_canonical_json(registry)) != expected_sha256:
            raise TrustedPeerSftpError(
                "signed registry snapshot does not match the pull plan"
            )
        self._verify_signed_object(registry, purpose="registry")

    def _reserve_attempt(self, grant: Mapping[str, Any], plan_id: str) -> Path:
        attempt_key = _sha256(
            f"{grant['one_shot_id']}\0{plan_id}\0{grant['destination']}".encode("utf-8")
        )
        attempts = self.state_root / "attempts"
        _assert_private_local_path(self.state_root, "state_root")
        if not attempts.is_dir() or _is_link_or_reparse(attempts):
            raise TrustedPeerSftpError("state_root/attempts must already exist")
        _assert_private_local_path(attempts, "state_root/attempts")
        _assert_private_local_path(self.receipt_root, "receipt_root")
        path = attempts / f"{attempt_key}.json"
        _write_exclusive_json(
            path,
            {
                "schema": ATTEMPT_SCHEMA,
                "attempt_id": attempt_key,
                "grant_id": grant["grant_id"],
                "one_shot_id": grant["one_shot_id"],
                "plan_id": plan_id,
                "destination": grant["destination"],
                "started_at": _timestamp(self._clock()),
                "status": "reserved",
            },
        )
        return path

    def _write_receipt(self, receipt: Mapping[str, Any]) -> Path:
        _assert_private_local_path(self.receipt_root, "receipt_root")
        receipt_key = _sha256(
            f"{receipt['grant_id']}\0{receipt['plan_id']}".encode("utf-8")
        )
        path = self.receipt_root / f"{receipt_key}.json"
        _write_exclusive_json(path, receipt)
        return path

    def execute(
        self,
        host_id: str,
        path_id: str,
        destination: str | Path,
        authorization: str | Path,
    ) -> ExecutionResult:
        destination_path = self.planner._destination(destination)
        plan = self.planner.pull_plan(host_id, path_id, destination_path).as_dict()
        grant = self._load_grant(authorization)
        endpoint = plan["endpoint"]
        source = plan["source"]
        exact = {
            "host_id": plan["registry"]["host_id"],
            "peer_id": plan["peer_id"],
            "endpoint_id": endpoint["endpoint_id"],
            "path_id": source["path_id"],
            "destination": plan["destination"],
            "network_label": endpoint["network_label"],
            "registry_sha256": plan["registry"]["sha256"],
            "registry_revision": plan["registry"]["revision"],
            "plan_id": plan["plan_id"],
        }
        for field, expected in exact.items():
            if grant[field] != expected:
                raise TrustedPeerSftpError(f"authorization grant does not bind {field}")
        self._verify_registry_signature(
            exact["host_id"], exact["registry_sha256"]
        )
        profile_key = (
            exact["host_id"],
            exact["endpoint_id"],
            endpoint["username"],
        )
        profile = self.auth_profiles.get(profile_key)
        if profile is None:
            raise TrustedPeerSftpError("no exact host-local auth profile is pinned")
        if (
            profile["network_label"] != endpoint["network_label"]
            or profile["remote_host"] != endpoint["host"]
        ):
            raise TrustedPeerSftpError(
                "host-local route profile does not match the planned endpoint"
            )

        # Revalidate every local boundary after all signatures and immediately
        # before the irreversible one-shot reservation/network attempt.
        destination_path = self.planner._destination(destination_path)
        for index, root in enumerate(self.credential_roots):
            _assert_private_local_path(root, f"credential_roots[{index}]")
        self._credential_file(profile["identity_file"], "identity_file")
        self._credential_file(profile["known_hosts_file"], "known_hosts_file")
        with _PinnedDestinationDirectory(destination_path.parent) as pinned:
            self._reserve_attempt(grant, plan["plan_id"])
            started_at = self._clock()
            staging_name: str | None = None
            staging_path: Path | None = None
            content_hash = hashlib.sha256()
            total = 0
            try:
                descriptor, staging_name, staging_path = pinned.create_stage()
                with os.fdopen(descriptor, "w+b", closefd=True) as raw_sink:
                    try:
                        class HashingSink:
                            def write(self, chunk: bytes) -> int:
                                content_hash.update(chunk)
                                return raw_sink.write(chunk)

                        total = self._transport(
                            endpoint,
                            profile,
                            source["remote_path"],
                            HashingSink(),
                            min(grant["max_bytes"], self.max_transfer_bytes),
                            self.connect_timeout_seconds,
                            self.required_paramiko_version,
                        )
                        raw_sink.flush()
                        os.fsync(raw_sink.fileno())
                        if total != os.fstat(raw_sink.fileno()).st_size:
                            raise TrustedPeerSftpError(
                                "transport byte count does not match staging file"
                            )
                        pinned.assert_path_still_same()
                        pinned.commit(
                            raw_sink.fileno(), staging_name, destination_path.name
                        )
                        staging_name = None
                        staging_path = None
                    except Exception:
                        pinned.discard_open(
                            raw_sink.fileno(), staging_name, staging_path
                        )
                        staging_name = None
                        staging_path = None
                        raise
                finished_at = self._clock()
                receipt = {
                    "schema": RECEIPT_SCHEMA,
                    "status": "succeeded",
                    "grant_id": grant["grant_id"],
                    "one_shot_id": grant["one_shot_id"],
                    "plan_id": plan["plan_id"],
                    "registry_sha256": exact["registry_sha256"],
                    "registry_revision": exact["registry_revision"],
                    "host_id": exact["host_id"],
                    "peer_id": exact["peer_id"],
                    "endpoint_id": exact["endpoint_id"],
                    "host_key_pin": endpoint["known_host_pin"],
                    "network_label": exact["network_label"],
                    "path_id": exact["path_id"],
                    "destination": str(destination_path),
                    "bytes": total,
                    "sha256": content_hash.hexdigest(),
                    "started_at": _timestamp(started_at),
                    "finished_at": _timestamp(finished_at),
                    "credential_paths_included": False,
                    "content_included": False,
                }
                self._write_receipt(receipt)
                return ExecutionResult(receipt)
            except Exception:
                # Once the one-shot reservation exists, every outcome gets a
                # local redacted receipt.  The generic code deliberately avoids
                # leaking remote content, library exceptions or credential paths.
                failed_receipt = {
                    "schema": RECEIPT_SCHEMA,
                    "status": "failed",
                    "error_code": "transfer-failed",
                    "grant_id": grant["grant_id"],
                    "one_shot_id": grant["one_shot_id"],
                    "plan_id": plan["plan_id"],
                    "registry_sha256": exact["registry_sha256"],
                    "registry_revision": exact["registry_revision"],
                    "host_id": exact["host_id"],
                    "peer_id": exact["peer_id"],
                    "endpoint_id": exact["endpoint_id"],
                    "host_key_pin": endpoint["known_host_pin"],
                    "network_label": exact["network_label"],
                    "path_id": exact["path_id"],
                    "destination": str(destination_path),
                    "started_at": _timestamp(started_at),
                    "finished_at": _timestamp(self._clock()),
                    "credential_paths_included": False,
                    "content_included": False,
                }
                try:
                    self._write_receipt(failed_receipt)
                except TrustedPeerPathError:
                    pass
                raise
            finally:
                if staging_name is not None and staging_path is not None:
                    pinned.cleanup(staging_name, staging_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trusted-peer-sftp-executor",
        description="Execute one signed, no-overwrite trusted-peer SFTP pull.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    execute = subparsers.add_parser("execute")
    execute.add_argument("--registry-config", required=True)
    execute.add_argument("--executor-config", required=True)
    execute.add_argument("--host-id", required=True)
    execute.add_argument("--path-id", required=True)
    execute.add_argument("--destination", required=True)
    execute.add_argument("--authorization", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        executor = TrustedPeerSftpExecutor.from_files(
            args.registry_config, args.executor_config
        )
        result = executor.execute(
            args.host_id, args.path_id, args.destination, args.authorization
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))
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
