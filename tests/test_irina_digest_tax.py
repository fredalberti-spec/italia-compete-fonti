import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from irina_inbox import Inbox
from irina_digest_costs import CostBlocked,POLICY_KEY,Budget
from irina_digest_tax import parse_public,verified_context,verify,renew,RENEWAL_KEY,EVIDENCE_KEY

META="Your 'Sold To' address is in the EU but outside of Ireland: This applies to Italy. For business purposes Meta doesn't add VAT and you must self-assess at the applicable local rate. Your 'Sold To' address is in Ireland:"
EU='<tr><td>IT</td><td>Italy</td><td>22</td><td>5 / 10</td><td>4</td><td>-</td></tr>'
RATES={'MARKETING':'0.0658','UTILITY':'0.0248','SERVICE':'0.0248'}


class Tax(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.inbox=Inbox(self.tmp.name);self.wa=SimpleNamespace(owners={'390000000001'})
        encoded=json.dumps({'billing_country':'Italy','currency':'EUR','business_purpose':True,
                            'vat_id_present':True,'supplier':'Meta direct; no BSP added'},sort_keys=True)
        self.policy={'currency':'EUR','market':'Italy','gross_multiplier':'1.22','taxes_verified':True,
                     'tax_evidence_sha256':hashlib.sha256(encoded.encode()).hexdigest(),
                     'valid_until':1000000,'meta_rates_eur':RATES,'template_category':'MARKETING'}
        with self.inbox.db() as db:
            db.execute('INSERT INTO settings VALUES (?,?)',(EVIDENCE_KEY,encoded))
            db.execute('INSERT INTO settings VALUES (?,?)',(POLICY_KEY,json.dumps(self.policy)))
        self.public=parse_public(META,EU)
        self.baseline=dict(self.public,transport_sha256='a'*64,tax_evidence_sha256=self.policy['tax_evidence_sha256'])
        self.check=Mock(return_value=self.baseline)

    def enable(self):
        with self.inbox.db() as db:
            db.execute('INSERT INTO settings VALUES (?,?)',(RENEWAL_KEY,json.dumps({'baseline':self.baseline})))

    def test_public_rules_must_be_unambiguous_and_italian_22_percent(self):
        self.assertEqual(self.public['standard_vat_percent'],'22')
        for meta,eu in [(META.replace('self-assess','changed'),EU),(META,EU.replace('22','23')),(META,'')]:
            with self.assertRaises(CostBlocked):parse_public(meta,eu)

    def test_private_context_hash_owner_currency_and_transport_are_bound(self):
        kwargs={'lookup':lambda:self.public,'account':lambda wa:'EUR','transport':lambda:'a'*64}
        self.assertEqual(verified_context(self.inbox,self.policy,self.wa,'390000000001',**kwargs),self.baseline)
        with self.assertRaisesRegex(CostBlocked,'MetaBillingCurrencyChanged'):
            verified_context(self.inbox,self.policy,self.wa,'390000000001',**dict(kwargs,account=lambda wa:'USD'))
        with self.assertRaises(CostBlocked):verified_context(self.inbox,self.policy,self.wa,'390000000002',**kwargs)
        with self.assertRaisesRegex(CostBlocked,'TaxBillingContextChanged'):
            verified_context(self.inbox,dict(self.policy,gross_multiplier='1.00'),self.wa,'390000000001',**kwargs)

    def test_renewal_only_after_fresh_verification_and_preserves_cost_fields(self):
        self.enable()
        self.assertIsNone(renew(self.inbox,self.wa,'390000000001',1000,self.check))
        self.check.assert_not_called()
        result=renew(self.inbox,self.wa,'390000000001',900000,self.check)
        self.assertEqual(result['state'],'tax_evidence_renewed');self.check.assert_called_once()
        with self.inbox.db() as db:
            policy=json.loads(db.execute('SELECT value FROM settings WHERE key=?',(POLICY_KEY,)).fetchone()['value'])
        self.assertEqual(policy,dict(self.policy,valid_until=900000+7*86400))
        self.assertIsNone(renew(self.inbox,self.wa,'390000000001',900001,self.check))

    def test_changed_evidence_blocks_both_renewal_and_verification(self):
        self.enable();self.check.return_value=dict(self.baseline,transport_sha256='b'*64)
        with self.assertRaisesRegex(CostBlocked,'PublicTaxEvidenceOrTransportChanged'):
            renew(self.inbox,self.wa,'390000000001',900000,self.check)
        with self.assertRaises(CostBlocked):verify(self.inbox,self.policy,self.wa,'390000000001',self.check)
        with self.inbox.db() as db:
            policy=json.loads(db.execute('SELECT value FROM settings WHERE key=?',(POLICY_KEY,)).fetchone()['value'])
        self.assertEqual(policy,self.policy)

    def test_expired_policy_cannot_be_revived(self):
        self.enable()
        with self.assertRaisesRegex(CostBlocked,'GrossCostPolicyExpiredOrInvalid'):
            renew(self.inbox,self.wa,'390000000001',1000000,self.check)
        self.check.assert_not_called()

    def test_budget_refuses_send_without_owner_context_when_guard_enabled(self):
        self.enable();rates=Mock(return_value=RATES)
        with self.assertRaisesRegex(CostBlocked,'TaxRecipientOrOwnerChanged'):
            Budget(self.inbox,lambda:900000,rates).quote('MARKETING','390000000001')
        rates.assert_not_called()

if __name__=='__main__':unittest.main()
