"""Fail-closed annual gross-cost budget, shared by digest editions and tests."""
import hashlib
import time
from pathlib import Path
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_CEILING
from html.parser import HTMLParser
from zipfile import ZipFile
from xml.etree import ElementTree as ET

LIMIT = 5_000_000  # EUR micro-units, taxes and test deliveries included
POLICY_KEY = 'digest_verified_cost_policy_v1'
RATE_SNAPSHOT_KEY = 'digest_verified_rate_snapshot_v1'
RATE_ATTEMPT_KEY = 'digest_rate_refresh_attempt_v1'
SOURCE = 'https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/'


class CostBlocked(ValueError):
    pass


class RateLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.link=None; self.label=''; self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.link=dict(attrs).get('href'); self.label=''
    def handle_data(self,data):
        if self.link: self.label+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.link:
            url=self.link
            if urllib.parse.urlparse(url).hostname == 'l.facebook.com':
                url=urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('u',[''])[0]
            host=urllib.parse.urlparse(url).hostname or ''
            if ('EUR' in self.label and 'volume' not in self.label.lower()
                    and host.endswith('.fbcdn.net') and urllib.parse.urlparse(url).path.endswith('.csv')):
                self.links.append(url)
            self.link=None


def parse_rates(data):
    # Meta's link labelled CSV currently serves an XLSX; support both deliberately.
    if data.startswith(b'PK'):
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with ZipFile(io.BytesIO(data)) as z:
            if any(i.file_size>5_000_000 for i in z.infolist()):raise CostBlocked('RateCardTooLarge')
            strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
            rows=[]
            for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//s:row',ns):
                values={}
                for c in row.findall('s:c',ns):
                    v=c.find('s:v',ns)
                    if v is not None:values[re.sub('[0-9]','',c.get('r'))]=strings[int(v.text)] if c.get('t')=='s' else v.text
                rows.append(values)
        match=next((r for r in rows if r.get('A')=='Italy' and r.get('B')=='EUR'),None)
        if not match:raise CostBlocked('ItalyEURRateMissing')
        result={'MARKETING':match.get('C'),'UTILITY':match.get('D'),'SERVICE':match.get('G')}
    else:
        import csv
        rows=list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        match=next((r for r in rows if len(r)>=7 and r[0]=='Italy' and r[1]=='EUR'),None)
        if not match:raise CostBlocked('ItalyEURRateMissing')
        result=dict(zip(('MARKETING','UTILITY','SERVICE'),(match[2],match[3],match[6])))
    rates={k:str(Decimal(v)) for k,v in result.items()}
    if any(not Decimal(v).is_finite() or not 0<Decimal(v)<1 for v in rates.values()):
        raise CostBlocked('InvalidMetaRates')
    return rates


def rate_url(data,snapshot,now):
    parser=RateLinks();parser.feed(data.decode('utf-8'))
    urls=set(parser.links)
    if len(urls)==1:return urls.pop(),None
    if urls:raise CostBlocked('AmbiguousCurrentEURRateCard')
    # Explicitly verified official browser card: bounded fallback, never a
    # permanent cached price. Re-fetch card and source before every attempt.
    verified=datetime.fromisoformat(snapshot['verified_at'].replace('Z','+00:00')).timestamp()
    expiry=datetime.fromisoformat(snapshot['valid_until'].replace('Z','+00:00')).timestamp()
    if not verified<=now<expiry or expiry-verified>7*86400:
        raise CostBlocked('VerifiedRateSnapshotExpired')
    if hashlib.sha256(data).hexdigest()!=snapshot['document_sha256']:
        raise CostBlocked('MetaPricingDocumentChanged')
    url=snapshot['url']
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.fbcdn.net'):
        raise CostBlocked('InvalidRateCardHost')
    return url,snapshot['card_sha256']


def live_rates(snapshot=None):
    # No credentials or billing changes. A failed read blocks delivery.
    with urllib.request.urlopen(SOURCE,timeout=20) as response:
        if urllib.parse.urlparse(response.url).hostname!='developers.facebook.com':raise CostBlocked('InvalidRateSource')
        data=response.read(2_000_001)
    if len(data)>2_000_000:raise CostBlocked('RateSourceTooLarge')
    snapshot=snapshot or json.loads(Path(__file__).with_name('irina_digest_rates.json').read_text())
    url,expected_hash=rate_url(data,snapshot,time.time())
    with urllib.request.urlopen(url,timeout=20) as response:
        if not (urllib.parse.urlparse(response.url).hostname or '').endswith('.fbcdn.net'):raise CostBlocked('InvalidRateCardHost')
        data=response.read(2_000_001)
    if len(data)>2_000_000:raise CostBlocked('RateCardTooLarge')
    if expected_hash and hashlib.sha256(data).hexdigest()!=expected_hash:
        raise CostBlocked('MetaRateCardChanged')
    return parse_rates(data)


def rate_snapshot(inbox):
    with inbox.db() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(RATE_SNAPSHOT_KEY,)).fetchone()
    return json.loads(row['value']) if row else json.loads(Path(__file__).with_name('irina_digest_rates.json').read_text())


def renew_rates(inbox, now, lookup=live_rates):
    """Renew an unchanged official card before expiry; never revive stale evidence."""
    snapshot=rate_snapshot(inbox)
    expiry=datetime.fromisoformat(snapshot['valid_until'].replace('Z','+00:00')).timestamp()
    if now<expiry-2*86400:return None
    if now>=expiry:raise CostBlocked('VerifiedRateSnapshotExpired')
    with inbox.db() as db:
        db.execute('BEGIN IMMEDIATE')
        attempt=db.execute('SELECT value FROM settings WHERE key=?',(RATE_ATTEMPT_KEY,)).fetchone()
        if attempt and now-float(attempt['value'])<3600:return None
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(RATE_ATTEMPT_KEY,str(now)))
        row=db.execute('SELECT value FROM settings WHERE key=?',(POLICY_KEY,)).fetchone()
    if not row:raise CostBlocked('GrossCostPolicyNotVerified')
    expected=json.loads(row['value'])['meta_rates_eur']
    current=lookup(snapshot=snapshot)
    if set(current)!=set(expected) or any(Decimal(current[k])!=Decimal(expected[k]) for k in current):
        raise CostBlocked('MetaRatesChanged')
    # The fallback reader verifies both document and card hashes on every read.
    # Dynamic discovery remains fresh on every quote and takes precedence.
    renewed=dict(snapshot,verified_at=datetime.fromtimestamp(now,timezone.utc).isoformat(),
                 valid_until=datetime.fromtimestamp(now+7*86400,timezone.utc).isoformat())
    with inbox.db() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(RATE_SNAPSHOT_KEY,json.dumps(renewed)))
    return {'state':'rates_renewed','valid_until':renewed['valid_until']}


class Budget:
    def __init__(self,inbox,now,rates=live_rates,wa=None):
        self.inbox,self.now,self.rates,self.wa=inbox,now,rates,wa

    def quote(self,category,recipient):
        with self.inbox.db() as db:
            row=db.execute('SELECT value FROM settings WHERE key=?',(POLICY_KEY,)).fetchone()
        if not row:raise CostBlocked('GrossCostPolicyNotVerified')
        try:
            policy=json.loads(row['value'])
            if (policy['currency']!='EUR' or policy['market']!='Italy' or not recipient.startswith('39')
                    or policy['taxes_verified'] is not True
                    or not re.fullmatch('[a-f0-9]{64}',policy['tax_evidence_sha256'])
                    or self.now()>=float(policy['valid_until'])):
                raise CostBlocked('GrossCostPolicyExpiredOrInvalid')
            multiplier=Decimal(policy['gross_multiplier'])
            if not multiplier.is_finite() or not 1<=multiplier<=3:raise CostBlocked('InvalidGrossMultiplier')
            from irina_digest_tax import verify
            verify(self.inbox,policy,self.wa,recipient)
            current=self.rates(snapshot=rate_snapshot(self.inbox)) if self.rates is live_rates else self.rates()
            expected=policy['meta_rates_eur']
            if set(current)!=set(expected) or any(Decimal(current[k])!=Decimal(expected[k]) for k in current):
                raise CostBlocked('MetaRatesChanged')
            if category not in current or (category!='SERVICE' and category!=policy['template_category']):
                raise CostBlocked('TemplateCategoryChanged')
            micros=int((Decimal(current[category])*multiplier).quantize(Decimal('0.01'),rounding=ROUND_CEILING)*1_000_000)
            if not 0<micros<=LIMIT:raise CostBlocked('InvalidGrossQuote')
            return micros,category
        except CostBlocked:raise
        except Exception:raise CostBlocked('CostVerificationUnavailable') from None

    def reserve(self,db,key,micros,category):
        # Caller holds BEGIN IMMEDIATE. Never release uncertain/rejected reservations.
        existing=db.execute('SELECT * FROM digest_costs WHERE key=?',(key,)).fetchone()
        if existing:
            if existing['gross_micros']<micros:raise CostBlocked('ReservedCostChanged')
            return
        year=datetime.fromtimestamp(self.now(),ZoneInfo('Europe/Rome')).year
        total=db.execute('SELECT COALESCE(SUM(gross_micros),0) FROM digest_costs WHERE year=?',(year,)).fetchone()[0]
        if total+micros>LIMIT:raise CostBlocked('AnnualGrossBudgetExceeded')
        db.execute('INSERT INTO digest_costs VALUES (?,?,?,?,?)',(key,year,micros,category,self.now()))
