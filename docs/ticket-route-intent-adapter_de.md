# Ticket-Route-Intent-Adapter

`system-gap-master` 1.5 ergänzt einen schmalen Adapter zwischen der
öffentlichen Routing-API von `ticket-master` 1.11 und einem bereits
vorhandenen system-gap-Transport. Die ausdrückliche Integrationsabhängigkeit
wird so installiert:

```console
python -m pip install "system-gap-master[ticket-routing]>=1.5,<1.6"
```

Die Grenze ist absichtlich einseitig:

1. Nur `ticket-master` erstellt das Ticket, löst den unveränderlichen
   Ziel-Snapshot auf und verwaltet Claims, Leases, das systembezogene Ledger
   sowie den Abschluss.
2. `system-gap-master` validiert den daraus erzeugten Payload
   `ellmos.ticket.route-intent.v1` und kann genau diesen Payload einmal an
   einen vom Aufrufer bereitgestellten Transport-Callback übergeben.
3. Das Transportergebnis bleibt undurchsichtig. `queued`, `sent` oder
   `delivered` bedeutet nie, dass ein Ticket abgeschlossen ist. Nur gültige,
   durch `ticket-master` verbuchte Host-Receipts dürfen das Ledger fortsetzen.

Der Adapter besitzt keine Queue, Inbox, Retry-Schleife, Zielauffächerung oder
Ticketdateien. Aufrufer müssen einen stabilen `idempotency_key` für die
Erstellung mitgeben. Ein erneuter Aufruf wird dadurch vom kanonischen
Ticket-Schreiber demselben Ticket zugeordnet, statt ein weiteres anzulegen.

## Gesonderter Message-Handoff

`ellmos.ticket.message-route-intent.v1` ist ein eigener, nicht ausführbarer
Nachrichten-Subject. Er enthält MessageID, kanonische ReceiptID, Ursprung,
Empfänger, Ziel und Digest des vollständigen Message-Tickets, aber keinen
Nachrichtentext und keine T-Ticket-ID. Das bestehende `route-intent.v1` und
sein Arbeits-Ticket-Lebenszyklus bleiben unverändert.

`submit_message_handoff()` verlangt ausdrücklich getrennte Sender- und
Empfängerquellen sowie Loader für deren **aktuelle** Konfiguration. Beide
Quellen müssen die Nachricht einschließlich Text aus dem autorisierten
Messages-Line-Store auflösen; IDs, Ziel und Digest werden vor dem vorhandenen
Transport-Callback geprüft. Ein bloßer Verweis ohne erreichbaren Payload ist
kein Handoff. Beide Konfigurationen müssen den Message-Vertrag gewählt haben.

Der Callback kann ein metadatenarmes Artefakt unter einem ausdrücklich
freigegebenen Root ablegen. Nur wenn Dateipfad, exakte Bytes, SHA-256,
Transport-ID und Ereigniszeit dazu passen, wird über den kanonischen
Messages-Line-Core eine `transported`-Beobachtung erfasst. `offline` erzeugt
keine Beobachtung. Wiederholung derselben Evidenz ist idempotent;
widersprüchliche Wiederverwendung der Transport-ID wird abgewiesen.
Transportartefakt und Callback-Rückgabe belegen **keinen** Empfang, keine
Anzeige und keinen Ack. Ein verifizierter externer Ack-/Historienimport ist
hier nicht vorhanden; Produktivtransport, Remote-Store-Registrierung und
Empfängeridentität benötigen eigene Abnahme.

Das vollständige Python-Beispiel steht in der
[englischen Adapter-Dokumentation](ticket-route-intent-adapter.md). Für eine
eingebettete Installation oder Tests kann über `api=` ein kompatibles
API-Objekt injiziert werden. Ohne Injektion importiert der Adapter nur die drei
paketierten öffentlichen Funktionen `create_routed_ticket`, `load_contract`
und `build_route_intent`.
