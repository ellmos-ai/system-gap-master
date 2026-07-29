# Trusted peer path registry

The trusted peer path registry lets pre-authorized machines discover exact
paths and pull ordinary files directly over SFTP on a Tailscale or LAN
connection. The yard carries only a signed directory of locations,
endpoints and peer permissions. It never relays the referenced file, a
credential value, an HMAC key or an SSH private key.

No request-time coordination is required. After one-time trust and SSH
server setup, each host publishes only its own slot and each authorized peer
can independently verify, resolve and pull.

## Use cases

- Publish the exact host-local location of a credential file so an authorized
  recovery machine can pull it through a read-only SSH account.
- Publish an ordinary configuration, certificate bundle or export file that
  peers need without copying its content into the semi-trusted yard.
- Advertise the location of SQLite state for discovery while forcing the
  actual transfer through the R9 `sqlite-transit-sync` snapshot adapter.
- Give several agent runtimes the same verified, machine-readable pull plan
  without giving them a shared shell script or permission to edit foreign
  host slots.

## Files and ownership

Each publisher writes exactly one derived location:

```text
<YARD>/hosts/<LOCAL_HOST_ID>/trusted-peer-paths/registry.json
```

`publish` does not accept an output path or a host override. The destination
comes from the host-local config, and the registry `host_id` must match its
slot. Other hosts may read the file but never update it.

Keep these items outside the synced yard:

- local config and source entries;
- signing/verification key files;
- SSH `known_hosts` and private authentication material;
- revision pins, pull staging files and validation state.

The public schemas live under [`schemas/`](../schemas/). Copy the examples in
[`examples/`](../examples/) to a host-local directory, replace every path and
identity, then protect that directory with operating-system permissions.

## Authenticity and replay protection

Every registry has a canonical JSON HMAC-SHA256 signature and a `key_id`.
The signing key is read through `publisher.signing_key_ref`; peers pin the
same out-of-band provisioned key through
`trusted_hosts[].verification_key_ref`. The key bytes never enter the yard
or command output.

HMAC is symmetric: every verifier holding a host's verification key could
forge that host. Use a distinct high-entropy key per publisher and distribute
it only to that host's trusted peers. Rotate by provisioning a new local key,
changing `key_id`, and updating peer trust pins before publishing.

Peers set a bootstrap `min_revision`. After a valid read, the CLI stores the
highest revision and document digest in the configured host-local
`state_dir`. Older signed documents and a different document at the same
revision fail closed as replay or equivocation. A publisher must increment
`revision`; it cannot overwrite a newer or unverifiable yard document.
Crash-released host-local OS locks serialize revision-pin and publish
updates, so concurrent agents cannot move the highest-seen state backwards.

## Published fields

Each path has:

- `path_id`: stable, path-neutral identifier;
- `kind`: `file`, `directory`, or `database/sqlite`;
- `local_path`: the exact absolute path on the publishing host;
- `remote_path`: the exact absolute path exposed by the SFTP subsystem;
- `endpoint_id`: one signed SFTP endpoint;
- `allowed_peer_ids`: peers permitted to resolve or plan this path;
- `direct_pull`: explicit publisher decision;
- optional `adapter` and `description`.

Exact credential paths are allowed because the registry exists to publish
locations. Treat path names as metadata that all yard readers can see.
Credential values and file bytes remain forbidden.

The peer allowlist controls this CLI's resolve/pull boundary; the SSH server
must separately enforce authentication, read-only filesystem permissions and
the intended network boundary. A signed registry is not an SSH ACL.

## CLI

Install the package, create the host-local config and entries files, then:

```bash
trusted-peer-paths publish \
  --config /host-local/trusted-peer-paths.local.json \
  --entries /host-local/trusted-peer-paths.entries.local.json

trusted-peer-paths validate \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-B

trusted-peer-paths list \
  --config /host-local/trusted-peer-paths.local.json

trusted-peer-paths resolve \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-B --path-id service-credential-file

trusted-peer-paths pull-plan \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-B --path-id service-credential-file \
  --destination /allowed/imports/credentials.json

# Dry-run unless --apply is explicit:
trusted-peer-paths pull \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-B --path-id service-credential-file \
  --destination /allowed/imports/credentials.json

trusted-peer-paths pull \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-B --path-id service-credential-file \
  --destination /allowed/imports/credentials.json --apply
```

`publish`, validation state and registry replacement use temporary files,
fsync and atomic replacement. `list` returns only paths authorized for the
configured `local_peer_id`. `resolve`, `pull-plan` and `pull` fail on
untrusted hosts, invalid signatures, replayed revisions, unknown transports,
traversal, unauthorized peers and malformed path data.

## SFTP pull boundary

Direct execution is deliberately narrow:

- `transport=sftp` on `network=tailscale|lan`;
- regular files only, with `direct_pull=true`;
- OpenSSH `sftp` invoked as an argument vector with `shell=False`;
- an exact host-local `sftp_executable_ref`, not a PATH lookup, and no
  user/system SSH config (`-F none`);
- batch mode, strict host-key checking and the exact host-local
  `known_hosts_ref`;
- conservative non-globbing remote paths and validated endpoint/user/port;
- absolute destination inside a configured `pull_destination_root`;
- no symlink, junction or reparse destination component;
- unique host-local staging plus atomic no-overwrite installation;
- no stdout/stderr content is returned or logged.

`pull` is a dry-run without `--apply`. Existing destinations always block.
Directories produce a verified plan with
`directory-pull-requires-reviewed-adapter`; this release does not recursively
copy them.

## SQLite boundary (R9)

Live SQLite databases and their `-wal`/`-shm` companions may be listed for
discovery, but must use:

```json
{
  "kind": "database/sqlite",
  "direct_pull": false,
  "adapter": "sqlite-transit-sync"
}
```

Any `.db`, `.sqlite`, `.sqlite3`, `-wal` or `-shm` path disguised as an
ordinary file is rejected. `pull-plan` and `pull --apply` remain blocked and
point to the R9 `db-transit/<namespace>` snapshot workflow. This module does
not implement database synchronization.
