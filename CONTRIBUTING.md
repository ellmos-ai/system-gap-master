# Contributing to system-gap-master

[English](#english) | [Deutsch](#deutsch)

---

<a id="english"></a>
## English

Thank you for your interest in contributing to **system-gap-master**!

### Architectural Principles & Quality Invariants

`system-gap-master` is a serverless cross-machine sync protocol and transfer yard for multi-machine AI agent operations. All contributions must respect our foundational invariants:

1. **Local-First & Zero-Egress (`INV-LOCAL-01`)**: Operates entirely on local filesystems and storage mounts; zero telemetry, zero analytics, zero external network calls by default.
2. **Unprivileged User-Mode Execution (`INV-SEC-02`)**: Executes safely in unprivileged user space (`RunAsInvoker`) without requiring root, sudo, or UAC administrator elevation.
3. **Strict Machine-Owned Slot Isolation (`INV-SLOT-03`)**: Each machine writes exclusively to its own designated slot (`hosts/<hostname>/`); peer slots are strictly read-only, preventing multi-machine collisions by design.
4. **Delete-After-Read Messaging (`INV-MSG-04`)**: Inter-machine messages (`messages/to-<host>.md`) are processed and atomically removed to enforce at-most-once processing semantics.
5. **Fail-Closed Locking & Lease Enforcement (`INV-FAIL-05`)**: Multi-agent and reconciler operations require valid exclusive kernel-backed leases (`reconciler.lock`); operations abort safely upon collision.
6. **Deterministic 3-Way & Append-Only Reconciliation (`INV-MERGE-06`)**: Provider conflict copies are reconciled via deterministic base-merge or timestamped append; destructive overwrites are strictly prohibited.
7. **Gated Preflight & Idempotent Daily Ritual (`INV-GATE-07`)**: The daily sync gate enforces once-per-day execution with audit trail in `DAILY_SYNC_LOG.md`.
8. **Cryptographically Bound SFTP & Detached Verification (`INV-PEER-08`)**: Trusted-peer preparation enforces sha256 checksums and detached Ed25519/GPG signatures; unsigned or tampered payloads are rejected fail-closed.
9. **100% Permissive Audited Dependency Stack (`INV-LIC-09`)**: Clean MIT/PSFL stack audited in `THIRD_PARTY_LICENSES.md` and `THIRD_PARTY_LICENSES.txt`, zero copyleft or AGPL contamination.
10. **Dual Security Response & Triage SLA (`INV-SLA-10`)**: 48-hour response and 5-day triage commitment via canonical security channels.

### Development Guidelines

- **Plan D Architecture**: Development, git operations, and tests occur strictly in the local git repository clone (`C:\_Local_DEV\repos\system-gap-master`).
- **Version Freeze Discipline**: Version `1.6.1` is strictly frozen per `T-20260920-167562623`. Do not bump package version; document all modifications under `## [Unreleased]` in `CHANGELOG.md`.
- **Python Version Support**: Compatible with Python 3.10 through 3.13.
- **Pre-commit Quality Gates**:
  - Bytecode compilation: `python -m compileall -q .`
  - Linting: `ruff check .`
  - Automated test suite: `pytest -ra -v` (100% green required)
- **Security Vulnerabilities**: Please do not report security vulnerabilities publicly. Follow our [SECURITY.md](SECURITY.md) guidelines for responsible disclosure (48h response SLA).

---

<a id="deutsch"></a>
## Deutsch

Vielen Dank für Ihr Interesse an einer Mitarbeit an **system-gap-master**!

### Architektur-Prinzipien & Qualitäts-Invarianten

`system-gap-master` ist ein serverloser Synchronisationsbereich (Transfer Yard) für geräteübergreifende Multi-Agenten-Workflows. Alle Beiträge müssen unsere grundlegenden Invarianten einhalten:

1. **100% Local-First & Zero-Egress (`INV-LOCAL-01`)**: Vollständige lokale Ausführung auf lokalen Dateisystemen und Mounts; null Telemetrie, null Cloud-Zwang.
2. **Unprivilegierter Ausführungsmodus (`INV-SEC-02`)**: Läuft sicher im normalen Benutzerkontext (`RunAsInvoker`) ohne Root-, Sudo- oder Administrator-Elevation.
3. **Strikte Host-Slot-Isolation (`INV-SLOT-03`)**: Jedes Gerät schreibt ausschließlich in seinen eigenen Slot (`hosts/<hostname>/`); fremde Slots bleiben strikt schreibgeschützt.
4. **Delete-After-Read Direktnachrichten (`INV-MSG-04`)**: Nachrichten zwischen Geräten werden nach erfolgreicher Verarbeitung atomar archiviert/entfernt.
5. **Fail-Closed Lease- & Lock-Sicherheit (`INV-FAIL-05`)**: Reconciler-Aktionen erfordern verlässliche Kernel-Leases; bei Kollisionen wird fail-closed abgebrochen.
6. **Deterministische 3-Wege-Reconciliation (`INV-MERGE-06`)**: Konfliktkopien werden deterministisch base-gemergt oder zeitgestempelt angehängt; niemals blind überschrieben.
7. **Tägliches Ritual mit Tages-Gate (`INV-GATE-07`)**: Das tägliche Synchronisations-Gate verhindert redundante Mehrfachläufe pro Kalendertag.
8. **Kryptografisch gebundene Peer-Prüfung (`INV-PEER-08`)**: SFTP- und Peer-Pfade erzwingen SHA-256 Hashes und getrennte Signaturen vor jeglicher Übertragung.
9. **100% permissiver Lizenz-Stack (`INV-LIC-09`)**: Rein MIT-/PSFL-lizenziert, auditiert in `THIRD_PARTY_LICENSES.md` und `THIRD_PARTY_LICENSES.txt`, null Copyleft.
10. **Zweisprachige Sicherheits-SLA (`INV-SLA-10`)**: Verbindliche 48h Reaktionszeit und 5 Tage Triage-Zusage.

### Richtlinien für Entwickler

- **Plan D Entwicklung**: Entwicklung und Tests erfolgen ausschließlich im lokalen Git-Repository (`C:\_Local_DEV\repos\system-gap-master`).
- **Version-Freeze Disziplin**: Version `1.6.1` bleibt gemäß Richtlinie `T-20260920-167562623` eingefroren; Neuerungen werden unter `## [Unreleased]` im `CHANGELOG.md` gepflegt.
- **Python-Unterstützung**: Python 3.10 bis 3.13.
- **Qualitäts-Tore vor Commits**:
  - Bytecode-Prüfung: `python -m compileall -q .`
  - Linter: `ruff check .`
  - Testsuite: `pytest -ra -v` (100% grün erforderlich)
- **Sicherheitsmeldungen**: Sicherheitslücken bitte nicht öffentlich melden, sondern gemäß [SECURITY.md](SECURITY.md) vertraulich einreichen (48h Reaktions-SLA).
