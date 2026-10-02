# Digest settimanale Fred — contratto di consegna v1

Canale distinto da Italia Compete, namespace Dropbox `2166447024`.
Destinatario esclusivo: `IRINA_PRIMARY_PHONE` presente nell'allowlist owner esistente.
Nessuna lettura Mail, produzione generativa, nuovo servizio o ampliamento OAuth.
L'invio è **disabilitato per default**. Solo dopo verifica Meta/costi/end-to-end,
attivazione esplicita con `IRINA_DIGEST_ENABLED=true` sul servizio esistente.

## Deposito del produttore

Cartella base: `/Projects/Personale/Digest Fred`.
Creare `PDF`, `Coda`, `Esiti` usando gli accessi esistenti; se gli scope non bastano,
fermarsi senza richiederne altri automaticamente. Depositare prima il PDF completo,
poi il manifest JSON (<=32 KiB) nella Coda, ultimo passo atomico del produttore.
Non inserire mock-up in Coda. Il servizio non invia contenuti costruiti per i test.

Esempio di schema, **non una richiesta pronta all'invio**:

```json
{
  "schema": 1,
  "edition": "2026-10-03",
  "edition_number": 1,
  "pdf_path": "/Projects/Personale/Digest Fred/PDF/2026-10-03.pdf",
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
cose da sapere sviluppate, titoli tematici immediati, notizie economiche italiane,
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

> Il digest economico settimanale richiesto è pronto: edizione {{1}}.
> In allegato il PDF di due pagine con notizie e sintesi interpretativa.
> Irina

Il codice esegue solo lookup e verifica, mai creazione o modifica di template.
La classificazione e il prezzo effettivi devono essere verificati in Meta per
l'account e il destinatario prima del test a pagamento. Nessun test WhatsApp
reale o nuovo costo è autorizzato da questo contratto. Servono verifica degli
accessi Dropbox correnti, approvazione esatta del DOCUMENT italiano, costo noto
e autorizzazione al test, PDF reale verificato, ricevuta delivered, poi attivazione.

Test locale: `python -m unittest discover -s tests -p 'test_irina*.py' -q`.
Le fixture sono PDF vuoti sintetici, usati solo con API simulate.
