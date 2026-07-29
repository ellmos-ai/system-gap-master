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

- The yard is semi-trusted. Exact credential paths may be registry metadata,
  but file content, credential values, HMAC keys, SSH private keys,
  `known_hosts`, validation state and staging files remain host-local.
- A host publishes only
  `hosts/<OWN-HOST>/trusted-peer-paths/registry.json`; the CLI derives this
  path and rejects host/slot substitution.
- HMAC-SHA256 signatures, pinned host/key identities, minimum revisions and
  host-local highest-seen state fail closed on tampering, replay and
  same-revision equivocation. Crash-released local OS locks serialize state
  and publish updates. HMAC verification keys are symmetric and must be
  distributed only to the publisher's trusted peers.
- Peer allowlists protect resolve/pull in this CLI. They do not replace SSH
  authentication or server-side read-only ACLs.
- Executed pulls use OpenSSH SFTP with `shell=False`, batch mode, strict
  host-key checking, exact host-local executable/`known_hosts` references, no
  inherited SSH config, conservative non-globbing paths, destination-root
  allowlists and no-overwrite install. `pull` is a dry-run unless `--apply`
  is explicit.
- Symlink, junction or reparse destinations, command-like endpoint/path
  values, unknown transports, unauthorized peers and unverifiable registries
  fail closed. Subprocess output is not returned because it could contain
  remote diagnostics.
- Directories require a separate reviewed adapter. SQLite `.db`, `.sqlite`,
  `.sqlite3`, `-wal` and `-shm` paths require
  `kind=database/sqlite`, `direct_pull=false` and
  `adapter=sqlite-transit-sync`; transfer remains in the R9
  `db-transit/<namespace>` snapshot boundary.
