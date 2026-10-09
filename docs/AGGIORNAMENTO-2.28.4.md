# Sentinel TD 2.28.4

## Legenda e storico CPU/RAM

Una legenda visibile spiega come leggere le caselle:

- Ogni casella mostra la media dei campioni di una fascia di 30 minuti.
- Si legge da sinistra a destra, riga per riga, dal più vecchio al più recente.
- Verde: normale; giallo: elevato; rosso: molto elevato; grigio: non disponibile.
- Il bordo blu indica la mezz’ora in corso, che riceve le nuove misure.
- Le caselle precedenti mantengono lo storico delle ultime 24 ore.

Gli intervalli sono fissi, per esempio **00:00–00:30** e **00:30–01:00**.
L'orario del tooltip non cambia a ogni minuto. Sono inclusi data, fascia oraria,
valore medio e stato. Le fasce ai bordi della finestra di 24 ore possono essere
parziali: non vengono inventate letture precedenti o future.

**Attuale** resta l'ultima lettura del server, con l'orario indicato da **Ultima misura**.
La frequenza segue **Rileva CPU, RAM e disco ogni** nelle impostazioni; la pagina si
aggiorna ogni 30 secondi. Il refresh della pagina non equivale a una nuova misura.

## Versione in basso

Il caricamento delle cartelle allungava il menu e spingeva il piè di pagina fuori
vista. Adesso scorre solo il menu: logo, versione e crediti restano visibili.
La versione viene letta anche dopo l'accesso con password, TOTP o passkey. Risposte
vuote ed errori di rete conservano l'ultima versione letta correttamente. Una prima
lettura fallita viene ritentata al prossimo aggiornamento dei siti.

Sono incluse tutte le modifiche precedenti, comprese le due colonne della stessa
altezza e i timer indipendenti per screenshot e risorse.

## Aggiornamento

Copia il contenuto di `panopticon-lite` nella cartella del progetto mantenendo il tuo
`.env`, poi esegui dalla cartella che contiene `docker-compose.yml`:

```bash
docker compose up -d --build api worker
```

Ricarica con Ctrl+F5 se il browser conserva la vecchia interfaccia.

## Verifica

26 test Python, suite traduzioni, controllo sintassi e release. Verifiche nel browser
su desktop e mobile: altezza delle colonne, legenda, fasce allineate, tooltip mouse,
tastiera e tocco; versione visibile con 50 cartelle, dopo scroll, refresh e accesso.
Le prove usano dati e servizi simulati, non il server di produzione.
