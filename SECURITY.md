# Security Policy

## Deutsch

### Sicherheitslücken melden

Bitte keine öffentlichen Issues für Sicherheitslücken eröffnen. Verwenden Sie GitHub Private Vulnerability Reporting, falls es im Repository aktiviert ist. Falls nicht, kontaktieren Sie die Maintainer über GitHub und veröffentlichen Sie keine Details.

### Geltungsbereich

`system-gap-master` definiert ein dateibasiertes Synchronisationsprotokoll für KI-Agenten über ein gemeinsames Verzeichnis ("Yard").
Es werden ausdrücklich **keine** Anmeldedaten, API-Keys, Token oder vertraulichen Personendaten im Yard abgelegt.
Sicherheitsrelevante Meldungen zu Pfadprüfungen, Dateizugriffen oder unerwarteter Datenweitergabe liegen im Geltungsbereich.

## English

### Reporting a Vulnerability

Please do not open public issues for security vulnerabilities. Use GitHub Private Vulnerability Reporting if it is enabled for the repository. If it is not enabled, contact the maintainers through GitHub and do not disclose details publicly.

### Scope

`system-gap-master` defines a file-based sync protocol for AI agents over a shared directory ("Yard").
No credentials, API keys, tokens, or confidential personal data may ever be stored in the yard.
Security reports concerning path validation, file access boundaries, or unexpected data exposure are within scope.

## Conflict-copy reconciler boundary

- Host configs, receipt salts, roots, plans, operation manifests and backups
  are private local state and must not be committed or synced.
- Public examples contain placeholders only. A persistent high-entropy
  `receipt_salt` HMAC-binds plans and manifests.
- Observer mode is read-only. Only one host/root adapter may use
  `mutating-owner`, and every adapter for that scope must share one local
  state directory so the path-derived lease is effective.
- Symlinks, junctions, reparse points, alternate data streams, device names,
  changed sources, changed rollback targets and tampered backups fail closed.
- Lease mutations share a persistent host-local guard inode with a
  kernel-released OS lock. Rollback rebinds every input immediately before
  mutation and retains recoverable archives rather than deleting them.
- Lease renewal uses a fsynced no-overwrite temporary file and atomic
  replacement after a final guard/token/fingerprint check. A malformed lease
  is quarantined only when explicit takeover is enabled and stable mtime age
  exceeds the configured TTL; recent damage remains busy.

## Trusted peer path registry boundary

- The yard is semi-trusted. Exact approved SFTP paths may be metadata, but
  file content, credential values, private keys, tokens and passwords are
  forbidden. Every path declares `metadata_type=path-location` and
  `content_included=false`.
- The CLI derives the read location
  `hosts/<TRUSTED-HOST>/trusted-peer-paths/registry.json` and rejects
  host/slot substitution. It has no publisher or yard-write operation.
- Strict JSON, canonical IDs, revision, freshness/expiry, pinned signature
  reference, canonical payload digest, known-host pin, peer permission and
  exact remote-path allowlists fail closed.
- A signature reference and digest are not publisher authentication. The
  planner receipt therefore says `cryptographic_signature_verified=false`.
- `pull-plan` is deterministic but non-executable. It never opens a network
  connection, invokes SSH/SFTP, reads referenced credentials/keys/signatures
  or known-hosts files, writes the yard, creates a destination, enables
  `direct_pull` or transfers bytes.
- The optional executor re-runs that plan and additionally verifies detached
  registry and one-shot-grant signatures, resolves authentication only from
  local allowlisted credential roots, matches the presented host key, reserves
  a durable one-shot attempt, streams one bounded regular file, and commits
  without replacement. The auth profile also binds an exact literal remote IP,
  local source IP and source/remote CIDRs, so route labels are enforced before
  the socket opens. Its receipts are local and redacted.
- Destinations must be absent, allowlisted, host-local, outside the yard and
  free of symlink/junction/reparse traversal. Server-side read-only ACLs,
  account/key provisioning and route authorization remain separate host gates.
- Directories require a separate reviewed adapter. SQLite `.db`, `.sqlite`,
  `.sqlite3`, `-wal` and `-shm` paths require
  `kind=database/sqlite`, `direct_pull=false` and
  `adapter=sqlite-transit-sync`; transfer remains in the R9
  `db-transit/<namespace>` snapshot boundary.
