"""Renew only unchanged public tax evidence for the verified direct Meta channel."""
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from irina_digest_costs import CostBlocked, POLICY_KEY

EVIDENCE_KEY='digest_tax_evidence_v1'
RENEWAL_KEY='digest_tax_renewal_v1'
ATTEMPT_KEY='digest_tax_refresh_attempt_v1'
META_TAX='https://www.facebook.com/business/help/459201562169682'
EU_TAX='https://europa.eu/youreurope/business/finance-and-tax/vat/vat-rules-rates/index_en.htm'


def text(value):
    value=value.replace(r'\u003C','<').replace(r'\u0026','&').replace(r'\/','/')
    return ' '.join(html.unescape(re.sub('<[^>]*>',' ',value)).split())


def parse_public(meta, eu):
    sections=set(re.findall(r"Your 'Sold To' address is in the EU but outside of Ireland:(.*?)Your 'Sold To' address is in Ireland:",text(meta),re.S))
    if len(sections)!=1:raise CostBlocked('MetaTaxRuleUnavailableOrAmbiguous')
    section=sections.pop()
    if not (100<len(section)<4000 and 'Italy' in section and 'self-assess' in section
            and "Meta doesn't add VAT" in section and 'business purposes' in section):
        raise CostBlocked('MetaTaxRuleChanged')
    rows=set()
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',eu,re.S|re.I):
        cells=[text(c) for c in re.findall(r'<td\b[^>]*>(.*?)</td>',row,re.S|re.I)]
        if len(cells)>=3 and cells[:2]==['IT','Italy']:rows.add(tuple(cells))
    if len(rows)!=1:raise CostBlocked('ItalyVATRateUnavailableOrAmbiguous')
    row=rows.pop()
    if row[2]!='22':raise CostBlocked('ItalyVATRateChanged')
    return {'meta_rule_sha256':hashlib.sha256(section.encode()).hexdigest(),
            'italy_vat_row_sha256':hashlib.sha256(json.dumps(row).encode()).hexdigest(),
            'standard_vat_percent':'22'}


def public_evidence():
    values=[]
    for url,hosts in [(META_TAX,{'www.facebook.com','facebook.com'}),(EU_TAX,{'europa.eu'})]:
        with urllib.request.urlopen(url,timeout=25) as response:
            parsed=urllib.parse.urlparse(response.url)
            if parsed.scheme!='https' or parsed.hostname not in hosts:raise CostBlocked('InvalidTaxSource')
            data=response.read(2_000_001)
        if len(data)>2_000_000:raise CostBlocked('TaxSourceTooLarge')
        values.append(data.decode('utf-8'))
    return parse_public(*values)


def transport_hash():
    # A code change requires a fresh explicit operator review, never auto-attestation.
    root=Path(__file__).parent
    names=['irina_digest_tax.py','irina_digest_costs.py','irina_digest.py',
           'irina_digest_admin.py','irina_processor.py','irina_inbox.py']
    return hashlib.sha256(b''.join(name.encode()+b'\0'+(root/name).read_bytes() for name in names)).hexdigest()


def currency(wa):
    from irina_inbox import WABA_ID
    from irina_processor import json_request
    return json_request('https://graph.facebook.com/v23.0/'+WABA_ID+'?fields=currency',wa.token).get('currency')


def verified_context(inbox, policy, wa, recipient, lookup=public_evidence, account=currency, transport=transport_hash):
    if wa is None or recipient not in wa.owners or not recipient.startswith('39'):
        raise CostBlocked('TaxRecipientOrOwnerChanged')
    with inbox.db() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(EVIDENCE_KEY,)).fetchone()
    if not row:raise CostBlocked('TaxEvidenceMissing')
    evidence=json.loads(row['value'])
    if (hashlib.sha256(row['value'].encode()).hexdigest()!=policy.get('tax_evidence_sha256')
            or evidence.get('billing_country')!='Italy' or evidence.get('currency')!='EUR'
            or evidence.get('business_purpose') is not True or evidence.get('vat_id_present') is not True
            or evidence.get('supplier')!='Meta direct; no BSP added'
            or policy.get('currency')!='EUR' or policy.get('market')!='Italy'
            or policy.get('gross_multiplier')!='1.22' or policy.get('taxes_verified') is not True):
        raise CostBlocked('TaxBillingContextChanged')
    if account(wa)!='EUR':raise CostBlocked('MetaBillingCurrencyChanged')
    return dict(lookup(),transport_sha256=transport(),tax_evidence_sha256=policy['tax_evidence_sha256'])


def configure(inbox, wa, recipient, now=time.time):
    with inbox.db() as db:
        policy=json.loads(db.execute('SELECT value FROM settings WHERE key=?',(POLICY_KEY,)).fetchone()['value'])
    if now()>=float(policy['valid_until']):raise CostBlocked('GrossCostPolicyExpiredOrInvalid')
    baseline=verified_context(inbox,policy,wa,recipient)
    with inbox.db() as db:
        existing=db.execute('SELECT value FROM settings WHERE key=?',(RENEWAL_KEY,)).fetchone()
        if existing and json.loads(existing['value'])['baseline']!=baseline:
            raise CostBlocked('TaxRenewalBaselineConflict')
        db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(RENEWAL_KEY,json.dumps({'baseline':baseline})))
    return {'tax_renewal_enabled':True,'gross_multiplier':'1.22','annual_gross_limit_eur':5}


def verify(inbox, policy, wa, recipient, check=verified_context):
    with inbox.db() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(RENEWAL_KEY,)).fetchone()
    if not row:return False
    if check(inbox,policy,wa,recipient)!=json.loads(row['value'])['baseline']:
        raise CostBlocked('PublicTaxEvidenceOrTransportChanged')
    return True


def renew(inbox, wa, recipient, now, check=verified_context):
    with inbox.db() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(RENEWAL_KEY,)).fetchone()
        policy_row=db.execute('SELECT value FROM settings WHERE key=?',(POLICY_KEY,)).fetchone()
    if not row:return None
    policy=json.loads(policy_row['value']);expiry=float(policy['valid_until'])
    if now<expiry-2*86400:return None
    if now>=expiry:raise CostBlocked('GrossCostPolicyExpiredOrInvalid')
    with inbox.db() as db:
        db.execute('BEGIN IMMEDIATE')
        attempt=db.execute('SELECT value FROM settings WHERE key=?',(ATTEMPT_KEY,)).fetchone()
        if attempt and now-float(attempt['value'])<3600:return None
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(ATTEMPT_KEY,str(now)))
    verify(inbox,policy,wa,recipient,check)
    renewed=dict(policy,valid_until=now+7*86400)
    with inbox.db() as db:
        # Do not overwrite a concurrent operator policy edit.
        changed=db.execute('UPDATE settings SET value=? WHERE key=? AND value=?',
            (json.dumps(renewed),POLICY_KEY,policy_row['value'])).rowcount
        if not changed:raise CostBlocked('ConcurrentCostPolicyChange')
    return {'state':'tax_evidence_renewed','valid_until':renewed['valid_until']}
