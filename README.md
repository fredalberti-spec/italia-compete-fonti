# Italia Compete e OFF CLASS — Fonti Telegram

Un solo worker Render riusa la sessione Telegram e il registro SQLite già presenti
sul disco persistente `/var/data`. Inoltra a Papà soltanto i quotidiani autorizzati;
non genera né pubblica post.

## Routine Papà — API Telegram (27 settembre 2026)

`papa.py` usa il client Telegram già autenticato dal worker, sullo stesso disco
persistente, senza browser, nuovi login o copie della sessione. È abilitata per
default (`PAPA_ENABLED=false` la disabilita). Dal 27/09/2026 controlla la
disponibilità ogni 5 minuti dalle 08:00 alle 09:30 Europe/Rome e inoltra solo
negli appuntamenti delle **08:30 e 09:30**, con scansione aggiornata prima di
ogni invio. Alle 09:30 invia solo ciò che manca: il risultato parziale delle
08:30 non chiude più la giornata. Entrambi gli appuntamenti sono persistiti
in SQLite e condividono il registro anti-duplicati; i risultati della versione
precedente restano validi per il primo appuntamento, incluso quello di oggi.

Il ciclo si attiva ogni 10 secondi. Un riavvio durante il minuto previsto può
recuperare l'appuntamento; fuori da quei due minuti non parte alcun invio di
recupero. Errori e tentativi incerti restano registrati e non vengono reinviati.
I controlli intermedi sono di sola lettura e non scaricano documenti.

Unico destinatario: utente privato Papà, ID 8836718451; unica fonte: Part 2,
ID -1001295597629. Inoltra esclusivamente Corriere della Sera nazionale e
Il Giorno Legnano/Legnano–Varese della data corrente, verificata nel nome PDF.
Preferisce il Corriere definitivo disponibile. Ogni chiamata contiene un solo
messaggio e un solo destinatario; non espande album e non scarica file.

Prima di ciascun invio controlla la cronologia del destinatario. SQLite conserva
ogni tentativo prima dell'invio, un random_id e l'ID del messaggio verificato.
Un esito incerto viene cercato in cronologia ma non reinviato automaticamente.
La protezione Telegram all'inoltro viene rispettata. Nessun avviso WhatsApp è
incluso: l'integrazione WhatsApp Business resta da configurare separatamente.

Log: `papa_api_ready` certifica accesso a fonte e identità destinatario;
`papa_forward_verified` certifica la presenza del singolo documento;
`papa_availability` registra i controlli intermedi;
`papa_daily_result` riporta presenti/mancanti e lo slot, con `final=true` alle 09:30; `papa_result_uncertain` richiede
verifica. Un deploy riuscito, da solo, non certifica l'invio dei quotidiani.
Test: `python -m unittest discover -s tests -p 'test_papa.py' -v`.

## Fonti e destinazioni

- Italia Compete: Part 2 (-1001295597629) ed Economaniacs (-1001302683686),
  PDF fino a 200 MiB. Restano la finestra 08:00–12:00 Europe/Rome, i controlli
  ogni 15 minuti e la destinazione `DROPBOX_BASE` esistenti.
- OFF CLASS: risoluzione del titolo `Harvard business review` senza differenze
  di maiuscole; se ci sono omonimi il collegamento si ferma. Il primo ID univoco
  viene fissato nel registro e riusato anche se la chat cambia nome.
- Archivio OFF CLASS: `/Off Class/01 fonti originali/Telegram Harvard Business Review`
  nel namespace Dropbox configurato separatamente per OFF CLASS.
  `Schede messaggio` contiene testo, URL (anche quelli dietro link cliccabili),
  data originale e riferimenti Telegram. `PDF` contiene gli allegati consentiti.

## Acquisizione OFF CLASS

Il controllo avviene ogni 15 minuti, anche a Mac spento. La data iniziale è
`OFFCLASS_START_FROM` con fuso esplicito; viene conservata nel registro. In seguito
si riparte dall'ultimo messaggio completato. Ogni ciclo lavora al massimo 100
messaggi per evitare di monopolizzare il worker. Non si recupera automaticamente
la cronologia anteriore alla data configurata.

Le schede sono `da verificare`: il corretto trasferimento non certifica il contenuto.
I link sono archiviati ma gli articoli collegati non vengono scaricati o analizzati.
La generazione di contenuti e la pubblicazione social restano disattivate.

Chat e messaggi protetti e media autodistruttivi non vengono copiati. Il normale
timer di cancellazione della cronologia della chat è distinto dai media
autodistruttivi. Nessuna restrizione di accesso o salvataggio viene aggirata.

## Integrità e ripresa

- Ogni upload viene verificato per dimensione e content hash Dropbox.
- I PDF HBR sono indirizzati per hash: repost dello stesso file non creano copie.
- Le schede conservano la provenienza di ogni messaggio; ritentare un upload
  interrotto riusa un percorso deterministico. Il cursore avanza solo al termine.
- In caso di errore il processo registra il tipo, senza segreti o testi privati,
  e ritenta. I tempi FloodWait di Telegram vengono rispettati.
- Un errore HBR non impedisce il lavoro sulle fonti Italia Compete.
- Le modifiche a messaggi già completati non vengono riacquisite automaticamente.

`status.json` è l'ultimo evento tecnico; il registro SQLite conserva la storia.
Un evento `offclass_source_checked` conferma lettura Telegram e accesso all'archivio;
`acquired=0` indica nessun nuovo messaggio acquisito in quel ciclo.
Il file `collegamento-<chat_id>.json` su Dropbox documenta fonte e impostazioni.

## Configurazione e distribuzione

Riutilizzare esclusivamente il servizio `italia-compete-fonti` e il suo disco;
non creare un secondo client che apra contemporaneamente la stessa sessione.
Lasciare intatti i segreti Telegram e Dropbox già autorizzati.

Variabili aggiunte:

- `OFFCLASS_ENABLED=true` abilita l'acquisizione HBR.
- `OFFCLASS_CHAT_TITLE=Harvard business review`.
- `OFFCLASS_CHAT_ID` facoltativo: ID verificato, altrimenti binding persistente.
- `OFFCLASS_START_FROM=2026-09-26T00:00:00+02:00`.
- `OFFCLASS_DROPBOX_BASE` e `OFFCLASS_DROPBOX_NAMESPACE_ID` indicano l'archivio.

`INGESTION_ENABLED` rimane l'interruttore generale. I segreti e le sessioni non
vanno inseriti nel repository, nella chat o nelle cartelle editoriali.
Le cartelle Dropbox devono esistere prima del deploy. Pubblicare i file nel
repository, impostare le sole nuove variabili sul servizio esistente e distribuire.
Verificare poi i log e la presenza dei file su Dropbox. I test locali da soli
non dimostrano che il collegamento sia attivo.

Per sospendere solo HBR impostare `OFFCLASS_ENABLED=false` e ridistribuire.
L'archivio acquisito resta disponibile. Le autorizzazioni revocate richiedono
intervento umano; il collegamento persistente non è una garanzia contro revoche.

## Verifica locale

`python -m unittest discover -s tests -v`

`python -m unittest test_offclass -v`

I test coprono hash, upload ambiguo, persistenza, protezione delle fonti, link,
PDF ripubblicati, ripresa dopo errore e riconoscimento univoco della chat.
