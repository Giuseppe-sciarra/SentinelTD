# Sentinel TD 2.28.16

In Impostazioni, accanto all'attesa per la conferma Offline, trovi:

- **Tentativi totali per un check del sito:** 1–5. Include il primo controllo;
  3 equivale al primo controllo più due retry. Default 3.
- **Pausa tra i retry del sito:** 1–60 secondi. Default 15.

Salva impostazioni: i valori sono persistenti nel database e vengono applicati
ai nuovi check manuali e automatici, senza riavviare i container. Un check già
in corso termina con i valori che aveva quando è iniziato.
I valori configurati precedentemente tramite ambiente sono usati come default
finché non vengono salvate le nuove impostazioni nel pannello.

I retry valgono per errori di connessione e DNS temporanei. I timeout di lettura
passano al ricontrollo successivo. Le operazioni POST di aggiornamento non
vengono duplicate. L'attesa Offline resta nella sua impostazione esistente.

Aggiorna i file del progetto con questo ZIP e ricrea API e worker:

```bash
cd /root/panopticon-lite
docker compose up -d --build --force-recreate api worker
docker compose exec -T api python -c 'from app.version import __version__; print(__version__)'
```

Deve stampare **2.28.16**. I connettori restano WordPress **2.32.0** e Joomla
**1.40.0**: per questa modifica non serve reinstallarli. Restano tutte le
rimozioni di Stato server e diagnostica della 2.28.15.
