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

Das vollständige Python-Beispiel steht in der
[englischen Adapter-Dokumentation](ticket-route-intent-adapter.md). Für eine
eingebettete Installation oder Tests kann über `api=` ein kompatibles
API-Objekt injiziert werden. Ohne Injektion importiert der Adapter nur die drei
paketierten öffentlichen Funktionen `create_routed_ticket`, `load_contract`
und `build_route_intent`.
