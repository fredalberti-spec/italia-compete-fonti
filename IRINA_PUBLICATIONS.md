# Italia Compete: WhatsApp post pubblicazione

Autorizzazione Fred 28/09/2026. Il servizio Irina esistente legge ogni 60 secondi
`ns:2166447024//Projects/Italia Compete/Gestione editoriale/Notifiche Irina/Coda`.
Il destinatario è soltanto IRINA_PRIMARY_PHONE, verificato nell'allowlist proprietario.
Non usa Telegram, browser o l'intelligenza generativa per riscrivere l'avviso.

Una richiesta JSON per rubrica e nome base, SOLO dopo verifica effettiva PUBLISHED:

```json
{"schema":1,"brand_id":7005805,"rubrica":"Quotidiani","date":"2026-09-28",
"basename":"20260928","title":"I progetti hanno bisogno di persone",
"png_path":"/Projects/Italia Compete/post_IC_new/Quotidiani/20260928.png",
"png_sha256":"SHA256 del PNG verificato, 64 caratteri esadecimali",
"posts":[{"network":"instagram","status":"PUBLISHED","id":"ID Metricool",
"uuid":"UUID Metricool","url":"https://www.instagram.com/p/SHORTCODE/"},
{"network":"linkedin","status":"PUBLISHED","id":"ID Metricool","uuid":"UUID Metricool",
"url":"https://linkedin.com/feed/update/urn:li:share:ID"}]}
```

Le quattro rubriche ammesse sono Quotidiani, In agenda, Nel mondo e Il punto.
Non conferisce alcuna autorizzazione alla pubblicazione di nuove rubriche.
Per caroselli si allega la copertina PNG finale. Immagine <=5 MiB e SHA256 obbligatorio.
I link devono essere permalink pubblicati. Il nome file della richiesta può essere
`AAAAMMGG-rubrica.json`; deduplica stabile su rubrica+basename, indipendente dal nome file.
La prima richiesta registrata è immutabile: eventuali integrazioni canale vanno riconciliate,
non inviate di nuovo automaticamente. Preferire richiesta con entrambi i canali risolti.

SQLite registra prima l'intenzione d'invio. Invii accettati, rifiutati o incerti non si ripetono.
Gli esiti vengono scritti in `Notifiche Irina/Esiti/KEY.json`, includendo ID messaggio e
ricevute webhook consegnato/letto se disponibili. `accepted` non significa consegnato.
Un crash durante l'invio diventa uncertain e richiede riconciliazione.

Entro la finestra conversazionale WhatsApp: immagine con didascalia, titolo e link.
Fuori finestra: SOLO modello italiano approvato `irina_italia_compete_pubblicato_v1`,
header IMAGE, body esatto `Italia Compete — {{1}}. Pubblicato: {{2}}. {{3}}. Irina`.
Il codice NON crea modelli né presume approvazione Meta. Senza modello approvato:
`blocked / ImageTemplateNotApproved`; il job resta in coda e viene ritentato solo dopo
preflight valido (riapertura finestra da Fred o approvazione del modello). Nessun POST
WhatsApp avviene prima del preflight. Non aggirare la finestra Meta.

Verifica locale: `python -m unittest discover -s tests -p 'test_irina*.py' -q`.
