# Security Policy

## Deutsch

### Sicherheitslücken melden

Bitte keine öffentlichen Issues für Sicherheitslücken eröffnen. Verwenden Sie bevorzugt das [GitHub Security Advisory Reporting](https://github.com/ellmos-ai/system-gap-master/security/advisories). Alternativ erreichen Sie das Sicherheitsteam direkt per E-Mail:
- **E-Mail:** `security@ellmos.ai`
- **Fallback / Maintainer:** `support@lukasgeiger.com` | `lukas@open-bricks.org`

Wir prüfen sicherheitsrelevante Hinweise zeitnah und stellen bei Bedarf koordinierte Patches bereit.

### Geltungsbereich & Sicherheitsarchitektur

`system-gap-master` definiert ein dateibasiertes, serverloses Synchronisationsprotokoll für KI-Agenten über ein gemeinsames Übergabeverzeichnis ("Yard").
- **Local-First & Zero-Egress:** Das Kernprotokoll und die Standard-Werkzeuge (`system_gap_daily_check.py`, `conflict_copy_reconciler.py`, `config_snapshot.py`, `trusted-peer-paths`) arbeiten 100% offline und senden zu keinem Zeitpunkt unbefugte Telemetrie oder Daten ins Netz.
- **Keine Secrets im Yard:** Es werden ausdrücklich **keine** Anmeldedaten, API-Keys, Token, Passwörter oder vertraulichen Klientendaten im Yard abgelegt (Regel 6).
- **Non-Elevation:** Alle Skripte und CLIs laufen standardmäßig im unprivilegierten Benutzerkontext (User-Mode). Es werden weder Administrator- noch Root-Rechte benötigt.
- **Fail-Closed Integrität:** Pfadvalidierungen (Schutz vor Symlink-, Junction- und Directory-Traversal-Attacken) und Berechtigungsprüfungen schlagen bei Zweifelsfällen standardmäßig fehl (*fail-closed*).

## English

### Reporting a Vulnerability

Please do not open public issues for security vulnerabilities. We strongly encourage reporting via [GitHub Security Advisories](https://github.com/ellmos-ai/system-gap-master/security/advisories). Alternatively, you can contact the security maintainers directly:
- **Email:** `security@ellmos.ai`
- **Fallback / Maintainer:** `support@lukasgeiger.com` | `lukas@open-bricks.org`

Security disclosures are handled promptly, with coordinated fixes published following responsible disclosure practices.

### Scope & Security Invariants

`system-gap-master` defines a file-based, serverless sync protocol for AI agents operating over a shared transfer yard directory.
- **Local-First & Zero-Egress:** The core protocol and default tools (`system_gap_daily_check.py`, `conflict_copy_reconciler.py`, `config_snapshot.py`, `trusted-peer-paths`) operate 100% offline without telemetry or external network egress.
- **No Secrets in Yard:** No credentials, API keys, tokens, passwords, private keys, or confidential personal data may ever be stored in the yard (Rule 6).
- **Non-Elevation:** All scripts and CLI utilities operate entirely within unprivileged user space. No administrator or root elevation is required or requested.
- **Fail-Closed Boundaries:** Path validations (protection against symlink, junction, and directory traversal attacks) and authorization checks fail closed by default.

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
