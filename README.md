# Italia Compete — Fonti

Prima versione da collaudare. Non è ancora un servizio operativo.

Un solo worker Render conserva sessione Telegram e registro SQLite sul disco persistente. Legge esclusivamente Part 2 (-1001295597629) ed Economaniacs (-1001302683686). Scarica PDF fino a 100 MiB, li salva nella cartella Dropbox configurata e verifica dimensione e content hash prima di registrare l'esito. Non invia messaggi, non pubblica post e non modifica WhatsApp o routine di Papà.

## Avvio assistito

1. Creare un'app personale su https://my.telegram.org (API development tools). Inserire api_id e api_hash **solo nei campi segreti Render**.
2. Creare un'app Dropbox in https://www.dropbox.com/developers/apps con accesso allo spazio richiesto (Full Dropbox), scope `files.metadata.read` e `files.content.write`. La cartella configurata va creata prima dell'acquisizione. L'autorizzazione ChatGPT esistente non viene riutilizzata.
3. Importare questo repository come Blueprint in Render, nello spazio confermato Fred's workspace. Il worker e il disco sono a pagamento: verificare e approvare il preventivo prima di Apply. Nessun acquisto è stato eseguito dall'assistente.
4. Compilare i tre valori richiesti. Lasciare `INGESTION_ENABLED=false`. Controllare che `DROPBOX_NAMESPACE_ID` e `DROPBOX_BASE` corrispondano all'account autorizzato.
5. Nella **Shell privata del servizio Render**, eseguire personalmente `python authorize.py`. I codici e la password 2FA vengono richiesti senza eco; non inserirli nella chat, nei log o nel repository. L'autorizzazione Dropbox usa PKCE e salva un refresh token sul disco protetto. La sessione Telegram concede accesso all'account; la restrizione alle chat è implementata dal programma, non dal permesso Telegram.
6. Verificare le condizioni delle fonti e il permesso di acquisizione/uso previsto. Impostare START_FROM alla data iniziale desiderata con fuso orario. Abilitare l'acquisizione solo per il collaudo autorizzato, poi riavviare.
7. Verificare un PDF su Dropbox, ripetere il controllo senza duplicazioni e riavviare il servizio per verificare la persistenza. Non dichiarare il collaudo completato prima di averlo osservato.

## Comportamento e limiti

- Le fonti con protezione al salvataggio o cancellazione a tempo rilevata vengono saltate. Part 2 potrebbe quindi risultare non acquisibile: il programma non aggira queste restrizioni.
- Le condizioni Telegram per la raccolta destinata a impieghi AI devono essere rispettate (https://telegram.org/tos/content-licensing). Il trasferimento a Dropbox non cambia le condizioni dei contenuti.
- Il programma controlla tutte le chat consentite ogni 15 minuti. Un guasto in una chat non impedisce il tentativo sull'altra. Gli errori vengono registrati senza dettagli potenzialmente sensibili e ritentati; FloodWait segue l'attesa imposta da Telegram.
- Alla prima acquisizione legge da START_FROM; poi usa un cursore persistente per ciascuna chat. Modifiche a vecchi messaggi non vengono riacquisite automaticamente.
- Deduplica per hash Dropbox anche fra chat diverse. Un upload ambiguo viene verificato al percorso deterministico al prossimo tentativo. File remoti incompatibili non vengono sovrascritti.
- Non deduce la data di edizione dal messaggio: i file sorgente mantengono nome e riferimento tecnico per la revisione editoriale.
- `status.json` mostra l'ultimo evento, non una certificazione del buon esito di entrambe le chat. Il registro SQLite conserva lo storico delle fonti processate. Non è ancora implementato un avviso automatico in ChatGPT: occorre integrare il monitoraggio con le routine, senza crearne duplicati.
- Sessioni e token sono esclusi da Git. Il disco contiene credenziali sensibili: limitarne l'accesso all'account Render autorizzato e non esportare snapshot in cartelle editoriali.
- Autorizzazioni revocate richiedono intervento umano. Nessuna promessa di accesso permanente senza possibili revoche.

## Verifica locale

`python -m unittest discover -s tests -v`

I test locali verificano restrizioni, integrità remota, recupero di upload ambiguo e persistenza del registro. Non sostituiscono i test reali Telegram/Dropbox/Render.
