from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from test_watch import product, schema_html
import watch_sources as sources
from watch_ebay_browser import analyze, load_snapshot

URL = 'https://www.ebay.com/itm/800778739305'
NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def ebay_html(country='Germany', destination='SWE', currency='SEK', reference='H36215140'):
    p = product()
    del p['sku']
    p['offers']['url'] = URL
    p['offers']['priceCurrency'] = currency
    p['offers']['shippingDetails']['shippingDestination']['addressCountry'] = destination
    p['image'] = [{'url': 'https://i.ebayimg.com/photo.jpg'}]
    return schema_html(p, '<div class="ux-layout-section-evo__col"><dt>Reference Number</dt>'
        f'<dd>{reference}</dd></div><div class="ux-labels-values--shipping">'
        f'<div>Located in: City, {country}</div></div>')


def snapshot(html=None):
    return {'version': 1, 'reference': sources.REFERENCE, 'checked_at': NOW.isoformat(),
        'search': {'url': sources.SEARCHES['ebay'], 'final_url': sources.SEARCHES['ebay'],
                   'http_status': 200, 'html': f'<a href="{URL}">Hamilton H36215140</a>'},
        'details': [{'url': URL, 'final_url': URL, 'http_status': 200,
                     'html': html or ebay_html()}]}


class EbayBrowserTests(unittest.TestCase):
    def test_verified_reference_and_sweden_total(self):
        row = sources.parse_ebay(ebay_html(), URL)
        self.assertEqual((row['reference'], row['origin'], row['total_sek']), ('H36215140', 'DE', 11100))
        self.assertEqual(row['photos'], ['https://i.ebayimg.com/photo.jpg'])

    def test_foreign_destination_or_currency_not_promoted_to_verified_offer(self):
        for html in (ebay_html(destination='NLD'), ebay_html(currency='USD')):
            result = analyze(snapshot(html))
            self.assertEqual(result['offers'], [])
            self.assertEqual(result['coverage']['status'], 'error')

    def test_non_eu_offer_is_explicitly_excluded(self):
        result = analyze(snapshot(ebay_html(country='United States', destination='NLD', currency='USD')))
        self.assertEqual(result['offers'], [])
        self.assertEqual(result['coverage']['checked'], 1)
        self.assertIn('United States', result['excluded'][0]['reason'])

    def test_wrong_reference_not_accepted(self):
        with self.assertRaises(sources.Ineligible):
            sources.parse_ebay(ebay_html(reference='H52414130'), URL)

    def test_block_or_incomplete_details_not_empty_success(self):
        data = snapshot()
        data['details'][0]['http_status'] = 403
        self.assertEqual(analyze(data)['coverage']['status'], 'error')
        data['details'] = []
        self.assertEqual(analyze(data)['coverage']['status'], 'error')

    def test_unrelated_details_rejected(self):
        data = snapshot()
        data['details'][0]['url'] = URL.replace('800778739305', '123456')
        with self.assertRaises(ValueError):
            analyze(data)

    def test_stale_snapshot_cannot_reuse_old_offers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text(json.dumps(snapshot()), encoding='utf-8')
            self.assertEqual(len(load_snapshot(path, NOW)['offers']), 1)
            self.assertEqual(load_snapshot(path, NOW + timedelta(hours=1))['coverage']['status'], 'error')
