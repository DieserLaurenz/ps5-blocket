import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from test_watch import MemoryStore
from test_watch_messages import TelegramMarkup
import watch_discovery as d
import watch_monitor as monitor


def card(ident=123, locked=False):
    body = ('Only with premium access Titanium or Gold' if locked else
            '<div class="refno">H36215140</div><div class="ew-watch-price">Price 1,129 USD Dealer</div>Dealer IT')
    return f'<a class="ew-grid-watch-card" href="/hamilton/jazzmaster/h36215140/watch-{ident}">{body}</a>'


def every_html():
    return ('<h1>Hamilton Jazzmaster H36215140</h1><button class="active">For Sale (2)</button>'
            '<button>Sold (19)</button>' + card() + card(124, True))


def forum_html(prefix='Säljes', title='Hamilton Jazzmaster'):
    return ('<h1>Handla - Säljes, Bytes, Köpes</h1><div class="structItem-title">'
            f'<a class="labelLink">{prefix}</a><a data-tp-primary="on" '
            f'href="/threads/hamilton.123/">{title}</a></div>')


def montredo_html():
    product = {'@type': 'Product', 'sku': '48900', 'offers': {
        'url': d.URLS['montredo'], 'availability': 'https://schema.org/InStock',
        'price': '1071.43', 'priceCurrency': 'EUR'}}
    return ('<script>Shopify.country = "SE";</script><div class="reference-block">H36215140</div>'
            '<script type="application/ld+json">' + json.dumps(product) + '</script>'
            '<button>Add to cart</button><sale-price>Sale price €1,070</sale-price>')


class DiscoveryTests(unittest.TestCase):
    def test_everywatch_ignores_locked_and_hidden_state(self):
        html = every_html() + '<script type="application/json">{"watch-999": "H36215140 100 USD"}</script>'
        rows, count = d.everywatch(html)
        self.assertEqual(count, 2)
        self.assertEqual([r['id'] for r in rows], ['everywatch:123'])
        self.assertEqual(rows[0]['native_currency'], 'USD')
        self.assertNotIn('total_sek', rows[0])

    def test_everywatch_requires_active_sale_tab(self):
        with self.assertRaises(ValueError):
            d.everywatch(every_html().replace('class="active">For Sale', '>For Sale').replace('<button>Sold', '<button class="active">Sold'))

    def test_everywatch_wrong_reference_or_sold_card_never_alerts(self):
        for html in (every_html().replace('class="refno">H36215140', 'class="refno">H36215141'),
                     every_html().replace('USD Dealer', 'USD Sold')):
            with self.assertRaises(ValueError):
                d.everywatch(html)

    def test_gate_is_not_successful_empty_search(self):
        snapshot = d.new_snapshot()
        for source in d.SOURCE_NAMES:
            snapshot['pages'][source] = [{'url': d.URLS[source], 'html': '<h1>Only Collectors Beyond This Point</h1>'}]
        report = d.analyze(snapshot)
        self.assertEqual(report['hints'], [])
        self.assertTrue(all(c['status'] == 'error' for c in report['coverage']))

    def test_initial_http_error_is_never_a_readable_empty_forum(self):
        snapshot = d.new_snapshot()
        snapshot['pages']['klocksnack'] = [{'url': d.URLS['klocksnack'], 'error': 'HTTP 403'}]
        status = d.analyze(snapshot, ['klocksnack'])['coverage'][0]
        self.assertEqual(status['status'], 'error')
        self.assertFalse(status['search_readable'])

    def test_montredo_euro_price_is_hint_and_sweden_context_required(self):
        row = d.montredo(montredo_html())[0]
        self.assertEqual(row['native_price'], 1070)
        self.assertEqual(row['native_currency'], 'EUR')
        self.assertNotIn('total_sek', row)
        for html in (montredo_html().replace('"SE"', '"DE"'),
                     montredo_html().replace('>H36215140<', '>H36215141<')):
            with self.assertRaises(ValueError):
                d.montredo(html)

    def test_montredo_sold_out_and_price_conflict(self):
        self.assertEqual(d.montredo(montredo_html().replace('InStock', 'OutOfStock')), [])
        with self.assertRaises(ValueError):
            d.montredo(montredo_html().replace('€1,070', '€800'))

    def test_prisjakt_only_exact_in_stock_merchant_rows(self):
        a = ('<a data-test="OfferClickoutButton" href="https://www.prisjakt.nu/go-to-shop/3842/offer/1080987?client_id=1551">'
             '<img alt="Uret.se">Hamilton H36215140 14 395 kr I lager</a>')
        html = '<h1>Hamilton H36215140</h1>' + a + a + a.replace('1080987', '999').replace('H36215140', 'H36215141')
        rows = d.prisjakt(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'prisjakt:3842-1080987')
        self.assertEqual(rows[0]['native_price'], 14395)
        self.assertNotIn('total_sek', rows[0])

    def test_forum_only_sale_prefix_and_hamilton_candidates(self):
        self.assertEqual(d.forum_candidates(forum_html())[0], ['https://klocksnack.se/threads/hamilton.123/'])
        for prefix in ('Avslutad', 'Köpes', 'Viktigt', 'Såld'):
            self.assertEqual(d.forum_candidates(forum_html(prefix))[0], [])
        self.assertEqual(d.forum_candidates(forum_html(title='Rolex'))[0], [])

    def test_forum_exact_reference_must_be_in_first_post(self):
        url = 'https://klocksnack.se/threads/hamilton.123/'
        html = '<h1>Säljes Hamilton</h1><div class="message-body">Hamilton H36215140</div>'
        self.assertEqual(len(d.forum_offer(html, url)), 1)
        for modified in (html.replace('H36215140', 'H36215141'), html.replace('Säljes', 'Avslutad'),
                         html.replace('H36215140', 'H36215140 såld')):
            self.assertEqual(d.forum_offer(modified, url), [])
        self.assertEqual(d.forum_offer(html.replace('H36215140', 'H36215141') + '<div class="message-body">H36215140</div>', url), [])

    def test_unfetched_forum_details_report_incomplete_coverage(self):
        snap = d.new_snapshot()
        snap['pages']['klocksnack'] = [{'url': d.URLS['klocksnack'], 'html': forum_html()}]
        result = d.analyze(snap, ['klocksnack'])
        self.assertEqual(result['hints'], [])
        self.assertEqual(result['coverage'][0]['found'], 1)
        self.assertTrue(result['coverage'][0]['errors'])

    def test_cross_host_urls_and_wrong_model_path_rejected(self):
        for url in ('https://evil.example/hamilton/jazzmaster/h36215140/watch-123',
                    'https://everywatch.com/hamilton/jazzmaster/h36215141/watch-123'):
            with self.assertRaises(ValueError):
                d.hint_id('everywatch', url)

    def test_stale_future_and_missing_snapshots_never_reuse_hints(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            self.assertEqual(d.load_snapshot(path, now)['hints'], [])
            for offset in (-1801, 61):
                snap = d.new_snapshot()
                snap['checked_at'] = (now + timedelta(seconds=offset)).isoformat()
                snap['pages']['everywatch'] = [{'url': d.URLS['everywatch'], 'html': every_html()}]
                path.write_text(json.dumps(snap), encoding='utf-8')
                self.assertEqual(d.load_snapshot(path, now)['hints'], [])

    def test_new_hints_dedupe_without_resetting_old_history(self):
        config = monitor.config_from(Path(__file__).resolve().parents[1] / 'watch-config.json')
        rows, _ = d.everywatch(every_html())
        store = MemoryStore()
        store.state = monitor.empty_state()
        store.state.update(recipient='original', unverified_notified={'chrono24:123': 123456})
        bot = Mock(recipient='original')
        report = {'offers': [], 'matches': [], 'hints': rows, 'coverage': []}
        monitor.notify(report, config, store, bot, 200000)
        before = copy.deepcopy(store.state)
        bot.reset_mock()
        monitor.notify(report, config, store, bot, 200001)
        bot.send.assert_not_called()
        self.assertEqual(store.state, before)
        self.assertEqual(store.state['unverified_notified']['chrono24:123'], 123456)
        self.assertEqual(store.state['notified'], {})

    def test_hint_markup_escaped_and_no_budget_promise(self):
        row = d.montredo(montredo_html())[0]
        row['search_text'] = '<unsafe> & unknown'
        text = monitor.hint_message(row)
        TelegramMarkup(text)
        self.assertIn('1.070 EUR', text)
        self.assertIn('Kein Budgetcheck', text)
        self.assertIn('&lt;unsafe&gt;', text)


if __name__ == '__main__':
    unittest.main()
