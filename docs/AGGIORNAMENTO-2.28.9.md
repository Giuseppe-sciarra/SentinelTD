# Sentinel TD 2.28.9 — retry dei check e falsi offline DNS

L'errore `[Errno -3] Temporary failure in name resolution` indica un errore temporaneo
di risoluzione del nome dal processo Sentinel. Non dimostra che il sito sia spento.
Può dipendere dal resolver del container, dal DNS del server host o dai resolver
a monte. La coincidenza con i controlli di Uptime Kuma non prova la causa: un resolver
condiviso e richieste concentrate possono contribuire al problema.

Il controllo risorse aggiunge GET periodiche ai connettori. Il worker era già limitato
a quattro lavori simultanei; ora anche le GET dei check hanno un limite di quattro
per processo. La connessione HTTP viene riutilizzata quando ancora disponibile,
riducendo nuove connessioni e risoluzioni ripetute. La chiusura da parte dell'hosting
o la scadenza del keepalive richiedono comunque una nuova connessione.

## Nuovo comportamento

- Tre tentativi totali: primo controllo, un retry dopo 15 secondi, un secondo dopo
  altri 15 secondi. Si ritentano solo errori DNS temporanei, ConnectError di rete e
  ConnectTimeout; non si ripetono refresh pesanti per un ReadTimeout.
- Vale per Check ora, Check tutti, check automatici, lettura delle risorse e controllo
  prima dell'aggiornamento, tramite la stessa funzione di GET del connettore.
- WordPress prova l'URL alternativo per errori HTTP/formato; non lo prova per DNS o
  connessione, perché il dominio è lo stesso. Joomla ha gli stessi retry di rete.
- Se il DNS resta temporaneamente indisponibile, il sito compare in giallo come
  **verifica DNS non riuscita**. Non è contato tra i siti OK né tra gli offline.
  Non genera notifiche sito-offline/online per il solo errore DNS. Check e pre-update
  accodano un nuovo controllo dopo un minuto. Gli aggiornamenti restano in sospeso.
- Gli errori definitivi di connessione continuano a usare la conferma offline
  configurata per i check automatici. La modifica non rende un dominio inesistente
  o un certificato non valido un semplice errore DNS temporaneo.
- I log conservano `CHECK RETRY`, `CHECK RECUPERATO` e `CHECK FALLITO`, con causa e
  tentativo, anche dopo che un nuovo check riuscito ha svuotato il campo errore.

## Aggiornamento

Carica `sentinel-td-2.28.9-github.zip` in `/root/`, poi:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.9-github.zip -d /root/sentinel-update-2.28.9
cp -a /root/sentinel-update-2.28.9/panopticon-lite/api/. /root/panopticon-lite/api/
cp -a /root/sentinel-update-2.28.9/panopticon-lite/connectors/. /root/panopticon-lite/connectors/
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

La verifica finale deve stampare **2.28.9**. Dopo l'aggiornamento ricarica il pannello.
Sono conservati lo storico PostgreSQL della 2.28.8, i timer configurabili, il riepilogo
notturno e tutte le precedenti modifiche. Connettori: WP 2.30.0 / Joomla 1.38.0.

I valori predefiniti sono modificabili nell'ambiente del progetto con
`STATUS_CHECK_ATTEMPTS=3` (da 1 a 5) e `STATUS_CHECK_RETRY_SECONDS=15` (da 1 a 60).

## Se l'errore DNS continua

I retry riducono i falsi down, ma non riparano un resolver che non risponde. Per
ispezionare i resolver e conservare i dettagli dei check:

```bash
docker compose exec -T worker cat /etc/resolv.conf
docker compose exec -T api cat /etc/resolv.conf
docker compose logs --no-color --timestamps --since 1h api worker
```

Il resolver Docker `127.0.0.11` in Compose è normale: inoltra le richieste ai DNS
configurati per Docker/host. Non è sufficiente la sua presenza per diagnosticare
il guasto; servono le impostazioni dell'host e prove durante l'errore.
