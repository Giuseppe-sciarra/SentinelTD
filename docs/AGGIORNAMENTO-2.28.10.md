# Sentinel TD 2.28.10 — scadenze WHOIS e aggiornamento in sequenza

## Problemi individuati nel codice

- Per .it una pagina WHOIS esterna aveva priorità sul registro. Una data vecchia letta
  da quella pagina impediva di interrogare il registro; una pagina con CAPTCHA non
  fornisce invece alcuna data utilizzabile.
- In caso di errore la vecchia scadenza veniva conservata, ma l'interfaccia nascondeva
  l'errore quando esisteva già una data: poteva continuare a mostrare "scaduto".
- I cambiamenti WHOIS non erano inclusi nell'impronta del pannello; "Aggiorna WHOIS"
  effettuava una sola ricarica dopo 12 secondi, anche se il lavoro era ancora in coda.

Questi difetti spiegano come una vecchia data possa restare visibile. Senza il database
o i log dell'installazione non è possibile attribuire il caso specifico a una sola causa.
La scadenza attuale di vivaiomares.it non è stata confermata dal registro durante questa
verifica: l'ambiente di verifica non ha raggiunto il servizio WHOIS autorevole e la
pagina secondaria ha richiesto una verifica CAPTCHA. Nessuna data è stata inventata.

## Nuovo comportamento

- Per .it viene interrogato prima **whois.nic.it**, il server WHOIS pubblicato da IANA.
  Per gli altri TLD RDAP resta la prima fonte, poi WHOIS del registro e infine HTTPS.
- Se il registro è irraggiungibile e la fonte secondaria riporta una data già passata,
  quella data è segnalata come **Data da verificare**. I fallimenti non cancellano una
  scadenza già conosciuta e non producono avvisi di scadenza basati su dati non verificati.
- Gli errori sono visibili anche quando esiste una vecchia data. I domini con lookup
  fallito vengono ritentati al successivo giro quotidiano, senza attendere una settimana.
- Nel pannello **Scadenze domini** il pulsante **Aggiorna tutti in sequenza** forza un
  nuovo lookup di tutti i domini registrabili, uno alla volta, e mostra quanti sono
  controllati e quanti hanno restituito un errore. I sottodomini vengono raggruppati.
- Ogni risultato viene salvato appena è disponibile. Un dominio in errore non ferma
  il giro. La durata dipende dalla coda del worker e dai tempi delle fonti WHOIS.
- Un secondo giro sequenziale non viene accodato mentre il primo è in coda o in corso.
  La scansione automatica conserva la concorrenza impostata nelle Impostazioni.
- I pulsanti di decisione "Rinnova / Non rinnova / Da decidere" mantengono il loro
  significato: registrano la decisione dell'agenzia, non rinnovano il dominio al registrar.

## Aggiornamento

Carica `sentinel-td-2.28.10-github.zip` in `/root/`, poi:

```bash
(
set -e
unzip -oq /root/sentinel-td-2.28.10-github.zip -d /root/sentinel-update-2.28.10
cp -a /root/sentinel-update-2.28.10/panopticon-lite/api/. /root/panopticon-lite/api/
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
)
```

Deve stampare **2.28.10**. Ricarica il pannello, apri **Scadenze domini** e premi
**Aggiorna tutti in sequenza**, oppure aggiorna il solo vivaiomares.it con l'icona WHOIS.
Non occorre aggiornare i connettori: WordPress 2.30.0 e Joomla 1.38.0 restano invariati.
Le modifiche della 2.28.9 e lo storico PostgreSQL della 2.28.8 sono conservati.

Se un dominio resta **Data da verificare**, leggi l'errore sotto la data: un DNS che
non risponde, una porta TCP/43 filtrata o un CAPTCHA richiedono una verifica delle
fonti/connettività. Il pannello non li trasforma in una conferma di dominio scaduto.

Fonte del server .it: https://www.iana.org/domains/root/db/it.html
