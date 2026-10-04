# Sentinel TD 2.28.12 — scansione domini cadenzata e retry

## Funzionamento

- Un dominio alla volta per worker, con **30 secondi di pausa dopo il completamento**
  prima di iniziare il successivo. Vale per scansioni manuali e automatiche sovrapposte.
  Quindi 100 domini richiedono almeno circa 50 minuti di sole pause, oltre ai tempi
  delle fonti e agli eventuali retry. La pausa non modifica i giorni della scansione.
- In **Impostazioni → Scadenze e avvisi** la pausa è modificabile tra 5 e 600 secondi.
  Il vecchio parametro di simultaneità viene normalizzato a 1 anche per configurazioni
  salvate con valori superiori; ora sono visibili pausa e tentativi.
- **Tentativi per fonte WHOIS**: da 1 a 3, predefinito 3. Timeout, errori DNS temporanei,
  problemi di connessione e risposte temporanee HTTP hanno retry dopo 15 e 30 secondi.
  Per HTTP 429/503 viene rispettato Retry-After fino a 120 secondi; attese più lunghe
  vengono lasciate al giro successivo senza ritentare prima del termine indicato.
- Timeout registro WHOIS 20 secondi; timeout HTTPS 20 secondi, connessione 10 secondi.
  La fonte ha un limite totale di 45 secondi per tentativo. La coda per una fonte non
  consuma tale limite. Richieste alla stessa fonte hanno almeno 3 secondi di pausa.
- CAPTCHA/antibot, domini inesistenti o differenti e scadenze realmente non esposte
  restano errori distinti. Rallentare può aiutare gli errori temporanei, ma non rende
  accessibile una fonte che richiede una verifica o blocca il traffico del server.

**Riscansiona registri ogni** continua a essere espresso in giorni. Il giro automatico
quotidiano aggiorna i domini dovuti all'intervallo impostato. Errori e domini entro
30 giorni dalla scadenza vengono ricontrollati giornalmente. È stato corretto il
confronto dell'intervallo: terminare pochi minuti dopo l'orario del cron non fa più
saltare il giorno previsto per la scansione.

Un fallimento conserva la data conosciuta e segnala il problema. Un successivo lookup
valido aggiorna la scadenza e cancella l'errore. Ogni risultato viene salvato subito,
anche nelle scansioni automatiche; un arresto successivo non perde i domini completati.
Il giro manuale e quello automatico hanno limite di sei ore, per consentire scansioni
più lente. Se un giro viene interrotto, il successivo riprende dai domini ancora dovuti.
Il controllo di un insieme selezionato usa un solo job, per lasciare spazio ai job
ordinari dei siti e delle risorse server. Su installazioni con più worker distinti,
la cadenza è condivisa all'interno di ciascun processo worker.

## Aggiornamento

Carica `sentinel-td-2.28.12-github.zip` in `/root/`, poi:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.12-github.zip -d /root/sentinel-update-2.28.12
cp -a /root/sentinel-update-2.28.12/panopticon-lite/api/. /root/panopticon-lite/api/
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

Il comando finale deve stampare **2.28.12**. I valori nuovi compaiono anche prima del
primo salvataggio grazie alla normalizzazione delle impostazioni; cambiare pausa e
tentativi non richiede riavviare il worker. I connettori restano WordPress 2.30.0 e
Joomla 1.38.0: non devono essere aggiornati. Il codice non è stato distribuito sul
server dell'utente.

Verifiche: 144 test Python e controlli di traduzione/browser sul template reale.
