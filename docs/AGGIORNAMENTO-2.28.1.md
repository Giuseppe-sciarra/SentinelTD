# Sentinel TD 2.28.1 — frequenza screenshot

Il valore già presente in **Impostazioni → Aggiorna anteprime siti ogni** è ora
usato da un timer autonomo, pianificato ogni minuto. Il monitoraggio del sito e
gli eventuali errori del connettore non impediscono più di pianificare le anteprime.
Le pagine vengono catturate anche se il contenuto non è cambiato.

## Applicazione sul server

1. Estrai il pacchetto. Copia il contenuto della cartella `panopticon-lite`
   nella tua cartella del progetto, sovrascrivendo i file del codice.
   Conserva il tuo `.env`: il pacchetto include solo `.env.example`.
2. Dalla cartella dove si trova il tuo `docker-compose.yml`, esegui:

   ```bash
   docker compose up -d --build api worker
   docker compose ps
   ```

   Se usi la struttura già indicata in precedenza, la cartella è `/root/panopticon-lite`.
   Lo shooter e i connettori non richiedono modifiche per questa correzione.
   La nuova colonna del database viene aggiunta automaticamente all'avvio.
3. Nel pannello verifica la versione **2.28.1** e il valore dell'intervallo.
   Non occorre risalvarlo se è già corretto.

## Comportamento

- L'intervallo resta quello configurato nel pannello: da 1 a 720 ore.
- Il timer rilegge il valore ogni minuto: le successive modifiche non richiedono
  un riavvio del worker.
- All'avvio vengono accodati gli screenshot mancanti o scaduti dei siti abilitati.
- Un tentativo fallito o respinto dall'antibot viene ripetuto dopo l'intervallo
  impostato. Non genera una nuova richiesta ogni minuto.
- La data dell'ultima anteprima riuscita non viene falsificata quando la cattura
  fallisce. L'API espone separatamente `shot_attempted_at`.
- I lavori vengono scaglionati di 12 secondi e deduplicati per sito, anche fra timer
  e pulsanti che accodano screenshot. La coda e la durata delle catture possono
  aggiungere ritardo all'esecuzione effettiva.
- Il comando manuale di rigenerazione può essere usato anche prima della scadenza.
- Un sito che continua a respingere lo shooter con un antibot può conservare una
  vecchia anteprima: il timer programma il tentativo, ma non rimuove il blocco del sito.

## Verifica dai log

```bash
docker compose logs --since 30m worker
```

Cerca `ANTEPRIME: accodati`, `SCREENSHOT OK`, `SCREENSHOT FALLITO` oppure
`ANTEPRIMA RESPINTA DAL SITO`.

## Validazione del pacchetto

14 test di regressione per intervalli, cambio impostazioni, siti disabilitati,
indipendenza dal polling, fallimenti HTTP, blocchi antibot, persistenza dei tentativi
e deduplicazione dei lavori arq. I test usano SQLite e fakeredis per i servizi locali
e una risposta HTTP simulata per lo shooter; non sono una prova sul server di produzione.
Sono inclusi anche i controlli di sintassi e la suite di traduzione già presente.
