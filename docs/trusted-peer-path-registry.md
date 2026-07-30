# Trusted-peer path registry: read-only preparation

`trusted-peer-paths` validates cloud-safe path metadata and produces a
deterministic pull-preparation receipt. It is intentionally **not a transfer
client**. The module does not publish a registry, open a socket, invoke
SSH/SFTP, read a credential/key/signature/known-hosts file, copy referenced
bytes, or create the destination.

The distinction is part of the contract:

- a registry may contain an exact approved SFTP path;
- it must state `metadata_type=path-location` and
  `content_included=false`;
- credential values, file content, private keys, tokens and passwords are
  rejected;
- a signature *reference*, payload digest and known-host pin are metadata,
  not proof that an external verifier or SSH client has used them.

This is the V4 preflight boundary. Real two-host activation remains a separate
reviewed change.

## Ownership and files

The validator derives one read location; callers cannot override it:

```text
<YARD>/hosts/<TRUSTED_HOST_ID>/trusted-peer-paths/registry.json
```

The document's `host_id` must match that slot. The CLI has no `publish`,
`pull`, `--apply` or `--output` operation. Results go to stdout. Therefore
this capability never edits the yard or another host slot.

The local policy stays outside the yard. It contains only trust metadata:

- local host and peer IDs;
- exact trusted host ID and minimum revision;
- pinned signature algorithm, key ID and signature-reference URN;
- exact remote-path allowlist;
- allowed `direct` and/or `private-overlay` network labels;
- endpoint-to-known-host SHA-256 pin mapping;
- host-local destination roots and registry age/TTL limits.

It contains no authentication identity, key path, credential path for a
client, SSH executable, or transfer command.

## Registry gates

Validation fails closed unless all of these hold:

1. strict UTF-8 JSON, no duplicate keys or non-finite numbers;
2. exact v2 schema fields and canonical IDs;
3. path is the derived host-owned slot, with no symlink/junction/reparse
   traversal, and the opened handle retains the checked file identity;
4. `host_id`, minimum revision, publication age and expiry/TTL are valid;
5. the signature algorithm, key ID and signature-reference URN equal the
   out-of-band local pins;
6. `payload_sha256` matches canonical JSON excluding
   `signature_reference`;
7. every endpoint is read-only SFTP with a `direct` or `private-overlay` label;
8. every endpoint pin exactly matches the local known-host pin;
9. every published remote path exactly matches the local allowlist;
10. metadata contains no secret/content fields or recognizable secret
    material.

The payload digest detects accidental or unreviewed document changes. It
does **not** authenticate the publisher. The receipt explicitly reports
`cryptographic_signature_verified=false`; activation requires a separately
reviewed detached-signature verifier.

## Pull-plan gates

`pull-plan` adds:

- the configured local peer must be in `allowed_peer_ids`;
- `direct_pull` must already be `true` in the validated registry;
- only `kind=file` is eligible;
- the exact destination must be absent, have an existing parent, be inside a
  configured host-local destination root, remain outside the yard and cross
  no symlink/junction/reparse point;
- SQLite-like paths are always `kind=database/sqlite`,
  `direct_pull=false`, `adapter=sqlite-transit-sync`;
- directories remain non-direct and need a separate reviewed adapter.

The resulting receipt is deterministic for the same registry, policy and
destination. Its `plan_id` is the SHA-256 of the canonical receipt body. It
always says:

```json
{
  "status": "prepared-no-transfer",
  "executable": false,
  "network_contacted": false,
  "file_transfer_performed": false,
  "referenced_files_read": false
}
```

It emits no shell command or provider choice.

## CLI

```bash
trusted-peer-paths validate \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-A

trusted-peer-paths list \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-A

trusted-peer-paths resolve \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-A --path-id service-credential-file

trusted-peer-paths pull-plan \
  --config /host-local/trusted-peer-paths.local.json \
  --host-id HOST-A --path-id service-credential-file \
  --destination /host-local/imports/credentials.json
```

The API is `TrustedPeerPathRegistry.validate`, `list_paths`, `resolve` and
`pull_plan`. Compatibility methods `publish` and `pull` fail closed without
side effects.

The examples intentionally contain `REPLACE_WITH_...` sentinels for pins,
signature references and digests. They are JSON templates, not invented
operational trust material, and runtime validation rejects them until an
operator inserts independently verified values.

## Remaining gates for real two-host activation

Preparation is not authorization to transfer. A real activation needs all of
the following, with evidence from both hosts:

1. provision a publisher signing key and verify a detached signature through
   a separately reviewed verifier;
2. obtain the real SSH host-key fingerprint out of band, pin it locally and
   verify it in the chosen SSH client;
3. create a dedicated server-side account restricted to read-only access to
   the exact approved paths, and test that writes and other reads fail;
4. choose and authorize either the direct or private-overlay route, then verify
   reachability without changing this provider-neutral registry;
5. provision authentication material outside the yard and outside this
   module; this preflight must never read it;
6. review a separate shell-free, strict-host-key, no-overwrite,
   bounded-download executor;
7. revalidate registry freshness, signature, pin, peer allowlist and
   destination ownership/permissions immediately before each transfer;
8. add auditable anti-replay state and transfer receipts without writing a
   foreign slot or placing sensitive content in the yard;
9. run two-host negative tests for wrong pin, stale registry, denied path,
   denied peer, write attempts, overwrite attempts and route failure;
10. obtain explicit activation approval. This preparation neither enables
    `direct_pull` nor performs that approval.
