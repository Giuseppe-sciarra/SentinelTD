# Sentinel TD 2.28.2

CPU, RAM e disco sono tre sezioni separate con divisori. Il valore attuale e l'età della
misura sono distinti dal valore abituale e dal picco. Le soglie sono nei tooltip dei titoli.
La CPU mostra il carico medio rapportato ai core, non l'utilizzo percentuale della CPU.

## Aggiornamento

Copia il contenuto di `panopticon-lite` nella cartella del progetto, mantenendo il tuo `.env`.
Dalla cartella che contiene `docker-compose.yml` esegui:

```bash
docker compose up -d --build api worker
```

In Impostazioni trovi **Rileva CPU, RAM e disco ogni**: 5 minuti di base, da 1 a 180.
Questo timer è separato dall'intervallo di controllo dei siti e viene verificato ogni minuto,
anche all'avvio. Non serve riavviare per cambiare la frequenza. I connettori non cambiano:
la lettura usa il loro endpoint di stato passivo già disponibile, senza scansioni di file,
prove di scrittura o richieste ai server di aggiornamento.

Un solo controllo per server/IP (o per macchina se hai attivato la divisione). Se il primo
sito non risponde si provano altri due dello stesso server. Un errore non genera raffiche:
si ritenta al prossimo intervallo. Le risorse non disponibili sono indicate nel pannello.
Servono connettori compatibili e raggiungibili: RAM da WordPress 2.29 / Joomla 1.37.

La pagina Stato server si aggiorna ogni 30 secondi mentre è visibile. Un campione vecchio
oltre due intervalli mostra **Dati non recenti**. Il tempo della misura è sempre visibile.
La coda può aggiungere un ritardo al campionamento. I grafici si riempiono con le misure
raccolte: nessuno storico viene inventato. Sono incluse le correzioni degli screenshot e
la dicitura **Salva impostazioni** delle versioni precedenti.

## Verifica

```bash
docker compose logs --since 15m worker
```

Cerca `RISORSE SERVER: accodati`, `RISORSE SERVER OK` e gli eventuali errori.

Validazione locale: 23 test Python con SQLAlchemy/arq e servizi locali simulati, suite
traduzioni e controlli della release. Non è una prova sul tuo server di produzione.
