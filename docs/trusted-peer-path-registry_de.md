# Trusted-Peer-Pfadregistry: reine Vorbereitung

`trusted-peer-paths` validiert cloud-sichere Pfadmetadaten und erzeugt einen
deterministischen Pull-Vorbereitungsbeleg. Das Modul ist ausdrücklich **kein
Transfer-Client**. Es veröffentlicht keine Registry, öffnet keine
Netzwerkverbindung, startet kein SSH/SFTP, liest keine referenzierte
Credential-, Key-, Signatur- oder Known-Hosts-Datei, kopiert keine Bytes und
legt kein Ziel an.

Der Vertrag trennt Pfadmetadaten von Inhalten:

- Eine Registry darf einen exakt freigegebenen SFTP-Pfad enthalten.
- Jeder Eintrag trägt `metadata_type=path-location` und
  `content_included=false`.
- Credential-Werte, Dateiinhalte, private Keys, Tokens und Passwörter werden
  abgelehnt.
- Signaturreferenz, Payload-Digest und Known-Host-Pin sind Metadaten. Sie
  beweisen nicht, dass ein externer Verifier oder SSH-Client sie verwendet
  hat.

Das ist die V4-Preflight-Grenze. Eine echte Zwei-Host-Aktivierung bleibt eine
separate, geprüfte Änderung.

## Eigentum und Dateien

Der Validator leitet genau einen Lesepfad ab:

```text
<YARD>/hosts/<TRUSTED_HOST_ID>/trusted-peer-paths/registry.json
```

Die `host_id` im Dokument muss zum Slot passen. Die CLI kennt weder
`publish`, `pull`, `--apply` noch `--output`; Ergebnisse erscheinen nur auf
stdout. Diese Capability ändert daher weder den Yard noch einen fremden Slot.

Die lokale Policy liegt außerhalb des Yards und enthält ausschließlich
Trust-Metadaten:

- lokale Host- und Peer-ID;
- exakte Trusted-Host-ID und minimale Revision;
- gepinnte Signaturmethode, Key-ID und Signaturreferenz-URN;
- exakte Remote-Pfad-Allowlist;
- erlaubte Netzlabels `direct` und/oder `private-overlay`;
- Zuordnung Endpoint zu Known-Host-SHA-256-Pin;
- host-lokale Zielroots und Grenzen für Alter/TTL der Registry.

Authentisierungsidentität, Key-Pfad, SSH-Executable und Transferbefehl gehören
nicht hinein.

## Fail-closed-Gates

Die Validierung verlangt striktes UTF-8-JSON ohne doppelte Keys, exakte
v2-Felder, kanonische IDs, den abgeleiteten Owner-Slot, gültige
Revision/Zeit/Expiry, identische lokale Pins für Signaturreferenz und
Known-Host-Key, einen passenden kanonischen Payload-Digest, read-only SFTP,
ein erlaubtes Netzlabel, eine beim Öffnen unveränderte Dateiidentität und eine
exakte lokale Remote-Pfad-Freigabe.
Secret-/Content-Felder und erkennbare Secret-Muster blockieren.

Der Digest schützt gegen unbeabsichtigte Dokumentänderungen, authentisiert
aber keinen Publisher. Deshalb meldet der Beleg ausdrücklich
`cryptographic_signature_verified=false`. Für die Aktivierung ist ein
separat geprüfter Detached-Signature-Verifier Pflicht.

`pull-plan` verlangt zusätzlich:

- Der lokale Peer steht in `allowed_peer_ids`.
- `direct_pull` ist bereits `true`; das Modul setzt es nie.
- Nur `kind=file` ist zulässig.
- Das Ziel existiert noch nicht, sein Parent existiert, es liegt in einem
  host-lokalen Allowlist-Root außerhalb des Yards und quert keinen
  Symlink/Junction/Reparse-Punkt.
- SQLite-Pfade bleiben `database/sqlite`, `direct_pull=false` und verwenden
  `sqlite-transit-sync`.
- Verzeichnisse bleiben non-direct und brauchen einen separaten Adapter.

Der Beleg ist für dieselbe Registry, Policy und dasselbe Ziel deterministisch
und immer nicht ausführbar:

```json
{
  "status": "prepared-no-transfer",
  "executable": false,
  "network_contacted": false,
  "file_transfer_performed": false,
  "referenced_files_read": false
}
```

## CLI

```bash
trusted-peer-paths validate --config <HOST_LOCAL_CONFIG> --host-id HOST-A
trusted-peer-paths list --config <HOST_LOCAL_CONFIG> --host-id HOST-A
trusted-peer-paths resolve --config <HOST_LOCAL_CONFIG> \
  --host-id HOST-A --path-id service-credential-file
trusted-peer-paths pull-plan --config <HOST_LOCAL_CONFIG> \
  --host-id HOST-A --path-id service-credential-file \
  --destination /host-local/imports/credentials.json
```

Die API besteht aus `TrustedPeerPathRegistry.validate`, `list_paths`,
`resolve` und `pull_plan`. Die Kompatibilitätsmethoden `publish` und `pull`
brechen ohne Seiteneffekt ab.

Die Beispiele enthalten bewusst `REPLACE_WITH_...`-Platzhalter. Sie erfinden
keine operativen Pins oder Keys und scheitern zur Laufzeit, bis unabhängig
verifizierte Werte eingesetzt wurden.

## Separate Ausführungsgrenze

Der optionale `trusted-peer-sftp-executor` implementiert jetzt die getrennt
prüfbare clientseitige Ausführungsgrenze, ohne diesen Planer zu verändern.
Siehe [`trusted-peer-sftp-executor.md`](trusted-peer-sftp-executor.md). Seine
bloße Installation provisioniert, plant oder autorisiert keinen Host.

## Verbleibende Host-Gates für die echte Zwei-Host-Aktivierung

1. Publisher-Key provisionieren und Detached-Signatur mit einem separat
   geprüften Verifier prüfen.
2. Echten SSH-Host-Key-Fingerprint out of band beziehen, lokal pinnen und im
   gewählten SSH-Client verifizieren.
3. Dedizierten Server-Account mit read-only ACL auf exakt freigegebene Pfade
   einrichten; Schreibzugriff und andere Reads müssen nachweislich scheitern.
4. Route `direct` oder `private-overlay` auswählen, autorisieren und prüfen, ohne
   die providerneutrale Registry umzubauen.
5. Authentisierungsmaterial außerhalb des Yards und dieses Moduls
   provisionieren; der Preflight darf es nie lesen.
6. Separaten shell-freien, Strict-Host-Key-, No-Overwrite- und
   Download-beschränkten Executor samt exakter Hostkonfiguration installieren
   und reviewen.
7. Unmittelbar vor jedem Transfer Registry-Frische, Signatur, Pin,
   Peer-Allowlist und Zielberechtigungen erneut prüfen.
8. Auditierbaren Anti-Replay-State und Transferbelege ergänzen, ohne fremde
   Slots zu schreiben oder sensible Inhalte in den Yard zu legen.
9. Zwei-Host-Negativtests für falschen Pin, stale Registry, gesperrten Pfad,
   gesperrten Peer, Schreib-/Overwrite-Versuch und Routenausfall bestehen.
10. Explizite Aktivierungsfreigabe einholen. Der Planer aktiviert weder
    `direct_pull` noch einen Transfer; der Executor verlangt für jeden Versuch
    eine signierte Einmalfreigabe.
