# Trusted-Peer-Pfadregistry

Die Trusted-Peer-Pfadregistry ermöglicht vorab autorisierten Rechnern,
genaue Pfade zu finden und gewöhnliche Dateien direkt per SFTP über
Tailscale oder LAN abzurufen. Der Yard enthält nur ein signiertes Verzeichnis
aus Pfaden, Endpunkten und Peer-Berechtigungen. Die referenzierte Datei,
Credential-Werte, HMAC-Schlüssel und private SSH-Schlüssel gelangen niemals
in den Yard.

Nach der einmaligen Einrichtung von Vertrauen und SSH-Server ist keine
Koordination pro Anfrage nötig. Jeder Host veröffentlicht nur in seinem
eigenen Slot; jeder berechtigte Peer kann selbstständig prüfen, auflösen und
abrufen.

## Anwendungsfälle

- Den genauen hostlokalen Pfad einer Credential-Datei veröffentlichen, damit
  ein autorisierter Wiederherstellungsrechner sie über ein
  schreibgeschütztes SSH-Konto abrufen kann.
- Gewöhnliche Konfigurationen, Zertifikatspakete oder Exportdateien
  bereitstellen, ohne deren Inhalt in den semi-vertrauenswürdigen Yard zu
  kopieren.
- Den Pfad eines SQLite-Zustands sichtbar machen, während die eigentliche
  Übertragung zwingend über den R9-Adapter `sqlite-transit-sync` läuft.
- Mehreren Agent-Runtimes denselben verifizierten Pullplan geben, ohne ein
  gemeinsames Shell-Skript oder Schreibrechte auf fremde Host-Slots.

## Dateien und Eigentum

Jeder Publisher schreibt genau an den abgeleiteten Ort:

```text
<YARD>/hosts/<LOCAL_HOST_ID>/trusted-peer-paths/registry.json
```

`publish` akzeptiert weder einen Registry-Pfad noch eine fremde Host-ID. Das
Registry-Ziel stammt aus der hostlokalen Konfiguration, und die
großgeschriebene `host_id` muss zum Slot passen. Das optionale CLI-`--output`
ist nur eine hostlokale Ergebnisdatei: ohne Überschreiben und niemals im
Yard, State, in Konfiguration, Keys, `known_hosts`, Executable, Eingabe oder
Pull-Ziel. Andere Hosts dürfen die Registry lesen, aber niemals aktualisieren.

Außerhalb des synchronisierten Yards bleiben:

- lokale Konfiguration und Publication-Entries;
- Signing-/Verification-Key-Dateien;
- SSH-`known_hosts` und private Authentisierungsmittel;
- Revisions-Pins, Pull-Staging und Validierungszustand.

Die öffentlichen Schemata liegen unter [`schemas/`](../schemas/). Die
Beispiele aus [`examples/`](../examples/) werden in einen hostlokalen Ordner
kopiert, vollständig angepasst und mit Betriebssystemrechten geschützt.

## Authentizität und Replay-Schutz

Jede Registry trägt eine HMAC-SHA256-Signatur über kanonisches JSON sowie
eine `key_id`. Der Publisher liest den Schlüssel über
`publisher.signing_key_ref`; Peers pinnen den einmalig außerhalb des Yards
bereitgestellten Schlüssel über `trusted_hosts[].verification_key_ref`.
Schlüsselbytes erscheinen weder im Yard noch in der Befehlsausgabe.

HMAC ist symmetrisch: Jeder Verifier mit dem Schlüssel eines Hosts könnte
diesen Host imitieren. Deshalb erhält jeder Publisher einen eigenen
hoch-entropischen Schlüssel, den nur seine autorisierten Peers bekommen. Für
eine Rotation werden zuerst neuer lokaler Schlüssel und neue Trust-Pins
verteilt, anschließend ändern sich `key_id` und Registry.

Peers setzen beim Bootstrap eine `min_revision`. Nach erfolgreicher Prüfung
speichert die CLI höchste Revision und Dokumenthash im hostlokalen
`state_dir`. Ältere signierte Dokumente und abweichende Dokumente mit
derselben Revision scheitern als Replay beziehungsweise Equivocation. Der
Publisher muss `revision` erhöhen und überschreibt keine neuere oder
unprüfbare Yard-Datei. Crash-freigegebene hostlokale OS-Locks serialisieren
Revisions-Pins und Publish-Vorgänge, damit parallele Agenten den höchsten
gesehenen Stand nicht zurücksetzen.
Der Publisher prüft denselben strikt typisierten Revisions-/Digest-Zustand
unter diesem Lock vor jedem Registry-Schreibvorgang in den Yard. Doppelte
JSON-Schlüssel, NaN/Infinity, ungültige State-Digests sowie nicht kanonische
oder nicht als String typisierte IDs brechen geschlossen ab.

## Veröffentlichte Felder

Jeder Pfadeintrag enthält:

- `path_id`: stabile, pfadneutrale ID;
- `kind`: `file`, `directory` oder `database/sqlite`;
- `local_path`: genauer absoluter Pfad auf dem veröffentlichenden Host;
- `remote_path`: genauer absoluter Pfad im SFTP-Subsystem;
- `endpoint_id`: signierter SFTP-Endpunkt;
- `allowed_peer_ids`: berechtigte Peers;
- `direct_pull`: ausdrückliche Entscheidung des Publishers;
- optional `adapter` und `description`.

Genaue Credential-Pfade sind zulässig, weil die Registry gerade Orte
veröffentlichen soll. Pfadnamen sind aber Metadaten, die alle Yard-Leser
sehen können. Credential-Werte und Dateiinhalte bleiben verboten.

Dateisystemadressierende Host- und Peer-IDs verwenden kanonische
Großschreibung, alle übrigen Registry-IDs kanonische Kleinschreibung.
Windows-Gerätenamen, Alternate Data Streams und abschließende Punkte/Spaces
werden abgelehnt. Bei vorhandenen Windows-`local_path`-Werten wird auch der
finale Langpfad klassifiziert, sodass ein 8.3-Alias SQLite nicht tarnen kann.
Boundary-Vergleiche lehnen zuerst lexikalische Reparse-Komponenten ab und
vergleichen danach den physischen Pfad. So kann eine legitime 8.3-Schreibweise
keine Überschneidung mit dem Yard oder einem anderen geschützten Pfad tarnen.

Die Peer-Allowlist schützt die Resolve-/Pull-Grenze dieser CLI. Unabhängig
davon muss der SSH-Server Authentisierung, schreibgeschützte Dateirechte und
die vorgesehene Netzwerkgrenze erzwingen. Eine signierte Registry ersetzt
keine SSH-ACL.

## CLI

Nach Installation des Pakets und Erstellung der hostlokalen Dateien:

```bash
trusted-peer-paths publish \
  --config /hostlokal/trusted-peer-paths.local.json \
  --entries /hostlokal/trusted-peer-paths.entries.local.json

trusted-peer-paths validate \
  --config /hostlokal/trusted-peer-paths.local.json \
  --host-id HOST-B

trusted-peer-paths list \
  --config /hostlokal/trusted-peer-paths.local.json

trusted-peer-paths resolve \
  --config /hostlokal/trusted-peer-paths.local.json \
  --host-id HOST-B --path-id service-credential-file

trusted-peer-paths pull-plan \
  --config /hostlokal/trusted-peer-paths.local.json \
  --host-id HOST-B --path-id service-credential-file \
  --destination /erlaubte/importe/credentials.json

# Ohne --apply nur Dry-Run:
trusted-peer-paths pull \
  --config /hostlokal/trusted-peer-paths.local.json \
  --host-id HOST-B --path-id service-credential-file \
  --destination /erlaubte/importe/credentials.json --apply
```

`publish`, Validierungszustand und Registry-Ersetzung verwenden temporäre
Dateien, `fsync` und atomaren Austausch. `list` zeigt nur Pfade, für die die
konfigurierte `local_peer_id` berechtigt ist. `resolve`, `pull-plan` und
`pull` brechen bei nicht vertrauenswürdigen Hosts, ungültiger Signatur,
Replay, unbekanntem Transport, Traversal, fehlender Peer-Berechtigung und
ungültigen Pfaddaten geschlossen ab.

## SFTP-Pull-Grenze

Die direkte Ausführung ist bewusst eng:

- `transport=sftp` über `network=tailscale|lan`;
- nur reguläre Dateien mit `direct_pull=true`;
- OpenSSH-`sftp` als Argumentvektor mit `shell=False`;
- genauer hostlokaler `sftp_executable_ref` statt PATH-Suche und keine
  User-/System-SSH-Konfiguration (`-F none`);
- Batch-Modus, strikte Host-Key-Prüfung und genauer hostlokaler
  `known_hosts_ref`; Whitespace, Quotes, `%`-Tokens und `${...}`-Expansion
  werden vor OpenSSH abgelehnt;
- konservative, nicht globbende Remote-Pfade und geprüfte
  Endpunkt-/User-/Port-Felder;
- absolutes Ziel in einem konfigurierten `pull_destination_root`;
- keine Symlink-, Junction- oder Reparse-Komponente im Ziel;
- ein unveränderlicher verifizierter Plan liefert Remote-Pfad und Endpunkt;
- eindeutiges hostlokales Staging, konfiguriertes `max_download_bytes`,
  SHA-256-Readback und atomare Hardlink-Installation ohne Überschreiben;
- POSIX-Modi von Staging und Ziel werden als `0600` gesetzt und geprüft;
- der SFTP-Prozess läuft im privaten Staging-Verzeichnis und leitet stdout
  und stderr direkt ins Nullgerät.

Ohne `--apply` bleibt `pull` ein Dry-Run. Ein vorhandenes Ziel blockiert
immer. Kann das Dateisystem keinen atomaren No-Replace-Hardlink bereitstellen,
bricht Apply geschlossen ab, statt eine teilweise Zieldatei sichtbar zu
machen. Unter Windows stellt `chmod(0600)` keine vollständige NTFS-ACL her;
die erlaubten Zielwurzeln brauchen daher hostseitig Owner-only-ACLs.
Verzeichnisse liefern einen verifizierten Plan mit
`directory-pull-requires-reviewed-adapter`; diese Version kopiert sie nicht
rekursiv.

## SQLite-Grenze (R9)

Live-SQLite-Datenbanken sowie `-wal`-/`-shm`-Begleiter dürfen zur Discovery
gelistet werden, müssen aber Folgendes festlegen:

```json
{
  "kind": "database/sqlite",
  "direct_pull": false,
  "adapter": "sqlite-transit-sync"
}
```

Als gewöhnliche Datei getarnte `.db`-, `.sqlite`-, `.sqlite3`-, `-wal`- oder
`-shm`-Pfade einschließlich vorhandener Windows-8.3-Aliase werden abgelehnt.
`pull-plan` und `pull --apply` bleiben blockiert und verweisen auf den
R9-Snapshot-Weg
`db-transit/<namespace>`. Dieses Modul implementiert keinen
Datenbankabgleich.
