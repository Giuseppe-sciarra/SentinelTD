# Sentinel TD 2.28.14

La sezione Stato server è stata rimossa completamente: pagina, menu, API di overview,
CPU/RAM/disco, storico delle risorse, campionamento, impostazioni e divisione per macchina.
Sono stati rimossi anche il giro di diagnostica notturna, la notifica Server · riepilogo
notturno e il relativo invio programmato. I vecchi lavori notturni già accodati non
eseguono richieste né notifiche dopo il riavvio del worker.

Entrambi i connettori sono aggiornati: WordPress **2.31.0**, Joomla **1.39.0**.
Non raccolgono più le risorse, non leggono /proc e non eseguono i comandi Linux di
fallback per CPU/RAM/disco, né nei check ordinari né nella diagnostica.

Restano i normali check dei siti e degli aggiornamenti, screenshot, sicurezza e
scadenze. La diagnostica manuale del singolo sito resta disponibile per verificare
spazio utile agli aggiornamenti, log e integrità del core. Il freno agli aggiornamenti
per IP resta disponibile: non è un controllo delle risorse.

## Aggiornamento sul server

Carica il nuovo ZIP in `/root/sentinel-td-2.28.14-github.zip`, poi esegui:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.14-github.zip -d /root/sentinel-update-2.28.14
cp -a /root/sentinel-update-2.28.14/panopticon-lite/api/. /root/panopticon-lite/api/
cp -a /root/sentinel-update-2.28.14/panopticon-lite/connectors/. /root/panopticon-lite/connectors/
cp -a /root/sentinel-update-2.28.14/panopticon-lite/packages/. /root/panopticon-lite/packages/
cp /root/sentinel-update-2.28.14/panopticon-lite/VERSION.txt /root/panopticon-lite/VERSION.txt
rm -f /root/panopticon-lite/api/app/server_metrics.py /root/panopticon-lite/api/app/load_history.py /root/panopticon-lite/api/app/nightly.py /root/panopticon-lite/api/app/routers/server_status.py
rm -f /root/panopticon-lite/connectors/wordpress/td-panopticon/includes/linux-metrics.php /root/panopticon-lite/connectors/joomla/plg_system_tdpanopticon/src/Support/LinuxMetrics.php
rm -f /root/panopticon-lite/packages/sentinel-td-wp-2.30.0.zip /root/panopticon-lite/packages/sentinel-td-jm-1.38.0.zip
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

L'ultima riga deve stampare **2.28.14**. Database e volumi non vengono eliminati.
La rimozione delle richieste periodiche server entra in vigore al riavvio del worker.

## Aggiorna anche i connettori sui siti

In **Impostazioni → Connettori**, verifica WordPress **2.31.0** e Joomla **1.39.0**
e usa l'installazione sui siti con versione precedente, per entrambi i tipi.
I pacchetti installabili sono inclusi anche nella cartella `packages` dello ZIP.
Un semplice check non sostituisce il connettore: sui siti con la vecchia versione
le vecchie letture possono ancora essere eseguite durante i normali check del CMS,
finché non viene installato il nuovo pacchetto.

## Normali check dei siti

I GET del connettore vengono coordinati tra API e worker: massimo due simultanei in
Sentinel e uno per IP, con due secondi di pausa. Il limite non controlla Uptime Kuma
né i POST di aggiornamento. I timeout transitori diventano «Da confermare» prima
di «Offline», secondo l'attesa già configurata nelle impostazioni; ricontrollo
automatico dopo un minuto. Con attesa zero la segnalazione resta immediata.

Non è stata verificata la causa sul server in produzione: la rimozione elimina
le richieste aggiuntive e i comandi delle risorse, ma altri problemi di rete,
hosting o CMS possono comunque produrre timeout.
