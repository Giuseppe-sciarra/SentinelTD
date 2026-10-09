# Sentinel TD 2.28.15

Rimozione completa della diagnostica aggiuntiva richiesta:

- Notifiche «File del core da controllare» e «Spazio e log del sito», anteprime e invii.
- Scansioni dei file del core, impronte e ricerche di log grandi, anche fuori dal sito.
- Calcolo del peso del sito/database, storico esposto nel pannello e azione diagnostica.
- Prove di scrittura e cancellazione di file temporanei da decine/centinaia di MB.
- Prove prima di aggiornamenti e backup WordPress e dopo un aggiornamento fallito;
  richieste HEAD per stimare i pacchetti collegate a quelle prove.
- Campi diagnostici nei report predefiniti e relativi avvisi della dashboard.

Resta la rimozione di Stato server, CPU/RAM/disco, campionamenti e giro notturno della
2.28.14. I lavori di diagnostica già accodati vengono scartati senza contattare i siti.
I dati preesistenti nel database non vengono cancellati e non vengono più esposti.

I connettori sono WordPress **2.32.0** e Joomla **1.40.0**. Il normale aggiornamento
CMS/plugin/temi, i backup effettivi, il ripristino e gli errori restituiti dal CMS
restano disponibili. Nessun test aggiuntivo di scrittura precede tali operazioni.

## Aggiornamento sul server

Carica lo ZIP in `/root/sentinel-td-2.28.15-github.zip`, poi esegui:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.15-github.zip -d /root/sentinel-update-2.28.15
cp -a /root/sentinel-update-2.28.15/panopticon-lite/api/. /root/panopticon-lite/api/
cp -a /root/sentinel-update-2.28.15/panopticon-lite/connectors/. /root/panopticon-lite/connectors/
cp -a /root/sentinel-update-2.28.15/panopticon-lite/packages/. /root/panopticon-lite/packages/
cp /root/sentinel-update-2.28.15/panopticon-lite/VERSION.txt /root/panopticon-lite/VERSION.txt
rm -f /root/panopticon-lite/api/app/diagnostics.py /root/panopticon-lite/api/app/server_metrics.py /root/panopticon-lite/api/app/load_history.py /root/panopticon-lite/api/app/nightly.py /root/panopticon-lite/api/app/routers/server_status.py
rm -f /root/panopticon-lite/connectors/wordpress/td-panopticon/includes/linux-metrics.php /root/panopticon-lite/connectors/joomla/plg_system_tdpanopticon/src/Support/LinuxMetrics.php
rm -f /root/panopticon-lite/packages/sentinel-td-wp-2.30.0.zip /root/panopticon-lite/packages/sentinel-td-jm-1.38.0.zip /root/panopticon-lite/packages/sentinel-td-wp-2.31.0.zip /root/panopticon-lite/packages/sentinel-td-jm-1.39.0.zip
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

L'ultima riga deve stampare **2.28.15**. Il database e i volumi restano al loro posto.

## Aggiorna entrambi i connettori

In **Impostazioni → Connettori**, verifica WordPress **2.32.0** e Joomla **1.40.0**
e installali sui siti con versione precedente, per entrambi i tipi. I pacchetti
installabili sono nella cartella `packages` dello ZIP. Un check ordinario non
installa il connettore: la rimozione delle prove prima degli aggiornamenti/backup
è effettiva su ogni sito quando riceve il nuovo connettore.

## Cosa facevano prima

Il giro automatico core/log/peso era notturno ed è stato eliminato nella 2.28.14.
La diagnostica poteva essere avviata anche manualmente. Le prove di scrittura del
connettore WordPress potevano invece scattare durante aggiornamenti o backup,
a qualsiasi ora. Queste ultime vengono eliminate nella 2.28.15.

Non è stata verificata la causa dei timeout sul server in produzione. La rimozione
elimina questo lavoro aggiuntivo, ma non costituisce una diagnosi definitiva del
problema di connettività osservato anche da Uptime Kuma.
