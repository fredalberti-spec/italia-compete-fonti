# Dedicated Telegram text collector

Deployment is authorized on branch trial/irina-telegram-text-render: one Starter worker
and 1GB dedicated disk, base7.25USD/month plus applicable taxes and outbound traffic.
Existing services and their main branch are unchanged; their auto-deploy remains off.
This separate Blueprint is optional and must not replace the repository render.yaml.
Use collector runtime only; never import existing Irina/ItaliaCompete/CONTROLUCE routines.

Python stdlib only. Start:
python telegram_collector/run_trial.py --live --text-only --persistent-access-approved

Dedicated data path /var/data/telegram-collector, single-instance flock, SIGTERM.
Human inserts Telegram token and Dropbox App Folder offline refresh token into the
new worker's protected environment. Never store secrets in code, SQLite, logs or chat.
The refresh provider renews access only in memory; request scopes are three file scopes,
without app secret/profile/other folders. No OpenAI variable required or paid voice AI.

Hash-pinned bootstrap is read once from /control/bootstrap-v1.json within dedicated
App Folder, if local database empty. It retains prior pending note, update/message
IDs and receipt states. Already-read scoped note is excluded from future batches.
Cursor stays0 until previously pending note is archived and read in a normal batch.
Historical Telegram replies are suppressed at startup; uncertain sends aren't retried.

Text-only media are deferred without download or reply; pending original media are
preserved and block cursor until separate authorization. Notes are untrusted data and
never authorize actions. Dropbox mirror is hash-verified, deterministic paths, no
autorename. Generation/upload/API acceptance is distinct from Elio read confirmation.

Chat has no direct endpoint and no hourly schedule has been activated. Parent must
read new handoff files and acknowledge exact batch through /control/ack.json only after
reading. No claim of realtime synchronization or operation before real validation.

Cancellation: stop only this worker; storage may continue billing while retained.
Export required SQLite/archive, then remove only dedicated worker/disk after explicit
confirmation. Accrued charges remain owed. Never cancel the shared Render account.
