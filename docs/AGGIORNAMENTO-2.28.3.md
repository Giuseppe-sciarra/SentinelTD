# Sentinel TD 2.28.3

I dettagli del server hanno due colonne della stessa larghezza e altezza:

- A sinistra: Siti, Domini, Aggiornamenti, Problemi e Peso.
- A destra: CPU, RAM e disco, con spazi più compatti e separatori più chiari.

L'altezza si adatta ai contenuti del server. Su schermi piccoli le schede passano a
una sola colonna e mantengono la propria altezza naturale.

Lo storico di CPU e RAM usa caselle più grandi: tutta la casella è selezionabile
con mouse, tastiera o tocco. Il tooltip mostra l'intervallo di mezz'ora, il valore
medio e lo stato. Le caselle grigie indicano una misura o uno stato non disponibile.
La frequenza delle misure resta quella impostata: una casella riassume i campioni
raccolti durante mezz'ora. La CPU continua a mostrare il carico rapportato ai core.
Le linee tratteggiate e le diciture isolate 100% e 90% sono state rimosse; le soglie
restano nei tooltip dei titoli CPU e RAM.

Sono incluse tutte le correzioni precedenti: timer screenshot autonomo, pulsante
**Salva impostazioni**, campionamento delle risorse configurabile e aggiornamento
automatico della pagina Stato server.

## Installazione

Copia il contenuto di `panopticon-lite` nella cartella del progetto, mantenendo il tuo
`.env`. Dalla cartella che contiene `docker-compose.yml` esegui:

```bash
docker compose up -d --build api worker
```

Se il browser mantiene la vecchia interfaccia, ricarica la pagina con Ctrl+F5.

## Verifiche

Controllo nel browser su desktop e mobile: altezza e bordo inferiore delle colonne
uguali, storico completo e parziale, stati mancanti, tooltip su mouse/tastiera/tocco,
assenza di scorrimento orizzontale e di errori Alpine. Controlli della release, suite
traduzioni e 23 test Python. La verifica usa dati simulati, non il server di produzione.
