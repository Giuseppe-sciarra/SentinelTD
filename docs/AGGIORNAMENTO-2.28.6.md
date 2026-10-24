# Sentinel TD 2.28.6 — statistiche Linux

## Rilevamento più completo

Connettori aggiornati: **WordPress 2.30.0** e **Joomla 1.38.0**.

Il rilevamento usa interfacce e comandi Linux comuni per ampliare la copertura di AlmaLinux, Rocky/RHEL/CentOS, Debian, Ubuntu e sistemi con strumenti minimi come Alpine/BusyBox. La disponibilità dipende dai programmi installati e dai permessi dell'ambiente PHP.

| Risorsa | Metodi, in ordine |
|---|---|
| Carico CPU, 1/5/15 minuti | `sys_getloadavg`, `/proc/loadavg`, `uptime` |
| Numero di core | Cache WordPress se presente, `/proc/cpuinfo`, `/sys/devices/system/cpu/online`, `getconf _NPROCESSORS_ONLN`, `nproc`, `lscpu -p=CPU,ONLINE`, `lscpu` |
| RAM e swap | `/proc/meminfo`, `free -b`, `free -k`, `free` |
| Disco totale e libero | Funzioni PHP, poi `df -Pk` sulla cartella del sito |

Le letture dirette precedono i comandi. RAM parziale (totale noto, disponibile assente) attiva i metodi successivi. Sono gestiti i formati procps moderni e vecchi e gli output BusyBox, con conversione corretta di byte/KiB. Zero RAM disponibile, zero swap e zero spazio libero sono valori validi.

I comandi sono di sola lettura, senza sudo, installazioni o modifiche al server. Vengono eseguiti attraverso le funzioni PHP consentite (`proc_open`, oppure `shell_exec`/`exec` con `timeout`), con lingua e percorso comandi deterministici, limite di tempo e limite all'output. Se le letture e i comandi sono tutti vietati, la misura resta non disponibile. La CPU conserva il significato di carico per core, non percentuale reale di utilizzo.

## Misure parziali da siti dello stesso server

Il campionamento non si ferma più quando il primo sito restituisce soltanto il disco o altre risorse parziali. Conserva ogni lettura effettiva e prova fino a tre siti della stessa macchina, fermandosi quando carico, core, RAM e disco sono disponibili. Le misure restano associate al sito di origine e lo storico del server le combina come prima; RAM e disco non vengono costruiti unendo totali e valori liberi provenienti da letture diverse.

Il timer configurabile, le fasce dello storico, il riepilogo notturno e tutte le altre modifiche precedenti sono conservati.

## Aggiornamento

Aggiorna i sorgenti conservando `.env` e i dati, poi esegui dalla cartella del progetto:

```sh
docker compose up -d --build api worker
```

Questa modifica richiede anche i nuovi connettori **installati sui siti**. In **Impostazioni → Connettori**, verifica le versioni **WP 2.30.0 / Joomla 1.38.0**, quindi premi **Aggiorna sui siti** per ciascun CMS. Se è selezionato un vecchio pacchetto caricato, premi **Usa quello incluso** per usare i nuovi sorgenti. Se preferisci, l'aggiornamento automatico notturno già previsto provvederà alla distribuzione quando attivo.

Due pacchetti installabili neutri sono inclusi nella cartella `packages/` di questa release. Non contengono indirizzo del pannello o chiavi di registrazione; per un pacchetto WordPress già configurato usa **Scarica** dal pannello.

## Verifiche

- 52 test Python, inclusi completamento delle misure parziali, core ricevuti separatamente, disco pieno e limite di tre siti.
- 45 controlli PHP con fixture procps moderne/vecchie e BusyBox, letture native e comandi reali su Linux, comandi mancanti e interruzione di comandi lenti.
- Ripetizione dei controlli con `proc_open` disabilitato, con `proc_open` e `shell_exec` disabilitati, e con le funzioni native CPU/disco disabilitate. Ambiente senza alcuna funzione di esecuzione gestito senza errori.
- Controllo sintassi di tutti i file PHP, chiamata delle funzioni di rilevamento effettive dei due connettori e 5 test dei cataloghi.

Le fixture verificano i formati e i metodi; non equivalgono a un'installazione di prova su ogni distribuzione o a una verifica dei permessi dei tuoi server.

Riferimenti dei metodi: [Linux /proc](https://www.kernel.org/doc/html/latest/filesystems/proc.html), [GNU nproc](https://www.gnu.org/software/coreutils/manual/html_node/nproc-invocation.html), [procps](https://github.com/procps-ng/procps).
