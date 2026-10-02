# CONTROLUCE — contratto definitivo di deposito e rilascio

Il servizio esistente consegna; il coordinatore produce e verifica il PDF.
Produttore coordinato separatamente: sabato 06:30 Europe/Rome, fonti e bundle
grafico approvati, prima edizione 03/10/2026 numero 1. Il servizio di consegna
non genera contenuti. Rubrica: **La domanda strategica della settimana**.

## Deposito

Namespace Dropbox `2166447024`; connettore `/Strategique/Projects/Digest Fred`,
SDK con path-root `/Projects/Digest Fred`. Depositare entro sabato 07:55 Europe/Rome.
Caricare PDF definitivo, poi manifest nella Coda come segnale di disponibilità.

Manifest esatto (esempio di schema, non edizione pronta):

```json
{
  "schema": 1,
  "edition": "2026-10-03",
  "edition_number": 1,
  "pdf_path": "/Projects/Digest Fred/PDF/2026-10-03.pdf",
  "pdf_sha256": "HASH_SHA256_REALE_64_CIFRE_MINUSCOLE",
  "editorial_verified": true,
  "sources": [
    {"url": "https://FONTE_VERIFICATA", "title": "Titolo verificato", "published_date": "2026-10-02"}
  ]
}
```

Salvare `Coda/2026-10-03.json`, massimo 32 KiB. Non aggiungere campi tariffari:
il produttore non può impostare prezzi, destinatari o autorizzazioni nel manifest.
`edition` deve essere sabato; `edition_number` intero positivo; fonti HTTPS non vuote
con data non successiva all'edizione. Il PDF deve corrispondere all'hash, essere
non cifrato, massimo 10 MiB, esattamente due A4 verticali, CropBox=MediaBox,
rotazione zero, UserUnit=1. Il produttore verifica fonti, contenuti e rendering.
Mock-up esclusivamente in `Test`, mai Coda e mai attestazione editoriale vera.

## Rilascio e ricevute

Solo primary già configurato e owner allowlist. Sabato 08:00 Europe/Rome con
ora legale automatica, polling 60 secondi. Recupero solo nel medesimo sabato;
a mezzanotte locale scadenza senza invio tardivo. Nessuna garanzia al secondo.
Deduplicazione immutabile per data: cambiare nome/numero/hash non crea un secondo invio.
Conflitti richiedono riconciliazione. Accepted, rejected e uncertain mai reinviati
automaticamente. Intento persistente prima del POST; crash durante invio => uncertain.

`Esiti/AAAA-MM-GG.json`: manifest, stato, errore sanitizzato, message_id,
ricevute `{status, stamp}` e `cost_reservation` con `{year, gross_micros, category,
reserved_at}`. `accepted` non prova consegna; richiedere `delivered`.
Esiti non scritti su Dropbox ritentati dal database persistente.

## Gate Meta e prova

Template distinto `irina_digest_settimanale_v1`, lingua `it`, header DOCUMENT,
body CONTROLUCE esatto verificato dal codice, categoria effettiva verificata.
Non usare template IMAGE Italia Compete. Template attualmente PENDING.
Richiesta persistente esplicita `prepare-activation` già registrata per mock-up v7:
`/Projects/Digest Fred/Test/controluce-mockup-v7.pdf`, SHA-256
`54cccfe9097166aa55a0cd943170ee02e9bdaf68cc8640f8479991416337d1d4`.
Il loop attende approvazione; una sola prova marcata PROVA/MOCK-UP; attivazione
solo dopo ricevuta delivered e costo nuovamente verificato. Nessuna edizione
reale disponibile finché il produttore non deposita PDF e manifest verificati.

Comandi operatore sul servizio esistente: `template`, `rates`, `test`, `status`,
`activate`, `prepare-activation`, `tax-renewal` via `python irina_digest_admin.py`.
Usare per tutti stessa coppia `--test-file` e `--test-sha256` v7 sopra.

## Campi tariffari privati e rinnovo

Setting privato `digest_verified_cost_policy_v1`:
`currency`, `market`, `taxes_verified`, `tax_evidence_sha256`, `gross_multiplier`,
`valid_until` Unix, `meta_rates_eur` (MARKETING, UTILITY, SERVICE), `template_category`.
Nessun identificativo fiscale, carta, telefono o credenziale nel repository.
Quote in micro-EUR lordi arrotondate per eccesso al centesimo; prenotazione
atomica prima del POST; prove/rifiuti/incerti compresi, nessun rimborso automatico.
Limite 5 EUR per anno civile Europe/Rome. Il report registra la prenotazione,
non una fattura Meta o prova del costo effettivamente addebitato.

Fonte listino: documento pricing ufficiale Meta e card EUR ufficiale collegata.
Lookup dinamico prioritario. Fallback verificato con hash documento+card e
scadenza massima sette giorni. Rinnovo nel loop esistente due giorni prima
scadenza, tentativi al massimo orari: riscarica fonte e card, verifica hash e
prezzi contro policy; salva nuovo snapshot sette giorni in setting dedicato
`digest_verified_rate_snapshot_v1`. Nessuna proroga su cambi, errori o evidenza
già scaduta. Cambi link/listino richiedono nuova verifica della fonte corrente.
Il comando esplicito `tax-renewal` abilita un secondo gate: rilegge il testo
fiscale pubblico Meta applicabile all'Italia e la riga IVA italiana della fonte
UE (22%), verifica valuta WABA EUR tramite API esistente, evidenza privata del
contesto di fatturazione già verificato e hash del codice del trasporto diretto.
Il moltiplicatore prudenziale resta 1.22, nessuna detrazione presunta. Il codice
non usa intermediari per la consegna; una modifica del trasporto richiede nuova
verifica operatore. Le schermate fiscali private dell'account non vengono
ricontrollate automaticamente: modifiche volontarie a paese/fornitore/commissioni
di fatturazione vanno segnalate e riconciliate prima di inviare.

Prima di ogni preventivo rilegge fonti pubbliche, valuta e trasporto e confronta
i fingerprint. Due giorni prima della scadenza, al massimo ogni ora, prolunga
la validità fiscale di sette giorni solo dopo verifica fresca e invariata.
Non modifica aliquota, prezzi, categoria o limite; non riattiva evidenza scaduta.
Cambi di testo fiscale/riga IVA, errori, valuta o trasporto diversi bloccano.
`Esiti/Verifica-costi.json` espone scadenze, rinnovo abilitato ed eventuale blocco,
senza dati identificativi. Il produttore può consultarlo prima del deposito.
Non serve una nuova conferma settimanale del destinatario; servono evidenze valide.
