# Trusted-peer SFTP executor

The `trusted-peer-paths` CLI remains a network-free metadata planner. The
optional `trusted-peer-sftp-executor` is the separately reviewed execution
boundary for a single ordinary file. Installing or configuring it does not
enable a scheduler and does not create keys, signatures, accounts or routes.

## Required trust material

All material below is provisioned out of band and kept outside the sync yard:

- a host-local planner configuration;
- an executor configuration under
  `system-gap.trusted-peer-sftp-executor.config.v1`;
- an SSH identity restricted by the remote server to read-only access;
- a known-hosts file containing the exact pinned server key;
- an allowed-signers file and detached OpenSSH signatures;
- a signed, short-lived `system-gap.trusted-peer-transfer-grant.v1` grant;
- existing host-local state, receipt, credential and destination directories.

The executor configuration binds auth to the exact tuple
`host_id + endpoint_id + username`. The yard cannot choose any local key,
known-hosts file, verifier, destination root, executable or receipt location.
The `ssh-keygen` binary is absolute-path and SHA-256 pinned. Paramiko is an
opt-in dependency and its exact installed version is pinned locally.

Every auth profile also binds the registry network label to an exact literal
remote IP, an exact local source IP and explicit source/remote CIDRs. The socket
is bound to that source address before connecting. A `private-overlay` label is
therefore an enforced route policy, not a descriptive string or DNS choice.

## Signed payloads

The registry and grant use canonical UTF-8 JSON: sorted keys, compact
separators, no NaN, and the entire `signature_reference` object removed before
hashing/signing. The digest is stored in `payload_sha256`.

OpenSSH signatures use purpose-specific namespaces:

- registry: `system-gap-registry`
- grant: `system-gap-transfer-grant`

The executor runs the pinned equivalent of:

```bash
ssh-keygen -Y verify -f ALLOWED_SIGNERS -I SIGNER \
  -n NAMESPACE -s DETACHED_SIGNATURE
```

Canonical payload bytes are provided on standard input. The signature file and
allowed-signers path are resolved only from the host-local executor config.

## One-shot grant binding

A grant binds all of the following:

- source host and receiving peer;
- endpoint, path ID and network label;
- exact destination;
- registry SHA-256 and revision;
- deterministic pull-plan ID;
- not-before, expiry and maximum byte count;
- grant ID and one-shot ID.

The maximum grant lifetime is host-local policy and cannot exceed 24 hours.
Before any network access the executor creates an exclusive attempt record
derived from one-shot ID, plan ID and destination. A failed attempt consumes
the grant; retry requires a new grant.

## Transfer boundary

After every non-network gate passes, the Paramiko adapter:

1. loads the expected host key from the local known-hosts file;
2. requires its SHA-256 pin to equal the registry/plan pin;
3. connects with that exact host key and the locally bound identity;
4. uses SFTP `lstat` and rejects anything except a regular file;
5. enforces the grant/config size limit before and during streaming;
6. writes into a mode-0600 exclusive staging file;
7. verifies byte count and fsyncs;
8. revalidates the pinned destination parent and commits relative to its open
   handle with a platform-specific no-replace primitive;
9. never replaces an existing destination.

There is no shell, SCP, remote command, upload, rename, delete, directory walk,
SQLite transfer, accept-new host key, password or interactive prompt path.

## Local state and receipts

`state_root/attempts/` must exist before execution. Every attempted one-shot
transfer reserves one immutable JSON record. Every post-reservation outcome
creates one redacted receipt in `receipt_root`; a successful receipt additionally
contains size and content SHA-256, while a failed receipt exposes only a generic
failure code. Receipts never contain credential paths, credential values,
private keys or file content and are not written to `.SYNC`.

The schemas and neutral examples are under `schemas/` and `examples/`.

German version:
[`trusted-peer-sftp-executor_de.md`](trusted-peer-sftp-executor_de.md).
