import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from monitor import ServiceError
import watch_monitor as monitor
import watch_sources as sources

CHRONO = 'https://www.chrono24.se/hamilton/jazzmaster--id123456.htm'
URET = 'https://www.uret.se/hamilton/jazzmaster/h36215140/1080987'


def product():
    return {'@type': 'Product', 'name': 'Hamilton H36215140', 'brand': {'name': 'Hamilton'},
            'sku': 'H36215140', 'image': [{'contentUrl': 'https://img.chrono24.com/listing.jpg'}],
            'offers': {'@type': 'Offer', 'url': CHRONO, 'availability': 'https://schema.org/InStock',
                       'price': '11000', 'priceCurrency': 'SEK', 'itemCondition': 'https://schema.org/NewCondition',
                       'availableAtOrFrom': {'address': {'addressCountry': 'IT'}},
                       'shippingDetails': {'shippingDestination': {'addressCountry': 'SE'},
                                           'shippingRate': {'currency': 'SEK', 'value': 100}}}}


def schema_html(p=None, extra=''):
    return '<script type="application/ld+json">' + json.dumps({'@graph': [p or product()]}) + '</script>' + extra


def uret_html(condition='new', extra=''):
    p = {'id': '1080987', 'url': URET, 'model': 'H36215140', 'brand': 'Hamilton', 'type': 'watch',
         'active': 1, 'name': 'Jazzmaster', 'pricing': {'current_price': 10000, 'vat': {'rate': 25}},
         'tags': {'condition': {'value': condition}}, 'images': []}
    d = {'test': {'u': URET.replace('/hamilton', '/api/hamilton'), 's': 200, 'b': p}}
    return ('<html lang="en"><p>Sverige • Svenska</p><button>Lägg i varukorgen</button>'
            '<p>Beställningsvara, 1–3 veckor Fraktkostnad: 99 kr</p>'
            '<script id="ng-state" type="application/json">' + json.dumps(d) + '</script>' + extra)


def corso_html():
    meta = {'product': {'id': 99, 'vendor': 'Hamilton', 'type': 'Watch',
            'handle': sources.CORSO_PRODUCT.rsplit('/', 1)[-1],
            'variants': [{'sku': 'H36215140', 'name': 'Hamilton H36215140', 'price': 1000000}]}}
    return ('<script>Shopify.country = "SE"; Shopify.currency = {"active":"SEK","rate":"11.5"};'
            'var meta = ' + json.dumps(meta) + ';</script><p>Condition: New</p>'
            '<form id="AddToCartForm"><span class="price-min">10 000 kr</span><button name="add">Add to cart</button></form>'
            '<table><tr><td>Europe</td><td>Express</td><td>2 days</td><td>€19.95</td></tr>'
            '<tr><td>Economy</td><td>4 days</td><td>€9.95</td></tr>'
            '<tr><td>Italy</td><td>Free</td><td>4 days</td><td>€0.00</td></tr></table>')


class ParseTests(unittest.TestCase):
    def test_exact_product_and_shipping(self):
        row = sources.parse_schema('chrono24', schema_html(), CHRONO)
        self.assertEqual(row['total_sek'], 11100)
        self.assertEqual(row['destination'], 'SE')

    def test_wrong_reference(self):
        p = product(); p['sku'] = 'H36215640'
        with self.assertRaises(sources.Ineligible):
            sources.parse_schema('chrono24', schema_html(p), CHRONO)

    def test_shipping_destination_not_domain(self):
        for destination in ('CH', 'US', None):
            p = product(); p['offers']['shippingDetails']['shippingDestination']['addressCountry'] = destination
            with self.subTest(destination=destination), self.assertRaises(sources.Ineligible):
                sources.parse_schema('chrono24', schema_html(p), CHRONO)

    def test_import_origin_and_unknown(self):
        for country in ('US', 'CH', 'GB', ''):
            p = product(); p['offers']['availableAtOrFrom']['address']['addressCountry'] = country
            with self.subTest(country=country), self.assertRaises(sources.Ineligible):
                sources.parse_schema('chrono24', schema_html(p), CHRONO)

    def test_unknown_shipping_cost(self):
        p = product(); del p['offers']['shippingDetails']['shippingRate']['value']
        with self.assertRaises(sources.Ineligible):
            sources.parse_schema('chrono24', schema_html(p), CHRONO)

    def test_free_shipping_is_zero_not_missing(self):
        p = product(); p['offers']['shippingDetails']['shippingRate']['value'] = 0
        self.assertEqual(sources.parse_schema('chrono24', schema_html(p), CHRONO)['total_sek'], 11000)

    def test_different_currency(self):
        p = product(); p['offers']['priceCurrency'] = 'EUR'
        with self.assertRaises(sources.Ineligible):
            sources.parse_schema('chrono24', schema_html(p), CHRONO)

    def test_used(self):
        p = product(); p['offers']['itemCondition'] = 'https://schema.org/UsedCondition'
        self.assertEqual(sources.parse_schema('chrono24', schema_html(p), CHRONO)['condition'], 'used')

    def test_unavailable(self):
        p = product(); p['offers']['availability'] = 'https://schema.org/OutOfStock'
        with self.assertRaises(sources.Ineligible):
            sources.parse_schema('chrono24', schema_html(p), CHRONO)

    def test_visible_price_and_table(self):
        html = schema_html(extra='<div class="wt-listing-detail-page-price">11 001 SEK</div>'
                           '<table><tr><th>Tillgänglighet</th><td>Beställningsvara</td></tr></table>')
        row = sources.parse_schema('chrono24', html, CHRONO)
        self.assertEqual(row['price_sek'], 11001)
        self.assertEqual(row['availability'], 'Beställningsvara')

    def test_conflicting_price(self):
        with self.assertRaises(ValueError):
            sources.parse_schema('chrono24', schema_html(extra='<div class="wt-listing-detail-page-price">8 000 SEK</div>'), CHRONO)

    def test_main_product_not_recommendations(self):
        with self.assertRaises(ValueError):
            sources.parse_schema('chrono24', schema_html() + schema_html(), CHRONO)

    def test_redirect_product_identity(self):
        p = product(); p['offers']['url'] = CHRONO.replace('123456', '999999')
        with self.assertRaises(ValueError):
            sources.parse_schema('chrono24', schema_html(p), CHRONO)

    def test_uret_live_shape(self):
        row = sources.parse_uret(uret_html(), URET)
        self.assertEqual(row['total_sek'], 10099)
        self.assertIn('1–3', row['availability'])

    def test_uret_sold_and_missing_shipping(self):
        for html in (uret_html().replace('Lägg i varukorgen', 'Slutsåld'),
                     uret_html().replace('Fraktkostnad:', 'Unknown:'),
                     uret_html().replace('Sverige • Svenska', 'Danmark')):
            with self.assertRaises(sources.Ineligible):
                sources.parse_uret(html, URET)

    def test_uret_unknown_condition(self):
        with self.assertRaises(sources.Ineligible):
            sources.parse_uret(uret_html(condition='damaged'), URET)

    def test_price_safety(self):
        for value in ('NaN', 'Infinity', -1, True):
            with self.assertRaises(ValueError): sources.money(value)
        self.assertEqual(sources.sek('12\xa0345,50 SEK'), 12345.5)

    def test_search_challenge_is_failure(self):
        with self.assertRaises(ValueError): sources.listing_links('chrono24', '<title>Challenge</title>')

    def test_price_on_request_without_structured_product(self):
        with self.assertRaises(sources.Ineligible):
            sources.parse_schema('chrono24', '<title>Hamilton Pris vid förfrågan</title>', CHRONO)

    def test_links_no_external_hosts(self):
        with self.assertRaises(ValueError):
            sources.listing_links('chrono24', '<a href="https://evil.example/hamilton/x--id123.htm">Watch</a>')

    def test_partial_source_failure_independent(self):
        c = Mock()
        c.get.side_effect = [ServiceError('HTTP 403'), '<a href="' + CHRONO + '">watch</a>', schema_html()]
        rows, coverage, excluded = sources.collect({'sources': ['ebay', 'chrono24'], 'detail_limit': 25}, lambda: c)
        self.assertEqual(len(rows), 1)
        self.assertEqual([x['status'] for x in coverage], ['error', 'ok'])

    def test_corso_shipping_uses_europe_not_italy(self):
        row = sources.parse_corso(corso_html())
        self.assertEqual(row['shipping_sek'], 114.43)
        self.assertEqual(row['total_sek'], 10114.43)
        self.assertTrue(row['total_estimated'])

    def test_corso_requires_country_and_currency(self):
        for html in (corso_html().replace('"SE"', '"US"'), corso_html().replace('"SEK"', '"USD"')):
            with self.assertRaises(sources.Ineligible): sources.parse_corso(html)

    def test_corso_requires_live_shipping_tariff(self):
        with self.assertRaises(sources.Ineligible): sources.parse_corso(corso_html().replace('Europe', 'Unknown'))

    def test_corso_requires_orderable_correct_product(self):
        for html in (corso_html().replace('name="add"', 'name="add" disabled'),
                     corso_html().replace('H36215140', 'H36215640'),
                     corso_html().replace('Condition: New', 'Condition: Used')):
            with self.assertRaises(sources.Ineligible): sources.parse_corso(html)

    def test_corso_price_crosscheck(self):
        with self.assertRaises(ValueError): sources.parse_corso(corso_html().replace('10 000 kr', '9000 kr'))

    def test_corso_bad_fx_fails_closed(self):
        with self.assertRaises(ValueError): sources.parse_corso(corso_html().replace('"11.5"', '"NaN"'))

    def test_corso_estimate_visible_in_alert(self):
        row = sources.parse_corso(corso_html())
        cfg = monitor.config_from(Path(__file__).resolve().parents[1] / 'watch-config.json')
        text = monitor.message(row, cfg)
        self.assertIn('ca. ', text)
        self.assertIn('Checkout-Endpreis', text)


class MemoryStore:
    def __init__(self): self.state = {}; self.saves = 0
    def load(self): return copy.deepcopy(self.state)
    def save(self, state): self.state = copy.deepcopy(state); self.saves += 1


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.config = monitor.config_from(Path(__file__).resolve().parents[1] / 'watch-config.json')
        self.row = sources.parse_schema('chrono24', schema_html(), CHRONO)
        self.report = {'offers': [self.row], 'matches': [self.row], 'coverage': [
            {'source': 'chrono24', 'status': 'ok', 'checked': 1, 'found': 1, 'errors': []}]}
        self.store = MemoryStore()
        self.bot = Mock(recipient='recipient')
        self.bot.send.return_value = 123

    def run_notify(self, now=100000):
        return monitor.notify(self.report, self.config, self.store, self.bot, now)

    def test_dedup_and_price_new_low(self):
        self.assertEqual(self.run_notify(), 1)
        self.assertEqual(self.run_notify(), 0)
        self.row['total_sek'] = 11050
        self.assertEqual(self.run_notify(), 0)  # Suppress small exchange-rate fluctuations.
        self.row['total_sek'] = 10900
        self.assertEqual(self.run_notify(), 1)
        self.row['total_sek'] = 11000
        self.assertEqual(self.run_notify(), 0)
        self.row['total_sek'] = 10900
        self.assertEqual(self.run_notify(), 0)

    def test_condition_ceilings_include_shipping(self):
        self.row['total_sek'] = 11500
        self.assertTrue(monitor.qualifies(self.row, self.config))
        self.row['total_sek'] = 11501
        self.assertFalse(monitor.qualifies(self.row, self.config))
        self.row.update(condition='used', total_sek=9001)
        self.assertFalse(monitor.qualifies(self.row, self.config))
        self.row['total_sek'] = 9000
        self.assertTrue(monitor.qualifies(self.row, self.config))

    def test_recipient_change_fails_closed(self):
        self.run_notify()
        with self.assertRaises(ServiceError): monitor.prepare_state(self.store.state, 'other')

    def test_corrupt_history_fails(self):
        with self.assertRaises(ServiceError): monitor.prepare_state({'version': 99}, 'recipient')

    def test_failed_send_not_deduplicated(self):
        self.bot.send.side_effect = ServiceError('failed')
        with self.assertRaises(ServiceError): self.run_notify()
        self.assertEqual(self.store.state, {})

    def test_photos_failure_does_not_resend_text(self):
        self.bot.send_photos.side_effect = ServiceError('failed')
        self.run_notify()
        self.assertEqual(self.run_notify(), 0)
        self.assertEqual(self.run_notify(), 0)
        self.assertEqual(self.run_notify(), 0)
        self.assertEqual(self.bot.send_photos.call_count, 3)
        self.assertEqual(self.bot.send.call_count, 2)  # Offer + initial health.

    def test_html_escaped(self):
        self.row['title'] = '<script>x</script>'
        text = monitor.message(self.row, self.config)
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)

    def test_daily_health_and_status_transition(self):
        self.run_notify()
        self.run_notify(now=100100)
        self.assertEqual(self.bot.send.call_count, 2)
        self.run_notify(now=186401)
        self.assertEqual(self.bot.send.call_count, 3)
        self.report['coverage'][0].update(status='error', errors=['HTTP 403'])
        self.run_notify(now=186402)
        self.assertEqual(self.bot.send.call_count, 4)

    def test_report_contains_no_telegram_state(self):
        self.report.update(checked_at='2026-09-14', limits=self.config['max_total_sek'], excluded=[])
        with tempfile.TemporaryDirectory() as tmp:
            monitor.write_report(self.report, Path(tmp))
            saved = json.loads((Path(tmp) / 'watch-offers.json').read_text(encoding='utf-8'))
            self.assertNotIn('recipient', saved)
            self.assertTrue((Path(tmp) / 'watch-offers.html').exists())


if __name__ == '__main__':
    unittest.main()
