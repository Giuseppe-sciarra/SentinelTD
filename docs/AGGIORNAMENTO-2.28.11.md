# Sentinel TD 2.28.11 — confronto fonti WHOIS

Ogni lookup controlla il registro WHOIS, RDAP per i TLD supportati, **who.is** e
**whois.com**. Le richieste alle fonti sono sequenziali per ciascun dominio, con
limiti di tempo; la concorrenza tra domini segue l'impostazione esistente.
who.is viene letto dalla pagina pubblica, senza chiave API o servizi a pagamento.
Una fonte bloccata, senza scadenza o con un dominio diverso non fornisce un risultato.

## Scelta della data

- Si confronta l'ultimo aggiornamento del record del dominio, quando comparabile.
- La fonte con record più recente può superare una data precedente del registro.
- Senza evidenza di un record più recente, il dato corrente del registro prevale
  sulle copie web. RDAP e WHOIS sono dati dello stesso registro, non voti indipendenti.
- Se restano solo copie web con date diverse, si usano le date delle rispettive
  rilevazioni se tutte disponibili e confrontabili.
- Se il conflitto resta irrisolto, la data nota viene conservata e il pannello
  mostra **Data da verificare** con i risultati delle fonti. Non si sceglie la
  scadenza più lontana e non partono avvisi basati sulla data dubbia.
- Una scadenza passata proveniente solo da fonti secondarie resta da verificare,
  salvo uno stato tecnico di autorinnovo fresco e riconoscibile.

**Confronto fonti**, nella colonna Scadenza, mostra quale fonte è utilizzata,
la scadenza letta, l'aggiornamento del record e la data della rilevazione, quando
pubblicati. Espandendo il confronto si vedono anche le fonti in errore.
Questi dati vengono salvati nel database; non vengono archiviati contatti personali
né pagine WHOIS complete. Gli errori conservano la scadenza precedente.

## Domini .it e autorinnovo

Lo stato `ok / autoRenewPeriod` viene mostrato come **Rinnovo in corso** entro
il periodo tecnico di 15 giorni, usando uno stato aggiornato e senza attribuire
arbitrariamente una scadenza all'anno successivo. La data effettivamente pubblicata
resta visibile. Gli stati inattivi, le fonti obsolete e i conflitti non sono mascherati.
I domini in autorinnovo continuano a essere ricontrollati giornalmente.

Nella pagina reale di vivaiomares.it letta durante questa verifica, who.is esponeva
scadenza `2026-10-01`, record aggiornato `2026-10-02T00:40:56Z`, rilevazione
`2026-10-04T15:49:30.278Z` e stato `ok / autoRenewPeriod`. Questo dato può cambiare;
non conferma una scadenza 2027 né il pagamento al provider. La nuova integrazione
è stata verificata anche sul formato HTML della pagina effettivamente ricevuta.
Le date usate nei test automatici sono invece sintetiche.

Riferimenti: https://who.is/whois/vivaiomares.it,
https://who.is/docs/api, https://www.nic.it/it/gestisci-il-tuo-it/glossario.

## Installazione

Carica lo ZIP in `/root/` ed esegui:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.11-github.zip -d /root/sentinel-update-2.28.11
cp -a /root/sentinel-update-2.28.11/panopticon-lite/api/. /root/panopticon-lite/api/
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

L'ultimo comando deve stampare **2.28.11**. La migrazione del nuovo campo contenente
il confronto delle fonti è automatica e idempotente. I connettori non richiedono
aggiornamenti: WordPress resta 2.30.0, Joomla resta 1.38.0.
Dopo l'installazione premi **Aggiorna tutti in sequenza** in Scadenze domini.
Lo ZIP aggiorna il codice: non è stato distribuito sul server dell'utente.

Verifiche: 121 test Python, 5 test traduzioni e controlli browser sul template reale
per confronto fonti, autorinnovo, avanzamento sequenziale, errori visibili e mobile.
