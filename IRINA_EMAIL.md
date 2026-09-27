# Irina email intake

The existing Irina web service polls Resend's receiving API every 60 seconds in a
separate thread. It does not send email, start Telegram, or create another service.

## Configuration on the Irina service only

- `IRINA_EMAIL_ENABLED=true`
- `IRINA_RESEND_API_KEY`: secret Resend key with Full access (required for receiving).
- `IRINA_EMAIL_DOMAIN`: the account's assigned receiving domain.
- `IRINA_OWNER_EMAILS`: comma-separated owner sender addresses.

Supported recipients: `irina@DOMAIN`, `italiacompete@DOMAIN`, `offclass@DOMAIN`.
The last two choose the project. Otherwise the subject chooses Italia Compete or
Off Class, and unlabelled messages go to Personale. Only allowlisted senders with
server-verified DMARC and SPF/DKIM passing are treated as owner material. Other
mail goes to correspondence without AI processing or replies to the sender.

Mail and quoted material never execute assistant commands. No links are followed.
Plain body, source HTML (not rendered), metadata and supported attachments are
saved and verified in Dropbox. Supported attachment types match WhatsApp intake;
PDF/images/text up to 8 MB are analysed. Other supported files are archived only.
Maximum 10 files, 20 MB per file, 40 MB aggregate; skipped files are named in the
result. Download URLs must be signed Resend inbound CDN URLs, without redirects
or API credentials. Duplicate email IDs cannot generate another normal notice;
ambiguous WhatsApp POST outcomes remain uncertain without automatic resending.

Owner-only WhatsApp notices wait for a real inbound WhatsApp message to open the
24-hour window; receipt of an email does not open or refresh that window. No
unapproved marketing template is used. This version does not support email replies.

## Verification

`python -m unittest discover -s tests -p 'test_irina*.py'`

Logs: `irina_email_ready` reports configuration presence only (not successful API
authentication); `irina_email_poll_ok` confirms a completed scan;
`irina_email_archived`, `irina_email_notice_accepted` (not proof of delivery),
`irina_email_poll_error`, `irina_email_processing_error`.

To test after setting the secret, forward a non-sensitive email from an allowlisted
address with subject `Off Class: prova email`. Check the source, attachment hashes
and project folder, then the WhatsApp result after an owner message to Irina.
