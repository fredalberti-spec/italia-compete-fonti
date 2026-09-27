# Irina: ricezione WhatsApp, appunti e corrispondenza

Implementazione preparata il 27 settembre 2026. **Non ancora attivata né collaudata su WhatsApp reale.**
Si avvia come servizio web Render separato. Nessuna modifica a worker.py, papa.py, papa_whatsapp.py,
alla sessione Telegram o alle automazioni esistenti. Non lanciare un secondo worker Telegram.

## Cosa fa

- Riceve messaggi firmati da Meta per il solo account e numero Irina già configurati.
- Riconosce i due numeri di Fred tramite `IRINA_OWNER_PHONES`, configurati come segreti/variabili Render.
- Risponde alle richieste di Fred sul numero effettivamente usato. Il recapito italiano è il principale per gli inoltri.
- Conserva i messaggi di altri mittenti come corrispondenza, senza attribuire loro autorità di comando.
- Inoltra al recapito principale di Fred il testo con nome dichiarato e numero del mittente; per gli allegati
  inoltra anche il contenuto quando la finestra WhatsApp è aperta. Il nome del profilo non prova l'identità.
- Fred può rispondere al messaggio inoltrato con `Rispondi: testo`, oppure con `Rispondi ID: testo`.
  Il testo viene inviato esattamente, al mittente della comunicazione identificata, senza una risposta autonoma del modello.
- Dopo oltre 24 ore dall'ultimo messaggio di Fred usa soltanto un modello approvato per avvisarlo:
  un'anteprima e un riferimento. `Leggi ID` consente di ricevere dettagli e allegati riaprendo la finestra.
- Se la finestra del destinatario originale è chiusa, non invia liberamente il testo approvato da Fred.
  Conserva la richiesta e segnala che manca un modello adatto o un nuovo messaggio del contatto.
- Trascrive i vocali diretti di Fred, conservando l'originale. Non gestisce chiamate telefoniche o WhatsApp live.
- Produce risposte, sintesi e bozze; archivia note e allegati nel progetto esplicitamente indicato.
  Non pubblica sui social, non manda email, non modifica calendari e non esegue codice suggerito dai messaggi.

## Come usarla

- Testo o vocale: «Per Off Class: appunto sulle organizzazioni…».
- Allegato con didascalia: «Per Italia Compete: conserva questo articolo».
- Materiale inoltrato senza destinazione: Irina chiede il progetto, mostrando un ID.
  Risposta: `ID Off Class`, `ID Italia Compete` oppure `ID Personale`.
- I comandi contenuti in materiale inoltrato o inviato da altri non sono ordini di Fred.
- Più progetti senza una scelta chiara: richiesta di chiarimento, nessuna assegnazione casuale.

## Registro condiviso con le chat

Il servizio **non inserisce messaggi nelle conversazioni ChatGPT** e non eredita le loro memorie,
plugin, autorizzazioni o materiali. Il punto di scambio è Dropbox:

| Progetto | Namespace | Cartella |
| --- | --- | --- |
| Italia Compete | 2166447024 | `/Projects/Italia Compete/Gestione editoriale/Appunti Irina` |
| Off Class | 13529493 | `/Off Class/01 fonti originali/Appunti Irina` |
| Personale | 13529493 | `/Irina/Appunti personali` |
| Corrispondenza | 13529493 | `/Irina/Risposte contatti` |

Ogni appunto ha cartella `AAAAMMGG/ID`, originale, trascrizione se presente, `note.json` e `note.md`.
Le chat possono recuperare questi materiali con il connettore Dropbox. Non è stata modificata la
generazione editoriale automatica per consumare tutti gli appunti senza revisione.
La conferma di salvataggio arriva solo dopo verifica dell'hash su Dropbox.

## Limiti espliciti

- Allegati: massimo 20 MiB; vocali massimo 30 minuti. PDF/immagini/testo analizzabili automaticamente fino a 8 MiB.
- DOCX/PPTX/XLSX/video si conservano come originali; non viene dichiarata un'analisi del contenuto non eseguita.
- URL nei messaggi sono conservati come testo, non visitati dal servizio.
- Memoria per la generazione: ultimi 12 appunti del progetto; non ricerca semantica di tutto l'archivio.
- Limite ricezione: 100 messaggi da Fred e 100 da contatti nelle ultime 24 ore, con tentativi tecnici limitati.
- Gli allegati dei contatti non sono trascritti automaticamente: vengono conservati e inoltrati a Fred.
- La finestra API usa un margine di 5 minuti prima delle 24 ore.
- L'accettazione API non equivale alla consegna o lettura. Le ricevute firmate vengono memorizzate separatamente.
- Invii incerti non si ripetono automaticamente, inclusi gli inoltri e le risposte ai contatti.
- Le copie di lavoro dei media vengono eliminate localmente dopo un giorno solo se già verificate in Dropbox.
- Nessun endpoint pubblico espone il registro: soltanto `/healthz` e `/webhook`.

## Configurazione e attivazione

Blueprint separato: `render-irina.yaml`, Dockerfile: `Dockerfile.irina`.
Prima di creare il servizio va approvato il costo aggiuntivo: Starter circa $7/mese + disco 1 GB circa $0,25/mese,
oltre a eventuali consumi API, traffico e imposte. Verificare il preventivo Render all'attivazione.

Variabili:

| Chiave | Provenienza / significato |
| --- | --- |
| `IRINA_OWNER_PHONES` | Due numeri forniti da Fred, internazionali senza `+`, separati da virgola |
| `IRINA_PRIMARY_PHONE` | Numero italiano di Fred, presente nell'elenco precedente |
| `IRINA_DATA_DIR` | `/var/data/irina`, disco persistente dedicato |
| `META_APP_SECRET` | App Secret dell'app Meta Assistente di Fred, da inserire in modo protetto |
| `IRINA_VERIFY_TOKEN` | Segreto casuale generato dal Blueprint, uguale nella verifica webhook Meta |
| `IRINA_WHATSAPP_TOKEN` | Token dell'app Irina con accesso al WABA esistente |
| `OPENAI_API_KEY` | Credenziale OpenAI API del progetto di Fred, da inserire in modo protetto; non la password ChatGPT |
| `IRINA_OPENAI_MODEL` | `gpt-4.1-mini`, configurabile |
| `IRINA_DROPBOX_REFRESH_TOKEN` | Accesso all'account Dropbox e ai namespace indicati; riutilizzare l'integrazione autorizzata tramite percorso protetto |
| `DROPBOX_APP_KEY` | App Dropbox associata al refresh token |

Non stampare segreti, copiarli in chat o committarli. Nessuna credenziale Telegram è necessaria.
Modello per gli avvisi proattivi: `irina_messaggio_ricevuto_v1`, italiano; testo esatto in `RELAY_BODY`.
Deve essere approvato da Meta prima dell'uso fuori dalla finestra conversazionale.

Procedura dopo disponibilità delle credenziali e approvazione del costo:
1. Creare servizio web e disco dal Blueprint separato; controllare che il worker esistente resti invariato.
2. Configurare i segreti e i due numeri; verificare build, `/healthz` e `irina_inbox_ready`.
3. Nell'app Meta impostare il callback HTTPS effettivo del servizio, percorso `/webhook`, e verify token.
4. Sottoscrivere il campo `messages` e la stessa app al WABA esistente. Non rimuovere altre sottoscrizioni.
5. Collaudare con Fred: testo da entrambi i numeri, vocale, PDF con progetto, allegato senza progetto,
   recupero via Dropbox. Verificare ricezione reale, salvataggio e risposta.
6. Collaudare inoltro da un contatto e risposta approvata da Fred, solo con un contatto e testo autorizzati.
7. Non dichiarare operativo prima delle prove reali. Non cambiare la routine Papà per simulare un test.

## Verifiche locali

`python -m unittest discover -s tests -q`

Test senza chiamate esterne: firme webhook, isolamento WABA/numero, due identità Fred,
contatti privi di potere di comando, progetto ambiguo, vocali, anti-duplicati,
incertezza invii, finestra 24 ore, mancato archivio, URL media, inoltro e risposte esatte.
Le prove mock non attestano credenziali valide, template approvati o recapito reale.

Fonti tecniche:
- https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/
- https://developers.openai.com/api/docs/guides/speech-to-text
- https://developers.openai.com/api/docs/guides/file-inputs
- https://render.com/docs/web-services
- https://render.com/pricing
