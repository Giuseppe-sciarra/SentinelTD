# Sentinel TD 2.28.8 — storico risorse persistente

CPU, RAM e disco vengono ora salvati in PostgreSQL, nel volume persistente già
utilizzato da Sentinel (`pg_data`). Riavviare o ricreare Redis, API o worker non
azzera più le misure. Il grafico continua a mostrare le ultime 24 ore; i campioni
conservati nel database per 48 ore vengono ripuliti quando arrivano nuove misure.

API e worker creano automaticamente la tabella all'avvio e importano una sola volta
lo storico ancora presente in Redis, mantenendo gli orari originali. L'importazione
non elimina le chiavi Redis. Le misure già perse prima di questa modifica non
possono essere ricostruite. Non occorre modificare Compose o ricreare Redis.

## Aggiornamento

Carica `sentinel-td-2.28.8-github.zip` in `/root/`, poi esegui:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.8-github.zip -d /root/sentinel-update-2.28.8
cp -a /root/sentinel-update-2.28.8/panopticon-lite/api/. /root/panopticon-lite/api/
cp -a /root/sentinel-update-2.28.8/panopticon-lite/connectors/. /root/panopticon-lite/connectors/
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

La verifica finale deve stampare **2.28.8**. I connettori restano **WordPress 2.30.0**
e **Joomla 1.38.0**. Tutte le modifiche precedenti, il timer configurabile, le fasce
di mezz'ora e le notifiche sono conservati. Il backup PostgreSQL include lo storico.

Per controllare l'importazione:

```bash
docker compose logs --since 10m api worker
```

La riga `Storico risorse persistente` segnala l'importazione. Se Redis non era
raggiungibile durante il primo avvio, l'importazione viene ritentata al successivo
avvio; le nuove misure vengono comunque salvate direttamente nel database.
