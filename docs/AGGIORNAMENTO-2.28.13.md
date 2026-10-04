# Sentinel TD 2.28.13

Nella lista Stato server l'intestazione Carico diventa **CPU · Carico**.
La dashboard ricarica avvisi e contatori sicurezza quando la apri e ogni minuto; gli avvisi critici hanno priorità e gli errori di caricamento restano visibili. Anche sicurezza e scadenze aperte si aggiornano ogni minuto, senza avviare nuove scansioni.

Risorse server: se i primi tre siti danno dati incompleti, al ciclo successivo prova gli altri siti dello stesso gruppo, mantenendo al massimo tre richieste per ciclo. Nei log del worker le righe `RISORSE SERVER PARZIALI` indicano sito, versione connettore, dati mancanti e fonti disponibili. Questo può recuperare dati esposti da un altro sito; non garantisce la lettura se tutti gli account del server la impediscono.

Include tutte le modifiche della 2.28.12. Connettori WordPress 2.30.0 e Joomla 1.38.0.

Carica lo ZIP in `/root/` e aggiorna:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.13-github.zip -d /root/sentinel-update-2.28.13
cp -a /root/sentinel-update-2.28.13/panopticon-lite/api/. /root/panopticon-lite/api/
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

Il comando deve stampare 2.28.13. Non è stata eseguita una distribuzione sul server.
