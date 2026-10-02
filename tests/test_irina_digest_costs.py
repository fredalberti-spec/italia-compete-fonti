import json
import hashlib
import tempfile
import unittest
from unittest.mock import Mock
from irina_inbox import Inbox
from irina_digest_costs import Budget,CostBlocked,POLICY_KEY,parse_rates,RateLinks,rate_url,renew_rates,rate_snapshot,RATE_SNAPSHOT_KEY

RATES={'MARKETING':'0.0658','UTILITY':'0.0248','SERVICE':'0.0248'}

class Costs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.inbox=Inbox(self.tmp.name);self.rates=Mock(return_value=RATES)
        self.b=Budget(self.inbox,lambda:1790982000,self.rates)
        # An illustrative tax multiplier only, never used on the live service.
        self.policy={'currency':'EUR','market':'Italy','taxes_verified':True,
            'tax_evidence_sha256':'0'*64,'gross_multiplier':'1.22','valid_until':1890982000,
            'meta_rates_eur':RATES,'template_category':'MARKETING'}

    def save(self):
        with self.inbox.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(POLICY_KEY,json.dumps(self.policy)))

    def test_unverified_gross_blocks_without_lookup(self):
        with self.assertRaisesRegex(CostBlocked,'GrossCostPolicyNotVerified'):self.b.quote('MARKETING','390000000001')
        self.rates.assert_not_called()

    def test_rates_category_and_expiry_block(self):
        self.save();self.assertEqual(self.b.quote('MARKETING','390000000001'),(90000,'MARKETING'))
        with self.assertRaisesRegex(CostBlocked,'TemplateCategoryChanged'):self.b.quote('UTILITY','390000000001')
        self.rates.return_value=dict(RATES,MARKETING='0.07')
        with self.assertRaisesRegex(CostBlocked,'MetaRatesChanged'):self.b.quote('MARKETING','390000000001')
        self.policy['valid_until']=0;self.save()
        with self.assertRaises(CostBlocked):self.b.quote('MARKETING','390000000001')

    def test_annual_budget_includes_test_and_uncertain_reservations(self):
        with self.inbox.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.b.reserve(db,'test:fixture',90000,'MARKETING')
            self.b.reserve(db,'test:fixture',90000,'MARKETING')
            self.b.reserve(db,'edition:fixture',4910000,'MARKETING')
            with self.assertRaisesRegex(CostBlocked,'AnnualGrossBudgetExceeded'):self.b.reserve(db,'edition:over',1,'MARKETING')
        with self.inbox.db() as db:self.assertEqual(db.execute('SELECT SUM(gross_micros) FROM digest_costs').fetchone()[0],5000000)

    def test_failed_rate_read_blocks(self):
        self.save();self.rates.side_effect=TimeoutError
        with self.assertRaisesRegex(CostBlocked,'CostVerificationUnavailable'):self.b.quote('MARKETING','390000000001')

    def test_csv_and_official_link_parser(self):
        self.assertEqual(parse_rates(b'Market,Currency,Marketing,Utility,Authentication,International,Service\nItaly,EUR,0.0658,0.0248,0.0248,n/a,0.0248\n'),RATES)
        links=RateLinks();links.feed('<a href="https://scontent.example.fbcdn.net/rates.csv?a=1">Rates in EUR</a><a href="https://evil.example/rates.csv">Rates in EUR</a>')
        self.assertEqual(links.links,['https://scontent.example.fbcdn.net/rates.csv?a=1'])
        with self.assertRaises(CostBlocked):parse_rates(b'Italy,USD,0.1,0.1,0.1,n/a,0.1')

    def test_verified_snapshot_expires_and_blocks_source_change(self):
        source=b'Official pricing markdown with EUR labels'
        snap={'verified_at':'2026-10-02T00:00:00Z','valid_until':'2026-10-09T00:00:00Z',
            'document_sha256':hashlib.sha256(source).hexdigest(),
            'card_sha256':'a'*64,'url':'https://scontent.example.fbcdn.net/rates.csv'}
        self.assertEqual(rate_url(source,snap,1790899200), (snap['url'],'a'*64))
        with self.assertRaisesRegex(CostBlocked,'VerifiedRateSnapshotExpired'):
            rate_url(source,snap,1791504000)
        with self.assertRaisesRegex(CostBlocked,'MetaPricingDocumentChanged'):
            rate_url(source+b'changed',snap,1790899200)
        snap['valid_until']='2026-10-10T00:00:00Z'
        with self.assertRaises(CostBlocked):rate_url(source,snap,1790899200)

    def test_dynamic_official_link_takes_priority(self):
        source=b'<a href="https://scontent.example.fbcdn.net/new.csv">Rates in EUR</a>'
        self.assertEqual(rate_url(source,{},0),('https://scontent.example.fbcdn.net/new.csv',None))

    def test_renewal_before_expiry_preserves_tax_expiry_and_throttles(self):
        self.save()
        snap=rate_snapshot(self.inbox)
        from datetime import datetime
        expiry=datetime.fromisoformat(snap['valid_until'].replace('Z','+00:00')).timestamp()
        lookup=Mock(return_value=RATES)
        self.assertIsNone(renew_rates(self.inbox,expiry-3*86400,lookup))
        lookup.assert_not_called()
        result=renew_rates(self.inbox,expiry-86400,lookup)
        self.assertEqual(result['state'],'rates_renewed')
        self.assertEqual(lookup.call_args.kwargs['snapshot'],snap)
        self.assertIsNone(renew_rates(self.inbox,expiry-86400+10,lookup))
        with self.inbox.db() as db:
            policy=json.loads(db.execute('SELECT value FROM settings WHERE key=?',(POLICY_KEY,)).fetchone()['value'])
        self.assertEqual(policy['valid_until'],self.policy['valid_until'])

    def test_renewal_expired_or_changed_rates_never_extends(self):
        self.save()
        snap=rate_snapshot(self.inbox)
        from datetime import datetime
        expiry=datetime.fromisoformat(snap['valid_until'].replace('Z','+00:00')).timestamp()
        lookup=Mock(return_value=dict(RATES,MARKETING='0.07'))
        with self.assertRaisesRegex(CostBlocked,'VerifiedRateSnapshotExpired'):
            renew_rates(self.inbox,expiry,lookup)
        lookup.assert_not_called()
        with self.assertRaisesRegex(CostBlocked,'MetaRatesChanged'):
            renew_rates(self.inbox,expiry-86400,lookup)
        with self.inbox.db() as db:
            self.assertIsNone(db.execute('SELECT value FROM settings WHERE key=?',(RATE_SNAPSHOT_KEY,)).fetchone())

if __name__=='__main__':unittest.main()
