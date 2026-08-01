# Agent-Tunnel — Benennung und Protokoll-Familie (nutzerneutral)

> Benennungs-Entscheid 2026-07-31 (User): die Trusted-Peer-Transportschicht
> und ihre Nutzung bekommen eine eigene, einheitliche Namensfamilie.
> Diese Datei ist die kanonische Referenz dafür.

## Die Namensfamilie

| Name | Was es ist | Beispiel |
|---|---|---|
| **agent-tunnel** | Der Transportweg: ein vertrauensvoller, host-gepinnter Kanal zwischen zwei Maschinen (SSH/SFTP über das Overlay-Netz, gepinnte Host-Keys, dedizierter SFTP-Account, peer-inbox-Ablagen). | SSH/SFTP über Tailscale, `ellmos-peer` |
| **file-beam** | Eine Datei wird über den agent-tunnel gebeamt — sie landet als **file-drop** auf dem anderen System (atomar in der peer-inbox des Ziels). | versiegelte DB-Snapshots, Receipts, Registry-Dateien |
| **credential-beam** | Der Credential-Transport über den agent-tunnel: ausschließlich öffentliches Schlüsselmaterial, Fingerprints und Pins (nie geheime Werte). | Public Keys, Host-Key-Pins, Registry-Signaturen |
| **agent-beam** | Der Agententransport: ein Agent (Prompt + Starter) landet auf dem anderen System und beginnt dort zu arbeiten. | Vier-Phasen-Paket im `agent-beam`-Konzept |

## agent-tunnel (der Transportweg)

Der agent-tunnel ist die Infrastruktur. Er besteht aus:

1. **Kanal:** SSH/SFTP über das Overlay-Netz (z. B. Tailscale); der sshd lauscht
   ausschließlich auf der Overlay-IP, die Firewall ist auf das Overlay-Subnetz
   beschränkt.
2. **Vertrauen:** gegenseitig gepinnte Host-Keys, publiziert in der
   `trusted-peer-paths/registry.json` je Host (nur Metadaten, nie Geheimnisse).
3. **Konto:** dedizierter Account pro Host (`ellmos-peer`), Public-Key-only,
   `ForceCommand internal-sftp` (kein TTY), Transit-Ablage `peer-inbox/`.
4. **Ablage:** jede file-drop landet atomar (Temp-Datei + `os.replace`) in der
   peer-inbox des Zielhosts; Konsumenten lesen von dort.

Sicherheitsregeln: kein Passwort-Login, keine Secret-Übertragung, keine
OneDrive-/Cloud-Ablage für Transportartefakte, jede Übertragung receiptet.

## file-beam / file-drop (Dateitransport)

Eine Datei reist als file-beam über den agent-tunnel und wird auf der
Gegenseite als file-drop abgelegt. Konventionen:

- **Versiegelung vor dem Beam:** DB-Snapshots werden versiegelt
  (sqlite-Backup-API, keine WAL/SHM-Bytecopy); Text-Artefakte gehen als
  fertige Datei.
- **Hash-Pflicht:** jede file-drop wird mit SHA-256 übertragen und gegen den
  deklarierten Hash verifiziert (kein Vertrauensvorschuss).
- **Readback:** der Empfänger bestätigt im Receipt Hash + Platzierung.

## credential-beam (Credential-Transport)

Der credential-beam transportiert **nur öffentliches Material**: Public Keys,
Fingerprints, Host-Key-Pins, signierte Registry-Metadaten. **Niemals** geheime
Schlüssel, Tokens oder Credentialwerte — der agent-tunnel ist kein
Secret-Kanal.

## agent-beam (Agententransport)

Der agent-beam nutzt denselben Tunnel, um einen **Agenten** (Prompt +
Starter) auf dem Zielsystem landen zu lassen — siehe das Konzeptkapitel in
`docs/communications-protocols-skill.md` (agent-beam, derzeit Entwurf/geblockt).

## Bezug zur Ping-Pong-Familie

- **Ping-Pong** = das Koordinationsprotokoll (Aufträge/Antworten über die Yard).
- **agent-tunnel** = der vertrauensvolle Übertragungsweg für große oder
  vertrauliche-kanonische Artefakte (Snapshots, Seeds) — ergänzt, ersetzt
  aber nicht die Yard (die Yard bleibt für Aufträge/Deltas/Receipts).
- Faustregel: Aufträge und Receipts laufen über die Yard (Ping-Pong);
  Datei-Transfers und Seeds laufen über den agent-tunnel (file-beam).
