# Trusted-Peer-SFTP-Executor

Die CLI `trusted-peer-paths` bleibt ein netzwerkfreier Metadatenplaner. Der
optionale `trusted-peer-sftp-executor` ist die getrennt geprüfte
Ausführungsgrenze für genau eine gewöhnliche Datei. Installation und
Konfiguration aktivieren keinen Scheduler und erzeugen weder Schlüssel noch
Signaturen, Konten oder Routen.

## Erforderliches Vertrauensmaterial

Alle folgenden Bestandteile werden außerhalb des Sync-Yards bereitgestellt und
bleiben hostlokal:

- eine Konfiguration des Planers;
- eine Executor-Konfiguration mit dem Schema
  `system-gap.trusted-peer-sftp-executor.config.v1`;
- eine SSH-Identity, deren Remotekonto nur lesen darf;
- eine Known-Hosts-Datei mit dem exakt gepinnten Serverschlüssel;
- eine Allowed-Signers-Datei und abgesetzte OpenSSH-Signaturen;
- eine signierte, kurzlebige Freigabe nach
  `system-gap.trusted-peer-transfer-grant.v1`;
- bereits vorhandene lokale State-, Receipt-, Credential- und Zielordner.

Die Executor-Konfiguration bindet die Authentifizierung an
`host_id + endpoint_id + username`. Der Yard kann weder lokale Schlüssel,
Known-Hosts-Dateien, Verifier, Zielroots, Programme noch Receipt-Orte wählen.
`ssh-keygen` ist über absoluten Pfad und SHA-256 gebunden. Paramiko ist eine
optionale Abhängigkeit; die genaue installierte Version wird lokal festgelegt.

Jedes Auth-Profil bindet außerdem das Netzwerklabel der Registry an eine
literale Remote-IP, eine literale lokale Quell-IP und ausdrückliche Quell- und
Remote-CIDRs. Der Socket wird vor der Verbindung an die Quelladresse gebunden.
`private-overlay` ist damit eine erzwungene Routingregel und kein bloßes Label.

## Signierte Nutzdaten

Registry und Freigabe verwenden kanonisches UTF-8-JSON: sortierte Schlüssel,
kompakte Trennzeichen, kein NaN und vor Hashing beziehungsweise Signatur ein
vollständig entferntes `signature_reference`-Objekt. `payload_sha256`
enthält den Digest.

OpenSSH-Signaturen nutzen zweckgebundene Namespaces:

- Registry: `system-gap-registry`
- Freigabe: `system-gap-transfer-grant`

Die kanonischen Bytes werden dem gepinnten `ssh-keygen -Y verify` über stdin
übergeben. Signatur- und Allowed-Signers-Pfade stammen ausschließlich aus der
hostlokalen Executor-Konfiguration.

## Bindung der Einmalfreigabe

Eine Freigabe bindet:

- Quellhost und empfangenden Peer;
- Endpoint, Pfad-ID und Netzwerklabel;
- das exakte Ziel;
- Registry-SHA-256 und Revision;
- die deterministische Pull-Plan-ID;
- Gültigkeitsbeginn, Ablauf und maximale Bytezahl;
- Grant-ID und One-Shot-ID.

Die maximale Lebensdauer ist lokale Policy und beträgt höchstens 24 Stunden.
Vor jedem Netzwerkzugriff legt der Executor exklusiv einen Versuchseintrag aus
One-Shot-ID, Plan-ID und Ziel an. Auch ein Fehlversuch verbraucht die Freigabe;
ein erneuter Versuch benötigt eine neue.

## Transfergrenze

Erst nachdem alle netzwerkfreien Gates bestanden sind, führt der
Paramiko-Adapter folgende Schritte aus:

1. erwarteten Hostkey aus der lokalen Known-Hosts-Datei laden;
2. dessen SHA-256 gegen Registry und Plan prüfen;
3. mit genau diesem Hostkey und der lokalen Identity verbinden;
4. per SFTP-`lstat` alles außer einer regulären Datei abweisen;
5. Größenlimit vor und während des Streams erzwingen;
6. in eine exklusive private Staging-Datei schreiben;
7. Bytezahl prüfen und `fsync` ausführen;
8. den gepinnten Zielordner erneut prüfen und relativ zu dessen offenem Handle
   mit einer plattformspezifischen No-Replace-Operation committen;
9. ein vorhandenes Ziel niemals ersetzen.

Es gibt keinen Pfad für Shell, SCP, Remotebefehle, Uploads, Rename oder Delete
auf dem Peer, Verzeichnisdurchläufe, SQLite-Transfer, Accept-new-Hostkeys,
Passwörter oder interaktive Prompts.

## Lokaler Zustand und Receipts

`state_root/attempts/` muss vor dem Start existieren. Jeder Transfer reserviert
einen unveränderlichen JSON-Versuchseintrag. Nach der Reservierung erzeugt jedes
Ergebnis ein redigiertes Receipt unter `receipt_root`. Ein erfolgreiches
Receipt enthält zusätzlich Größe und Inhalts-SHA-256; ein fehlgeschlagenes nur
einen generischen Fehlercode. Receipts enthalten keine Credentialpfade,
Credentialwerte, privaten Schlüssel oder Dateiinhalte und werden nicht nach
`.SYNC` geschrieben.

Schemata und neutrale Beispiele liegen unter `schemas/` und `examples/`.

Englische Version:
[`trusted-peer-sftp-executor.md`](trusted-peer-sftp-executor.md).
