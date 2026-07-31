---
name: communications-protocols
description: Kommunikations-Protokolle für die Zusammenarbeit mehrerer Agenten über verschiedene Maschinen hinweg (Ping-Pong, agent-beam, listeners). Anwenden, wenn Aufträge, Antworten oder Agentenläufe über die Cloud-Synchronisierung der Sync-Yard überbrückt werden müssen.
---

# communications-protocols-skill

Protokolle für die Koordination mehrerer Agenten auf verschiedenen Hosts über
die geteilte Sync-Yard. Die Yard ist Transport, kein Arbeitsplatz: Schreiben in
den eigenen Slot, Lesen aus fremden Slots, Receipts für jede Aktion. Es gelten
weiterhin die Regeln aus `PROTOCOL.md` (Slot-Regel R1, No-Secrets R6).

## Unterkapitel 1: Ping-Pong

**Definition.** Zwei oder mehr Worker (Agenten auf verschiedenen Hosts)
überbrücken die Latenz der Cloud-Synchronisierung mit **versetzt laufenden
Cron-Jobs**: Jeder Worker scannt zyklisch die Yard auf neue Aufträge an sich,
erledigt sie, legt die Antwort und ggf. Neuaufträge für den anderen ab.
Das Ping-Pong entsteht dadurch, dass jede Seite auf dem Stand der anderen
aufsetzt — Auftrag → Scan → Erledigung → Antwort → Scan → Weiterarbeit.

**Wann einsetzen.** Wenn ein Arbeitspaket zwei Hosts betrifft (z. B. beidseitige
Verifikation, Spiegel-Setup, Zwei-Host-Pilot) und keine direkte
Kommunikationsverbindung zwischen den Agenten existiert.

### Referenzbeleg (erste produktive Anwendung)

Trusted-Peer-Aufbau WORKSTATION-LG ↔ ASUS-GEI, 2026-07-31
(kimi-code@WORKSTATION-LG, kimi-code@ASUS-GEI):

- Auftrag im fremden Slot: `.SYNC/laptop/AUFTRAG_TRUSTED_PEER_ASUS-GEI_2026-07-31.md`
- Versetzte Scans: beide Seiten mit 15-Minuten-Cron (bei einer Seite Minuten
  7/22/37/52 — versetzt gegen Volllast-Marken).
- Ping-Pong-Sequenz: Auftrag → Registry-Antwort (`.SYNC/hosts/ASUS-GEI/…`) →
  beidseitige Schlüssel-Autorisierung → Gegenproben → Fehler-Deltas
  (`DELTA*`/`DELTA-ANTWORT*` in `.SYNC/laptop/`) → kooperative Fehleranalyse.
- Belege: `.SYNC/workstation/KIMI_TRUSTED_PEER_*_2026-07-31.md`.

### Regeln

1. **Slot-Disziplin:** Jeder Worker schreibt nur in seinen eigenen Slot;
   Aufträge an andere Hosts sind Dateien im Slot des Empfängers
   (z. B. `.SYNC/<fremd-slot>/AUFTRAG_*.md`). Fremde Stände nie überschreiben.
2. **Scan-Offset:** Cron-Zeiten gegen die Volllast-Minuten 0/30 versetzen
   (z. B. 7/22/37/52). Bei mehreren Workern pro Yard dieselbe Taktung,
   keine exakte Synchronität erzwingen.
3. **Receipt-Pflicht:** Jede Aktion aus einem Scan erzeugt einen Beleg im
   eigenen Slot (was, Befund, Befehle, Ergebnis, Nonclaims). Kein
   „erledigt" ohne Dateibeleg.
4. **Idempotenz:** Jeder Scan muss mit dem Zustand „schon erledigt" korrekt
   umgehen (vorhandene Registry, bereits autorisierter Key). Nichts doppelt
   ausführen, nichts blind neu schreiben.
5. **Fail-closed-Gewohnheiten:** Schreiboperationen auf gesperrte Dateien
   können still scheitern — Erfolg NIE aus dem eigenen Log, sondern aus dem
   Readback der Zieldatei behaupten (Lehrfall: Add-Content auf read-only-ACL,
   2026-07-31). Verifikation immer gegen den tatsächlichen Endpunkt
   (ssh-keyscan statt Registry-Glaube, `ssh-keygen -lf` statt visuellem Diff).
6. **Keine Geheimnisse:** Nur Public Keys, Fingerprints, Pfade und Receipts
   in der Yard. Keine Credentialwerte, keine privaten Schlüssel, keine
   Rohtexte von Sessions/Prompts.
7. **Ping-Pong-Pausen:** Ein Scan ohne Neues kostet nur einen Logeintrag.
   Kein Worker „pusht" den anderen zur Antwort; die Taktung trägt den Fluss.
8. **Fehler-Kooperation:** Fehlschläge werden als Delta mit Verdacht +
   Gegenprobe adressiert (Beleg: `DELTA-ANTWORT…` 2026-07-31 — die
   Fehleranalyse des Partners war präziser als die eigene). Zweitmeinung
   (z. B. agy-Forensik) ist ein legitimier Ping-Pong-Schritt.

### Anti-Patterns

- „Fire-and-forget"-Aufträge ohne Scan-Partner (Ping-Pong braucht zwei Uhren).
- Sofort-Antworten erwarten: Die Yard-Latenz (Cloud-Sync + Cron-Takt) ist
  Teil des Designs; Dringendes gehört in `agent-beam` (siehe unten).
- Aufträge mit Schreibrechten im fremden Slot statt als Datei im Fremdslot.

## Unterkapitel 2: agent-beam (Entwurf, noch nicht aktiv)

**Idee.** Für dringende Aufträge: Statt nur einer Textdatei wird ein Paket
bestehend aus Prompt + Starter (z. B. `.bat`/`.ps1`) in den Fremdslot kopiert.
Ein Watcher auf der Gegenseite erkennt das Paket und **startet einen eigenen
Agentenlauf** auf dem Zielsystem — der Agent „landet" mit Auftrag und
Starter und beginnt sofort.

**Offene Designfragen (vor erster Umsetzung klären):**

- Signatur/Vertrauen: Nur signierte Starter aus autorisierten Quellen;
  die Trusted-Peer-Registry (Host-Key-Pins) ist der vorgesehene Anker.
- Sandbox der Ausführung: Kein direktes Ausführen fremder Skripte ohne
  lokalen Review-Schritt (Quarantäne-Ordner + explizite Annahme).
- Rückkanal: Der gebeamtete Agent schreibt sein Receipt in SEINEN Slot;
  der Sender verifiziert per Scan.
- Kollision mit Ping-Pong: agent-beam ersetzt keine Taktung, sondern
  überholt sie nur für deklarierte Dringlichkeit (Prioritätsfeld im Paket).

**Status:** Konzeptphase. Keine produktive Nutzung ohne eigenen
Sicherheits-Contract und Nutzerentscheid.

## Unterkapitel 3: listeners / ear-to-ear-listening (Entwurf, noch nicht aktiv)

**Idee.** Automatisierte Listener beobachten Trigger in der Yard
(Dateibewegungen, bestimmte Dateimuster, Flags) und starten daraufhin
Agenten auf dem anderen System. „Ear-to-ear": Der Listener des einen Hosts
hört auf das Ohr (Eingangsslot) des anderen.

**Trigger-Klassen (Vorschlag):**

- `TRIGGER_<ziel>_<art>_<datum>.flag` im Slot des Ziels (explizit, auditierbar)
- Eingang neuer `AUFTRAG_*`-Dateien (ersetzt den reinen Cron-Scan für
  dringende Fälle)
- Registry-Änderungen (z. B. neue Trusted-Peer-Einträge → Verifikationslauf)

**Offene Designfragen:**

- Debouncing: Cloud-Sync liefert Dateien in Etappen; Listener müssen
  Schreibvorgänge als „komplett" erkennen (z. B. `.done`-Marker neben dem
  Payload oder atomare Umbenennung).
- Missbrauchsschutz: Listener starten keine Agenten aus unverifizierten
  Triggern; Trigger-Whitelist je Slot.
- Verhältnis zu agent-beam: Listener sind die Infrastruktur, agent-beam der
  Pakettypus — gehören zusammen, werden aber getrennt gegatet.

**Status:** Konzeptphase. Keine produktive Nutzung ohne eigenen
Sicherheits-Contract und Nutzerentscheid.
