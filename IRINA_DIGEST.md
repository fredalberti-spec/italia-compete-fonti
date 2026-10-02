# Digest settimanale Fred — contratto di consegna v1

Canale distinto da Italia Compete, namespace Dropbox `2166447024`.
Destinatario esclusivo: `IRINA_PRIMARY_PHONE` presente nell'allowlist owner esistente.
Nessuna lettura Mail, produzione generativa, nuovo servizio o ampliamento OAuth.
L'invio è **disabilitato per default**. Solo dopo verifica Meta/costi/end-to-end,
attivazione esplicita con `python irina_digest_admin.py activate` sul servizio
esistente dopo il gate di prova e costo. Lasciare `IRINA_DIGEST_ENABLED` disattivato.

## Deposito del produttore

Cartella base: `/Projects/Digest Fred`.
Radice verificata sia dal servizio sia dal connettore: namespace `2166447024`,
montato nel connettore sotto `/Strategique`. Le quattro cartelle dedicate sono
state create con il grant già configurato sul servizio il 02/10/2026:
`/Projects/Digest Fred`, `PDF`, `Coda`, `Esiti`.

Per il produttore via connettore usare
`ns:2166447024//Projects/Digest Fred` (oppure il path_display restituito:
`/Strategique/Projects/Digest Fred`). Per l'SDK del servizio, dopo with_path_root
sul namespace, usare `/Projects/Digest Fred`. Nel manifest pdf_path usare sempre
la forma SDK `/Projects/Digest Fred/PDF/AAAA-MM-GG.pdf`, senza `/Strategique`.
Il produttore può quindi scrivere nello stesso namespace letto dal servizio,
senza altri accessi. Se gli scope non bastano, fermarsi senza ampliarli. Depositare prima il PDF completo,
poi il manifest JSON (<=32 KiB) nella Coda, ultimo passo atomico del produttore.
Non inserire mock-up in Coda. Il servizio non invia contenuti costruiti per i test.

Esempio di schema, **non una richiesta pronta all'invio**:

```json
{
  "schema": 1,
  "edition": "2026-10-03",
  "edition_number": 1,
  "pdf_path": "/Projects/Digest Fred/PDF/2026-10-03.pdf",
  "pdf_sha256": "SHA256 effettivo del file definitivo: 64 cifre esadecimali minuscole",
  "editorial_verified": true,
  "sources": [{"url": "https://URL-DELLA-FONTE", "title": "Titolo verificato", "published_date": "2026-10-02"}]
}
```

Nome manifest consigliato: `Coda/2026-10-03.json`. `edition` è la data locale del
sabato di rilascio e la chiave immutabile di deduplicazione; `edition_number` è un
intero progressivo positivo stampato dal produttore in alto a destra insieme alla
data. Cambiare nome del file o numero progressivo non autorizza un secondo invio.
La prima registrazione è immutabile. Manifest modificati producono conflitto, mai
una sostituzione automatica; riconciliazione manuale per errori e invii incerti.

PDF <=10 MiB, non cifrato, esattamente due pagine A4 verticali, MediaBox e CropBox
coincidenti, rotazione zero, UserUnit 1. Hash verificato sui byte scaricati.
Il produttore verifica visualmente entrambe le pagine e ogni fonte, con cinque
cose da sapere sviluppate e cinque brevi, titoli tematici immediati, notizie economiche italiane,
globali e imprese, sintesi interpretativa, La lente della ricerca, La connessione
che conta e Domanda per l’aula. Nessun contenuto inventato. L'attestazione e la
lista fonti sono obbligatorie: il servizio non può provarne la veridicità né la
corrispondenza semantica col PDF. Tale responsabilità resta al produttore.

## Tempo, esiti e fallimenti

Poll ogni 60 secondi nel servizio esistente. Sabato dalle 08:00 Europe/Rome,
ZoneInfo gestisce CET/CEST. Un riavvio recupera solo entro quel sabato; oltre la
mezzanotte locale l'edizione diventa `expired`. Nessuna garanzia al secondo esatto,
specialmente se il servizio o Dropbox sono indisponibili.

`Esiti/AAAA-MM-GG.json` contiene manifest, stato, errori sanitizzati, message_id e
ricevute webhook. `queued` prima del rilascio, `blocked` per attivazione/template/
validazione, `accepted` significa accettato da Meta; solo ricevuta `delivered`
prova consegna, `read` prova lettura. `rejected`, `uncertain`, `expired` non si
ritentano automaticamente. Il lock di processo e SQLite persistente proteggono
l'intenzione di invio. Crash dopo intenzione => `uncertain`; riconciliare prima
di qualsiasi retry. I report Dropbox falliti sono ritentati dal database.
Manifest malformati/conflitti producono log sanitizzati, non una ricevuta di
edizione non identificabile. Il PDF non viene caricato a Meta prima dei preflight.

## Meta e costi: prerequisiti aperti

Entro la finestra owner valida viene inviato un DOCUMENT ordinario. Fuori finestra
è richiesto un template distinto `irina_digest_settimanale_v1`, lingua `it`, stato
`APPROVED`, header `DOCUMENT`, body esattamente:

> CONTROLUCE, il digest economico settimanale richiesto, è pronto: edizione {{1}}.
> In allegato il PDF di due pagine con notizie e sintesi interpretativa.
> Irina

Il worker esegue solo lookup e verifica. Il comando operatore dedicato può
richiedere il template, esclusivamente a seguito di autorizzazione esplicita.
La classificazione e il prezzo effettivi devono essere verificati in Meta per
l'account e il destinatario prima del test a pagamento. Fred ha autorizzato creazione template, test sul primary e attivazione dopo prova
riuscita, con tetto complessivo **5 EUR annui, imposte e prova comprese**.
L'autorizzazione non sostituisce la verifica di prezzi, imposte, approvazione Meta
e ricevuta `delivered`. Nessun test WhatsApp reale è stato eseguito.

Test locale: `python -m unittest discover -s tests -p 'test_irina*.py' -q`.
Le fixture sono PDF vuoti sintetici, usati solo con API simulate.

## Verifiche del 02/10/2026 e costo documentato

Lookup di sola lettura dalla shell del servizio con il token Meta configurato:
`message_templates?name=irina_digest_settimanale_v1` restituisce `data:[]`;
`exact_approved:false`. Il template dedicato non esiste e non è stato creato.
Lookup account `fields=currency`: `EUR`; controllo del prefisso del primary:
mercato `Italy`, senza registrare o mostrare il numero.

Fonte primaria: [Prezzi Meta, aggiornati il 30/09/2026](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing#rate-cards-and-volume-tiers),
tariffario EUR collegato dalla pagina, effective October 1, 2026, riga Italy:
Utility **EUR 0.0248**, Marketing **EUR 0.0658**, Service **EUR 0.0248** per
messaggio consegnato. Categoria del nuovo template ancora da attribuire a Meta:
non presumere Utility per una newsletter editoriale. Per 52 consegne annue il
costo di listino è EUR 1.2896 Utility oppure EUR 3.4216 Marketing, prima delle
imposte ed eventuali sconti di volume. Per 4–5 edizioni mensili:
EUR 0.0992–0.1240 Utility oppure EUR 0.2632–0.3290 Marketing.

Dal 01/10/2026 i messaggi Service possono essere fatturati dopo i primi 1.000
mensili per numero business; i template Utility sono fatturabili anche nella
finestra aperta. Nessuna gratuità del test è presunta. L'eventuale esenzione
FEP, il consumo della quota Service e la categoria finale non sono verificati
per un invio futuro. Per un singolo test predisporre consenso a un costo massimo
di listino EUR 0.0658 più imposte, dopo conferma della categoria approvata.
Nessun nuovo costo Render: uso del servizio e del disco esistenti, nessun cambio
piano o numero istanze. Nessuna chiamata OpenAI è necessaria per la consegna.

## Comandi operatore e blocco di costo

Dalla shell del servizio esistente: `python irina_digest_admin.py template`,
poi `rates`, `test`, `status`, `activate`, in questo ordine dopo le verifiche.
Non viene creato alcun endpoint pubblico né accesso aggiuntivo. `template` usa un
esempio DOCUMENT esplicitamente marcato PROVA e categoria richiesta MARKETING;
Meta decide quella finale. Un marker persistente impedisce reinvii ciechi della
richiesta. `test` usa esclusivamente il mock-up v6 sotto `Test`, SHA-256
`b8ac2688e02b7b58f75dcc42a7cb3dc40753b62c2f8f02d4ed2e5e9a8e0fc01e`;
non crea manifest editoriali o edizioni. Il body e il nome allegato dichiarano
PROVA MOCK-UP NON VERIFICATO. La prova ha chiave persistente e un solo tentativo.
`activate` richiede template approvato esatto, costo verificato e ricevuta
`delivered` della prova; solo allora abilita il setting persistente dedicato.

`digest_costs` conserva prenotazioni conservative in micro-EUR lordi per anno
civile Europe/Rome, incluse prove e invii incerti/rifiutati. Un'unica transazione
SQLite prenota prima del POST messaggio. Nessuna prenotazione viene liberata
automaticamente: si preferisce bloccare prima che superare il limite. Prezzo,
categoria o verifica fiscale non disponibili => nessun POST messaggio.

Il setting privato `digest_verified_cost_policy_v1` richiede valuta EUR, mercato
Italy, categoria effettiva del template, prezzi Meta per MARKETING/UTILITY/SERVICE,
`taxes_verified:true`, SHA-256 dell'evidenza fiscale, moltiplicatore lordo verificato
e scadenza `valid_until` Unix. È assente per default. Nessuna aliquota è presunta.
Non occorre una conferma settimanale di Fred; il controllo prezzi è automatico
prima di ogni tentativo e blocca quando cambia il listino o scade l'evidenza.

**Blocco verificato 02/10/2026:** la risposta HTTP pubblica Meta è Markdown e
la tabella EUR contiene etichette prive del collegamento al tariffario, mentre
l'interfaccia browser presenta il link. Il lookup automatico restituisce
`UnresolvedCurrentEURRateCard` e resta chiuso. La policy fiscale non è configurata.
Prima di attivare serve una sorgente corrente Meta risolvibile automaticamente
(verificata anche nel runtime Render), evidenza fiscale applicabile, creazione
ed approvazione template e prova con ricevuta. Il trasporto della WebShell Mac
è attualmente indisponibile; non si aggira con nuove credenziali.

## Deposito del produttore

Consegnare PDF e manifest entro **sabato 07:55 Europe/Rome** per lasciare cinque
minuti ai controlli, preferibilmente venerdì. Prima caricare il PDF completo,
poi il JSON in Coda: il manifest è il segnale di disponibilità. Il servizio
rilascia dal sabato 08:00 con polling di 60 secondi; non garantisce il secondo
esatto. A mezzanotte locale l'edizione non inviata scade senza spedizione tardiva.
Il genitore produce i contenuti verificati: il servizio controlla struttura,
hash e attestazione, non certifica la veridicità delle notizie. Per l'edizione
reale non usare il mock-up né un manifest `editorial_verified:true` per prove.

Il nome approvato è **CONTROLUCE — Economia, imprese e scenari globali.
La settimana letta in prospettiva.** Prima del test usare la versione più recente
approvata dal produttore. Per una nuova versione, tutti i comandi template/test/
status/activate accettano la stessa coppia esplicita `--test-file` (solo PDF in
`/Projects/Digest Fred/Test/`) e `--test-sha256`. La chiave della prova è il suo
hash; non ripetere la prova se una precedente versione è già accettata senza
un'esplicita necessità autorizzata. Il default v6 è un artefatto di prova soltanto.
