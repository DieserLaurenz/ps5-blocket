import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scraper as s

CONFIG = json.loads((s.ROOT / 'config.json').read_text(encoding='utf-8'))


def doc(title='PS5 Disc Edition', price=2500, location='Stockholm', flags=None, identifier='123'):
    return {'id': identifier, 'heading': title, 'price': {'amount': price, 'currency_code': 'SEK'},
            'location': location, 'flags': ['shipping_exists'] if flags is None else flags, 'trade_type': 'Säljes'}


def search_html(docs, page=1, last=1):
    data = {'queries': [{'queryKey': [{'scope': 'search'}], 'state': {'data': {
        'docs': docs, 'metadata': {'paging': {'current': page, 'last': last}}}}}]}
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    return '<script type="application/json" data-react-query-state>' + encoded + '</script>'


class FakeClient:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return next(self.pages)


class ScraperTests(unittest.TestCase):
    def test_console_bundles_survive(self):
        for title in ['PS5', 'Sony PlayStation 5 spelkonsol vit', 'PS5 Slim med två handkontroller',
                      'PS5 + 2 spel', 'Playstation 5 Disc Edition med controller', 'PS5 utan kontroll']:
            with self.subTest(title=title):
                self.assertEqual(s.title_reason(title), '')

    def test_accessories_and_wrong_consoles_excluded(self):
        for title in ['PS5-spel', 'Sony PlayStation 5 DualSense handkontroll vit', 'PS5 Portal',
                      'Sony Playstation 5 kamera', 'Vertikalt stativ för PlayStation 5-konsol',
                      'Sony Playstation 5 Slim Cover Rythm Blue', 'PS5 controller', 'PS5 vr 2 headset',
                      'PS5 defekt', 'Bytes PS5 mot dator', 'PS4', 'STAR WARS Zero Company - PS5', 'PS5 och PS4 spel',
                      'Playstation 5 Suicide Squad (inga repor)', 'PS5 Diablo 4', 'PS4/PS5, The Division', 'Sony Playstation 5 och 4 spel',
                      'L2 R2 Button Extensions Trigger Extension Button Box for PS5', 'Sony PlayStation 5 vertikalställ svart',
                      '4mount väggfäste för PS5 Pro', 'PS5 kontroler', 'Playstation 5 webbkamera',
                      'Avatar Frontiers of Pandora Collector\'s Edition till PlayStation 5']:
            with self.subTest(title=title):
                self.assertTrue(s.title_reason(title))

    def test_delivery_policy(self):
        self.assertIsNotNone(s.listing(doc(), CONFIG)[0])
        self.assertIsNotNone(s.listing(doc(location='Göteborg', flags=[]), CONFIG)[0])
        self.assertIsNotNone(s.listing(doc(location='Goteborg', flags=[]), CONFIG)[0])
        self.assertIsNone(s.listing(doc(location='Stockholm', flags=[]), CONFIG)[0])
        self.assertIsNone(s.listing(doc(location='Göteborgs län', flags=[]), CONFIG)[0])

    def test_prices_and_unknown_currency(self):
        for price in [0, -5, 4001, None, '3000', True]:
            with self.subTest(price=price):
                self.assertIsNone(s.listing(doc(price=price), CONFIG)[0])
        self.assertIsNotNone(s.listing(doc(price=4000), CONFIG)[0])
        value = doc()
        value['price']['currency_code'] = 'NOK'
        self.assertIsNone(s.listing(value, CONFIG)[0])

    def test_parse_empty_vs_broken(self):
        self.assertEqual(s.parse_search(search_html([]))[0], [])
        with self.assertRaises(s.ScrapeError):
            s.parse_search('<html>Access denied</html>')

    def test_pagination_and_deduplication(self):
        client = FakeClient([search_html([doc()], 1, 2), search_html([doc(identifier='456')], 2, 2), search_html([doc()])])
        rows, urls, warnings = s.collect(CONFIG, client)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(urls), 3)
        self.assertFalse(warnings)

    def test_repeated_page_and_truncation(self):
        client = FakeClient([search_html([doc()], 1, 2), search_html([doc()], 2, 2)])
        with self.assertRaises(s.ScrapeError):
            s.collect({**CONFIG, 'queries': ['ps5']}, client)
        _, _, warnings = s.collect({**CONFIG, 'queries': ['ps5'], 'max_pages': 1}, FakeClient([search_html([doc()], 1, 2)]))
        self.assertTrue(warnings)

    def test_detail_shipping_contradiction(self):
        page = '<script type="application/ld+json">' + json.dumps({'@type': 'Product', 'description': 'Endast avhämtning', 'offers': {'availability': 'https://schema.org/InStock'}}) + '</script>'
        rows, excluded = s.select([doc()], CONFIG, FakeClient([page]))
        self.assertFalse(rows)
        self.assertTrue(excluded)
        rows, _ = s.select([doc(location='Göteborg')], CONFIG, FakeClient([page]))
        self.assertFalse(rows[0]['shipping'])
        self.assertTrue(rows[0]['pickup'])

    def test_description_fault_negation(self):
        self.assertIsNone(s.fault_in_description('inga problem med konsolen'))
        self.assertIsNone(s.fault_in_description('inga kanda defekter'))
        self.assertIsNotNone(s.fault_in_description('konsolen startar inte'))
        self.assertIsNotNone(s.fault_in_description('trasig hdmi port'))

    def test_shipping_promise_in_description(self):
        page = '<script type="application/ld+json">' + json.dumps({'@type': 'Product', 'description': 'Kan även skickas mot fraktkostnad.'}) + '</script>'
        rows, _ = s.select([doc(flags=[])], CONFIG, FakeClient([page]))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['shipping'])
        self.assertIn('description', rows[0]['shipping_source'])

    def test_history_price_drop(self):
        row = s.listing(doc(), CONFIG)[0]
        initial = s.update_history([row], {}, 'first')
        self.assertTrue(row['new'])
        row['price'] = 2000
        second = s.update_history([row], initial, 'second')
        self.assertFalse(row['new'])
        self.assertEqual(row['price_drop'], 500)
        self.assertEqual(second['123']['first_seen'], 'first')
        self.assertEqual(initial['123']['price'], 2500)

    def test_failure_preserves_saved_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            target = output / 'angebote.html'
            target.write_text('previous')
            with patch.object(s.Client, 'get', side_effect=s.ScrapeError('HTTP 403')):
                with self.assertRaises(s.ScrapeError):
                    s.run(CONFIG, output)
            self.assertEqual(target.read_text(), 'previous')

    def test_untrusted_html_cannot_break_out_of_data_script(self):
        report = {'listings': [{'title': '</script><script>alert(1)</script>'}]}
        output = s.render(report)
        self.assertNotIn('</script><script>alert(1)', output)
        self.assertIn('\\u003c/script>', output)


if __name__ == '__main__':
    unittest.main()
