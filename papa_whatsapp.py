"""One daily Papa notice from Irina, using approved WhatsApp templates only.

A durable reservation precedes POST. Accepted is not delivered/read; ambiguous
requests are never retried automatically. No Telegram client or browser here.
"""
import asyncio
import json
import hashlib
import os
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

PHONE_ID = '1320348587831246'
WABA_ID = '1753950079223989'
SENDER = '19295574726'
RECIPIENT_HASH = '32b36e8e9085cb0cab06d98d4d6a257aa510253eae1cdb5df015906ed3ed1081'
NAME = 'Irina Merovan | EA to Professor Alberti'
TITLES = {'corriere': 'il Corriere della Sera nazionale',
          'giorno_legnano': 'Il Giorno edizione Legnano–Varese'}
INTRO = 'papa_irina_benvenuto_v1'
TEMPLATES = {
    INTRO: "Ciao, sono Irina, l'assistente digitale di tuo figlio Fred. Ti ho girato {{1}} di oggi. Apri Telegram sul tuo iPad: trovi tutto nella chat con Fred. Buona lettura!",
    'papa_irina_quotidiani_a_v1': 'Ciao! Ti ho girato {{1}} di oggi. Apri Telegram sul tuo iPad: trovi tutto nella chat con Fred. Buona lettura! Irina',
    'papa_irina_quotidiani_b_v1': 'Buongiorno! Trovi {{1}} di oggi su Telegram, nella chat con Fred. Puoi aprire Telegram sul tuo iPad. Ti auguro una buona lettura! Irina',
    'papa_irina_quotidiani_c_v1': 'Ciao! Su Telegram ti aspettano le letture di oggi: {{1}}. Apri la chat con Fred sul tuo iPad. Buona lettura e buona giornata! Irina',
}


def enabled():
    return os.environ.get('PAPA_WHATSAPP_ENABLED', 'false').lower() == 'true'


def recipient():
    value = os.environ.get('PAPA_WHATSAPP_TO', '').strip().lstrip('+')
    if not value.isdigit() or hashlib.sha256(value.encode()).hexdigest() != RECIPIENT_HASH:
        raise ValueError('WhatsAppRecipientMismatch')
    return value


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS papa_whatsapp_notices ('
               'day TEXT PRIMARY KEY, slot TEXT, template TEXT, papers TEXT, '
               'state TEXT, message_id TEXT, created_at TEXT)')
    db.commit()


class GraphError(Exception):
    def __init__(self, status, code=None):
        self.status, self.code = status, code
        super().__init__('WhatsAppGraphError')


class Graph:
    def __init__(self, token):
        self.token = token

    def request(self, method, resource, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request('https://graph.facebook.com/v23.0/' + resource,
            data=data, method=method, headers={'Authorization': 'Bearer ' + self.token,
                                              'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                code = json.loads(exc.read()).get('error', {}).get('code')
            except (ValueError, AttributeError):
                code = None
            raise GraphError(exc.code, code) from None

    async def call(self, method, resource, payload=None):
        return await asyncio.to_thread(self.request, method, resource, payload)


def select_template(db, day, approved):
    # Uncertain first introduction must be reconciled manually before any later notice.
    uncertain = db.execute("SELECT 1 FROM papa_whatsapp_notices WHERE template=? "
                           "AND state IN ('pending','uncertain')", (INTRO,)).fetchone()
    if uncertain:
        return None
    introduced = db.execute("SELECT 1 FROM papa_whatsapp_notices WHERE template=? "
                            "AND state='accepted'", (INTRO,)).fetchone()
    if not introduced:
        return INTRO if INTRO in approved else None
    last = db.execute("SELECT template FROM papa_whatsapp_notices WHERE state='accepted' "
                      "ORDER BY day DESC LIMIT 1").fetchone()
    names = [n for n in TEMPLATES if n != INTRO]
    start = day.toordinal() % len(names)
    for i in range(len(names)):
        name = names[(start+i) % len(names)]
        if name in approved and (not last or name != last[0]):
            return name
    return None


async def notify(db, day, slot, present, report, graph=None):
    if not enabled():
        return 'disabled'
    setup(db)
    key = day.isoformat()
    if slot not in ('08:30', '09:30'):
        raise ValueError('WhatsAppInvalidSlot')
    papers = sorted(set(present))
    if not papers or any(p not in TITLES for p in papers):
        return 'no_verified_papers'
    previous = db.execute('SELECT state FROM papa_whatsapp_notices WHERE day=?', (key,)).fetchone()
    if previous:
        return previous[0]
    try:
        target = recipient()
    except ValueError:
        report('papa_whatsapp_blocked', day=key, slot=slot, reason='recipient_mismatch')
        return 'blocked'
    token = os.environ.get('IRINA_WHATSAPP_TOKEN', '')
    if graph is None:
        if not token:
            report('papa_whatsapp_blocked', day=key, slot=slot, reason='missing_api_token')
            return 'blocked'
        graph = Graph(token)
    try:
        identity = await graph.call('GET', PHONE_ID + '?fields=id,display_phone_number,verified_name,status')
        digits = ''.join(c for c in identity.get('display_phone_number', '') if c.isdigit())
        if (identity.get('id') != PHONE_ID or digits != SENDER or
                identity.get('verified_name') != NAME or identity.get('status') != 'CONNECTED'):
            report('papa_whatsapp_blocked', day=key, slot=slot, reason='sender_identity_or_status')
            return 'blocked'
        result = await graph.call('GET', WABA_ID + '/message_templates?fields=name,status,language,components&limit=100')
        approved = set()
        for template in result.get('data', []):
            name = template.get('name')
            bodies = [c.get('text') for c in template.get('components', []) if c.get('type') == 'BODY']
            if (name in TEMPLATES and template.get('status') == 'APPROVED' and
                    template.get('language') == 'it' and bodies == [TEMPLATES[name]]):
                approved.add(name)
        name = select_template(db, day, approved)
        if name is None:
            report('papa_whatsapp_blocked', day=key, slot=slot,
                   reason='template_not_approved_or_introduction_uncertain')
            return 'blocked'
    except Exception as exc:
        report('papa_whatsapp_blocked', day=key, slot=slot, reason='api_preflight',
               error_type=type(exc).__name__, code=getattr(exc, 'code', None))
        return 'blocked'
    titles = ' e '.join(TITLES[p] for p in papers)
    payload = {'messaging_product': 'whatsapp', 'recipient_type': 'individual',
               'to': target, 'type': 'template',
               'template': {'name': name, 'language': {'code': 'it'},
                            'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': titles}]}]}}
    with db:
        # Unique day constraint also protects concurrent invocations/restarts.
        inserted = db.execute('INSERT OR IGNORE INTO papa_whatsapp_notices VALUES (?,?,?,?,?,?,?)',
            (key, slot, name, json.dumps(papers), 'pending', None,
             datetime.now(ZoneInfo('Europe/Rome')).isoformat())).rowcount
    if not inserted:
        return 'already_reserved'
    try:
        response = await graph.call('POST', PHONE_ID + '/messages', payload)
        message_id = response.get('messages', [{}])[0].get('id')
        if not message_id:
            raise ValueError('MissingWhatsAppMessageId')
        state = 'accepted'
    except GraphError as exc:
        # Explicit 4xx rejection is known not accepted; never retry this day automatically.
        state = 'rejected' if 400 <= exc.status < 500 else 'uncertain'
        message_id = None
        report('papa_whatsapp_' + state, day=key, slot=slot, code=exc.code)
    except Exception as exc:
        state, message_id = 'uncertain', None
        report('papa_whatsapp_uncertain', day=key, slot=slot, error_type=type(exc).__name__)
    with db:
        db.execute('UPDATE papa_whatsapp_notices SET state=?,message_id=? WHERE day=?',
                   (state, message_id, key))
    if state == 'accepted':
        report('papa_whatsapp_accepted', day=key, slot=slot, papers=papers,
               template=name, introduction=(name == INTRO), message_id=message_id,
               delivery='not_verified', recipient='papa_verified')
    return state
