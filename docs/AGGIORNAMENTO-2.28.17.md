# Sentinel TD 2.28.17

## Registro offline

Apri **Statistiche → Registro offline**. Il registro è una lista persistente nel
PostgreSQL già presente: non aggiunge richieste HTTP/DNS, cron, diagnostica o
notifiche. Le notifiche già configurate restano governate dalla loro impostazione.

- Un episodio viene registrato quando il normale check conferma lo stato offline
  dopo i tentativi e l'attesa configurati. Gli errori temporanei recuperati prima
  della conferma, DNS temporanei e richieste rinviate non vengono contati.
- Una riga per episodio: dal primo errore al primo check riuscito. I check falliti
  successivi aggiornano la stessa riga; i retry della stessa richiesta non sono
  check distinti. Il conteggio “Check offline” parte dalla conferma.
- Gli orari sono sempre **Europe/Rome**, anche se il browser usa un altro fuso,
  con ora legale automatica. Il tooltip mostra l'orario UTC corrispondente.
- Durata osservata dai check, non misura continua di uptime: l'effettivo ritorno
  online può precedere il primo check riuscito. Gli episodi senza un ritorno
  online rilevato mostrano l'ultima conferma, anche se i controlli vengono sospesi.
- Filtri 7/30/90/365 giorni, sito e IP/server; paginazione di 50 righe. I riepiloghi
  restano globali per il periodo; i tre contatori e la lista seguono i filtri.
- I totali per IP sommano episodi dei singoli siti, non guasti accertati del server.
  L'IP è quello già risolto per il check e può appartenere a un proxy/CDN.
  Se non noto, viene conservato il dominio; il registro non fa una nuova risoluzione.
- Lo storico parte da questo aggiornamento. Non è possibile ricostruire gli
  episodi precedenti dal solo stato corrente. Gli episodi ancora aperti rimangono
  visibili anche se iniziati prima del periodo selezionato.
- La tabella viene creata automaticamente all'avvio. Dati mantenuti dopo recreate
  e dopo rinomina/eliminazione dei siti; nessuna scadenza automatica del registro.

## Aggiornamento

Copia i file nuovi nella cartella del progetto e ricrea API e worker:

```bash
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
```

Versione attesa **2.28.17**. Connettori WordPress **2.32.0** e Joomla **1.40.0**
invariati: per questa modifica non serve aggiornarli. Il registro rimane nel
volume PostgreSQL esistente.
