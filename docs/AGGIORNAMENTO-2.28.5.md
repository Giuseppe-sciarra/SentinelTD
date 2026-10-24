# Sentinel TD 2.28.5

## Notifiche dei controlli notturni

Apri **Notifiche → Server · riepilogo notturno**.

- Scegli **Orario di invio** e **Fuso orario**. Il valore iniziale è **09:00, Europe/Rome**, con cambio automatico dell'ora legale.
- Scegli Email, Telegram oppure entrambi. Al primo aggiornamento vengono mantenuti i canali già scelti per gli avvisi di spazio/log e integrità del core.
- **Invia solo se ci sono problemi** è attivo inizialmente. Disattivalo per ricevere anche il riepilogo dei controlli regolari.
- Modifica oggetto e messaggi con l'editor visuale. Le anteprime Email e Telegram mostrano server, CPU (carico per core), RAM, disco, siti, spazio scrivibile, percorsi e dimensioni dei log, file del core ed eventuali controlli non riusciti. Orario e fuso scelti aggiornano l'anteprima.
- Premi **Salva modifiche**: il nuovo orario viene letto dal worker senza ulteriori riavvii.

Il controllo esistente resta alle 03:40 nel fuso del worker; viene mantenuta la prova di spazio da 50 MB. Questi lavori non inviano più avvisi immediati separati per sito. I risultati vengono conservati nel database, raggruppati per server e consegnati dopo il termine di tutti i controlli, all'orario scelto o appena completati se terminano più tardi.

Un orario precedente al rilevamento sposta l'invio al giorno successivo. Riavvii e invii falliti conservano i dati in attesa: i tentativi di recupero rispettano comunque l'orario giornaliero e non reinviano sui canali già consegnati. Eventuali rilevamenti arretrati vengono riuniti in un solo riepilogo per canale al giorno. Un lavoro rimasto incompleto per oltre 24 ore viene indicato nel riepilogo come controllo non completato.

Su Telegram i report entro il limite sono un solo messaggio. Quelli più lunghi arrivano con un unico invio come documento HTML contenente tutti i dettagli. Email riceve sempre un unico messaggio completo. I siti disabilitati, rimossi o silenziati vengono esclusi dalla consegna.

Le voci **Spazio e log · controlli manuali** e **File del core da controllare** gestiscono la diagnostica avviata manualmente; la raccolta notturna usa la nuova voce Server.

## Aggiornamento

Aggiorna i sorgenti conservando `.env` e i dati esistenti, poi dalla cartella del progetto:

```sh
docker compose up -d --build api worker
```

Le nuove tabelle vengono create automaticamente all'avvio. Il pacchetto conserva tutte le modifiche delle versioni precedenti, inclusi timer screenshot, campionamento risorse, dettagli server su due colonne, footer e storico a mezz'ore.

## Verifiche

48 test Python, 5 test dei cataloghi e controllo della release pulita. Verifica del pannello reale con Alpine su desktop/mobile: anteprime Email/Telegram, salvataggio di orario e fuso, assenza di errori JavaScript e overflow. I test di consegna usano trasporti simulati.
