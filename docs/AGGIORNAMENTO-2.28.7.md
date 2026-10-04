# Sentinel TD 2.28.7

Pacchetto completo nuovo, con versione applicativa **2.28.7** nel backend
(`api/app/version.py`). Include tutte le modifiche fino alla 2.28.6 e i connettori
**WordPress 2.30.0** e **Joomla 1.38.0**, entrambi con il rilevamento Linux aggiornato.

## Aggiornamento del progetto in /root/panopticon-lite

Carica il nuovo file `sentinel-td-2.28.7-github.zip` in `/root/`, poi esegui:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.7-github.zip -d /root/sentinel-update-2.28.7
cp -a /root/sentinel-update-2.28.7/panopticon-lite/api/. /root/panopticon-lite/api/
cp -a /root/sentinel-update-2.28.7/panopticon-lite/connectors/. /root/panopticon-lite/connectors/
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

L'ultima riga deve stampare **2.28.7**. La copia aggiorna il backend, l'interfaccia,
i sorgenti di entrambi i connettori e i requisiti del build. Non sostituisce `.env`
o `docker-compose.yml` e non modifica i volumi con i dati.

Il file ZIP contiene la cartella `panopticon-lite`: copia il suo contenuto nel
progetto esistente, senza creare `/root/panopticon-lite/panopticon-lite`.

In **Impostazioni → Connettori**, seleziona **Usa quello incluso** se è attivo un
pacchetto caricato in precedenza, poi **Aggiorna sui siti** per ciascun CMS.
Verifica **WP 2.30.0 / Joomla 1.38.0**. I pacchetti installabili neutri sono anche
disponibili nella cartella `packages/` di questo archivio.
