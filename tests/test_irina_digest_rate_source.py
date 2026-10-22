from tests.offline_guard import install
install()

import json
import unittest
from unittest.mock import Mock
from irina_digest_rate_source import PAGE,API,configuration,current_rates,SourceError


def page(**changes):
    c={'restUrl':API,'marketValueToName':{'IT':'Italy'},'currencyValueToLabel':{'EUR':'Euro (EUR)'},
       'restNonce':'public-fixture','wpNonce':'public-fixture'}
    c.update(changes)
    return ('<script type="application/json">'+json.dumps({'state':{'wab/pricing-calculator':c}})+'</script>'
            '<div data-wp-context=\''+json.dumps({'allowedCategories':['Marketing','Utility','Service']})+'\'></div>').encode()


class Rates(unittest.TestCase):
    def test_current_quotes_bind_market_currency_categories_and_base_tier(self):
        reader=Mock(side_effect=[page(),b'{"quote":"0.0658","tier_list":[]}',
            b'{"quote":"0.0248","tier_list":[{"min_volume":0,"quote":"0.0248"}]}',
            b'{"quote":"0.0248","tier_list":[]}'])
        self.assertEqual(current_rates(reader),{'MARKETING':'0.0658','UTILITY':'0.0248','SERVICE':'0.0248'})
        for call in reader.call_args_list[1:]:
            self.assertTrue(call.args[0].startswith(API+'?'))
            self.assertIn('market=IT',call.args[0]);self.assertIn('currency=EUR',call.args[0])
            self.assertEqual(call.kwargs['headers']['X-WP-Nonce'],'public-fixture')

    def test_changed_or_ambiguous_page_and_unsafe_host_block(self):
        for data in [page(restUrl='https://example.org/steal'),page(marketValueToName={'IT':'Other'}),
                     page()+page(),page().replace(b'Service',b'Other'),b'login page']:
            with self.assertRaises(SourceError):configuration(data)

    def test_invalid_tiers_nonfinite_and_free_quotes_fail_closed(self):
        for quote in [b'{"quote":"NaN","tier_list":[]}',b'{"quote":"0","tier_list":[]}',
                      b'{"quote":"0.02","tier_list":[{"min_volume":0,"quote":"0.01"}]}',b'{}']:
            with self.assertRaises(SourceError):current_rates(Mock(side_effect=[page(),quote]))

    def test_public_page_without_nonces_uses_anonymous_read_only_endpoint(self):
        raw=page();raw=raw.replace(b', "restNonce": "public-fixture"',b'').replace(b', "wpNonce": "public-fixture"',b'')
        reader=Mock(side_effect=[raw]+[b'{"quote":"0.0248","tier_list":[]}']*3)
        current_rates(reader)
        for call in reader.call_args_list[1:]:
            self.assertNotIn('_wab_nonce',call.args[0]);self.assertEqual(call.kwargs['headers'],{})

if __name__=='__main__':unittest.main()
