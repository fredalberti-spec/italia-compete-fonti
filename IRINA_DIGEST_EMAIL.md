# CONTROLUCE — percorso email predisposto

Il percorso email usa il servizio e la chiave Resend già esistenti. È disattivato
per default e non crea credenziali, servizi, domini o piani. Non legge Mail.
L'edizione 001 resta esclusa anche dopo attivazione; il coordinatore ne gestisce
l'invio immediato. Prima edizione ammissibile: 10/10/2026.

## Configurazione privata e prerequisiti

`IRINA_DIGEST_EMAIL_FROM`: singolo mittente verificato, indirizzo oppure `Nome Cognome <indirizzo>`.
Il dominio e la deduplicazione usano il solo indirizzo; il nome viene formattato
nel campo From senza cambiare la firma del testo.
`IRINA_DIGEST_EMAIL_TO`: unico destinatario concordato, configurato privatamente.
`IRINA_DIGEST_EMAIL_PREFLIGHT=true`: abilita solo verifica provider e report.
`IRINA_DIGEST_EMAIL_ENABLED=true`: attiva consegna, solo dopo verifica completa.
`IRINA_RESEND_API_KEY`: chiave già presente, non copiarla in file o chat.

La sola creazione di una casella Aruba non verifica il dominio presso Resend.
La verifica richiede dominio con status verified e capability sending enabled,
quota giornaliera numerica entro 100 e mensile entro 3000, entrambe disponibili.
Quota sconosciuta, piani a pagamento, limiti nulli o esauriti bloccano l'invio.
Eventuali DNS, accessi o credenziali nuovi richiedono conferma separata.
Il modulo non configura SMTP e non usa password comunicate in chat.

## Consegna e prova

Stesso PDF definitivo validato con hash, due A4 e fonti del manifest originale.
Sabato 08:00 Europe/Rome, ora legale automatica, recupero solo quel sabato.
Nessun CC/BCC. Intento persistente prima del POST e idempotency key; la
deduplicazione SQLite rimane permanente oltre le 24 ore di idempotenza Resend.
Accepted, rejected e uncertain non vengono ritentati automaticamente.
Le ricevute riguardano esclusivamente gli ID dei propri invii; delivered prova
la consegna al provider destinatario, non la lettura personale.

Report `Esiti/Email-stato.json` e `Esiti/email-AAAA-MM-GG.json`, indipendenti
dagli esiti WhatsApp. Un test locale non prova la consegna reale: l'attivazione
richiede mittente autenticato, destinatario corretto e quota gratuita verificata;
la consegna attiva richiede poi un esito delivered. Non inviare mock-up come
edizione reale. Nessun invio email è stato effettuato durante l'implementazione.

Fonti provider: https://resend.com/docs/api-reference/usage/retrieve-usage ,
https://resend.com/docs/api-reference/domains/list-domains ,
https://resend.com/docs/api-reference/emails/send-email .
