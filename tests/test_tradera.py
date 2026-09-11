"""Synthetic fixtures following the official v3 WSDL; no credentials/network."""
import copy
from datetime import datetime, timezone
from html.parser import HTMLParser
import io
import os
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

import evaluator
import marketplaces
import monitor
import scraper
import tradera as t
from test_evaluator import analysis
from test_monitor import Store, Telegram, row as blocket_row


NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
CONFIG = {'max_price': 4000, 'queries': ['ps5', 'playstation 5'], 'request_delay': 0,
          'pickup_cities': ['Göteborg', 'Gothenburg', 'Goteborg'],
          'sources': {'blocket': {'enabled': True}, 'tradera': {'enabled': True, 'include_auctions': True}}}


def xml(body, tag='GetItemResult'):
    return ET.fromstring(f'<{tag} xmlns="{t.NS}">{body}</{tag}>')


def item(**changes):
    fields = {'Id': '123', 'CategoryId': '1000006', 'ShortDescription': 'PS5 Slim Disc',
              'ItemType': 'PureBuyItNow', 'BuyItNowPrice': '3000', 'NextBid': '2900',
              'OpeningBid': '1000', 'TotalBids': '3', 'MaxBid': '2850',
              'StartDate': '2026-09-01T00:00:00Z', 'EndDate': '2099-09-15T18:00:00Z',
              'RemainingQuantity': '1', 'Status': '<Ended>false</Ended>',
              'Seller': '<Id>88</Id><City>Stockholm</City><CountryName>Sverige</CountryName>',
              'ShippingOptions': '<ShippingOptionId>1</ShippingOptionId><Cost>109</Cost>',
              'AcceptsPickup': 'true', 'LongDescription': 'Fungerar bra. Inga fel.',
              'ImageLinks': '<string>https://img.tradera.net/images/123/a.jpg</string>',
              'ThumbnailLink': 'https://img.tradera.net/images/123/a-small.jpg'}
    fields.update(changes)
    return xml(''.join(f'<{key}>{val}</{key}>' for key, val in fields.items() if val is not None))


def search_page(ids=(123,), pages=1):
    return xml(f'<TotalNumberOfPages>{pages}</TotalNumberOfPages>' + ''.join(
        f'<Items><Id>{identifier}</Id><ShortDescription>PS5 Slim Disc</ShortDescription>'
        '<NextBid>2900</NextBid><BuyItNowPrice>3000</BuyItNowPrice><IsEnded>false</IsEnded></Items>'
        for identifier in ids), 'SearchResult')


class TraderaTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)

    def listing(self, **changes):
        return t.listing(item(**changes), self.config, NOW)

    def test_fixed_price_boundary_and_shipping_extra(self):
        row = self.listing(BuyItNowPrice='4000')
        self.assertEqual(row['price'], 4000)
        self.assertEqual(row['shipping_cost'], 109)
        self.assertEqual(row['sale_type'], 'fixed')
        self.assertIsNone(self.listing(BuyItNowPrice='4001'))
        self.assertIsNone(self.listing(BuyItNowPrice='0'))
        self.assertIsNone(self.listing(BuyItNowPrice='unknown'))

    def test_auction_uses_next_bid_not_current_bid_or_buy_now(self):
        row = self.listing(ItemType='Auction', NextBid='4000', MaxBid='3900', BuyItNowPrice='6000')
        self.assertEqual(row['price'], 4000)
        self.assertEqual(row['sale_type'], 'auction')
        self.assertIsNone(self.listing(ItemType='Auction', NextBid='4001', MaxBid='3900'))
        self.assertIsNone(self.listing(ItemType='Auction', NextBid=None))
        self.assertEqual(self.listing(ItemType='Auction', NextBid=None, TotalBids='0')['price'], 1000)

    def test_fixed_only_excludes_auctions(self):
        self.config['sources']['tradera']['include_auctions'] = False
        self.assertIsNone(self.listing(ItemType='Auction'))
        self.assertIsNotNone(self.listing(ItemType='ShopItem'))

    def test_inactive_expired_unknown_or_future_items_excluded(self):
        for changes in ({'Status': '<Ended>true</Ended>'}, {'Status': None},
                        {'EndDate': '2026-09-11T11:00:00Z'}, {'EndDate': 'bad'},
                        {'StartDate': '2099-01-01T00:00:00Z'}, {'RemainingQuantity': '0'},
                        {'ItemType': 'ContactOnly'}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.listing(**changes))

    def test_country_delivery_and_exact_pickup(self):
        self.assertIsNone(self.listing(Seller='<City>Göteborg</City>'))
        self.assertIsNone(self.listing(Seller='<City>Göteborg</City><CountryName>Norway</CountryName>'))
        self.assertIsNone(self.listing(ShippingOptions=None))
        seller = '<City>Göteborg</City><CountryName>Sweden</CountryName>'
        self.assertTrue(self.listing(Seller=seller, ShippingOptions=None)['pickup'])
        self.assertIsNone(self.listing(Seller=seller, ShippingOptions=None, AcceptsPickup='false'))
        self.assertIsNone(self.listing(Seller=seller.replace('Göteborg', 'Göteborg outskirts'), ShippingOptions=None))
        self.assertIsNone(self.listing(ShippingCondition='Skickar inte'))

    def test_shared_console_and_fault_filters(self):
        self.assertIsNone(self.listing(ShortDescription='PS5 controller'))
        self.assertIsNone(self.listing(LongDescription='Konsolen är defekt.'))
        self.assertIsNotNone(self.listing(LongDescription='Inga fel.'))

    def test_gallery_prefers_full_images_and_rejects_external_hosts(self):
        row = self.listing()
        self.assertEqual(len(row['photos']), 1)  # No duplicate thumbnail.
        good = 'https://img.tradera.net/images/123/a.jpg'
        self.assertEqual(t.photo_urls([good, good.replace('https:', 'http:'),
            'https://evil.invalid/a', 'https://img.tradera.net.evil.invalid/images/a',
            'https://img.tradera.net@evil.invalid/images/a', 'https://img.tradera.net:443/images/a',
            'file:///images/a', 'https://img.tradera.net/avatar/a', 'https://[bad']), [good])

    def test_same_numeric_id_does_not_collide_with_blocket(self):
        row = self.listing()
        self.assertEqual(marketplaces.key(row), 'tradera:123')
        self.assertEqual(marketplaces.key(blocket_row()), '123')
        self.assertEqual(marketplaces.url({**row, 'url': 'https://evil.invalid'}),
                         'https://www.tradera.com/item/1000006/123')
        with self.assertRaises(ValueError):
            marketplaces.url({**row, 'category_id': '1" onclick="bad'})
        store, telegram = Store(), Telegram()
        state = monitor.prepare_state({}, 'recipient')
        with patch('monitor.time.sleep'):
            self.assertEqual(monitor.notify_rows([blocket_row(), row], state, store, telegram, 'now'), 2)
            self.assertEqual(monitor.notify_rows([blocket_row(), {**row, 'price': 3200}], state, store, telegram, 'later'), 0)
        self.assertEqual(set(state['notified']), {'123', 'tradera:123'})
        self.assertIn('View listing on Tradera', telegram.messages[1])
        self.assertEqual(len(telegram.photo_batches), 1)

    def test_auction_format_rating_and_no_false_bargain(self):
        row = self.listing(ItemType='Auction', ReservePriceReached='false')
        row.update(analysis=analysis(), seller_rating={'score': 98, 'count': 100})
        row['price_comparison'] = evaluator.compare_price(row, {'fetched_at': NOW.timestamp()}, NOW.timestamp())
        text = monitor.message_for(row)
        for expected in ('Auction', 'NOT final price', 'Reserve price not met', 'UTC',
                         '98% positive', 'last 12 months', 'copy to Tradera', 'no bargain score'):
            self.assertIn(expected, text)
        self.assertNotIn('Blocket', text)

    def test_ai_cache_and_budget_are_shared_but_ids_are_namespaced(self):
        rows = [blocket_row(), self.listing()]
        config = {**self.config, 'assessment': {'enabled': True, 'max_calls_per_run': 1}}
        state = {}
        with patch('evaluator.generate', return_value=analysis()) as generate:
            evaluator.enrich(rows, config, state, lambda _: None, None, api_key='test', now=NOW.timestamp())
        self.assertEqual(generate.call_count, 1)
        self.assertIn('123', state['ai_cache'])
        self.assertNotIn('analysis', rows[1])
        with patch('evaluator.generate', return_value=analysis()) as generate:
            evaluator.enrich(rows, config, state, lambda _: None, None, api_key='test', now=NOW.timestamp())
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(set(state['ai_cache']), {'123', 'tradera:123'})
        self.assertEqual(state['ai_budget']['calls'], 2)

    def test_pagination_dedup_and_detail_rotation(self):
        self.config['sources']['tradera']['detail_limit'] = 1
        state = {}
        fake = Mock()
        fake.call.side_effect = lambda method, params: search_page((123, 456)) if method == 'Search' else item(Id=params['itemId'])
        with patch('tradera.Client', return_value=fake):
            rows, stats = t.Source(self.config, state).search()
            self.assertEqual([row['id'] for row in rows], ['123'])
            self.assertEqual(stats['scanned'], 2)
            self.assertTrue(stats['warnings'])
            rows, _ = t.Source(self.config, state).search()
            self.assertEqual([row['id'] for row in rows], ['456'])
        self.assertEqual([call.args[1]['pageNumber'] for call in fake.call.call_args_list if call.args[0] == 'Search'], [1, 1, 1, 1])

    def test_repeated_pages_and_malformed_pagination_are_not_empty_success(self):
        fake = Mock()
        with patch('tradera.Client', return_value=fake):
            for response in (search_page(pages=2), xml('', 'SearchResult'), search_page((), pages=2)):
                fake.call.return_value = response
                with self.assertRaises(scraper.ScrapeError):
                    t.Source(self.config, {}).search()

    def test_detail_failure_keeps_prior_matches_and_reports_incomplete(self):
        fake = Mock()
        def respond(method, params):
            if method == 'Search':
                return search_page((123, 456))
            if params['itemId'] == '123':
                return item()
            raise scraper.ScrapeError('HTTP 429')
        fake.call.side_effect = respond
        with patch('tradera.Client', return_value=fake):
            rows, stats = t.Source(self.config, {}).search()
        self.assertEqual(len(rows), 1)
        self.assertIn('failed', stats['warnings'][0])

    def test_feedback_summary_is_scoped_and_cached_per_seller(self):
        fake = Mock()
        fake.call.return_value = xml('<UserId>88</UserId><LastTwelveMonth><TotalPositive>49</TotalPositive>'
                                     '<TotalNegative>1</TotalNegative></LastTwelveMonth>', 'GetFeedbackSummaryResult')
        with patch('tradera.Client', return_value=fake):
            source = t.Source(self.config, {})
        row = self.listing()
        source.extras(row)
        source.extras(row)
        self.assertEqual(row['seller_rating']['score'], 98)
        self.assertEqual(row['seller_rating']['count'], 50)
        self.assertEqual(fake.call.call_count, 1)
        fake.call.return_value = xml('<UserId>77</UserId>', 'GetFeedbackSummaryResult')
        source.feedback.clear()
        source.extras(row)
        self.assertIsNone(row['seller_rating'])

    def test_source_failures_are_isolated_in_both_directions(self):
        ok = Mock()
        ok.search.return_value = ([self.listing()], {'scanned': 1, 'pages': 1, 'excluded': 0, 'warnings': []})
        bad = Mock(side_effect=scraper.ScrapeError('test source unavailable'))
        with patch.dict(os.environ, {'TRADERA_ENABLED': 'true'}):
            for failed, working in [('blocket', 'tradera'), ('tradera', 'blocket')]:
                with patch.dict(marketplaces.REGISTRY, {failed: bad, working: Mock(return_value=ok)}):
                    rows, _, statuses = marketplaces.search(self.config, {})
                self.assertEqual(len(rows), 1)
                self.assertEqual(statuses[failed]['status'], 'failed')
                self.assertEqual(statuses[working]['status'], 'ok')
            with patch.dict(marketplaces.REGISTRY, {'blocket': bad, 'tradera': bad}):
                with self.assertRaises(scraper.ScrapeError):
                    marketplaces.search(self.config, {})

    def test_disabled_source_never_uses_credentials_or_calls_api(self):
        ok = Mock()
        ok.search.return_value = ([], {'scanned': 0, 'pages': 0, 'excluded': 0, 'warnings': []})
        with patch.dict(os.environ, {'TRADERA_ENABLED': 'false'}), \
             patch.dict(marketplaces.REGISTRY, {'blocket': Mock(return_value=ok), 'tradera': Mock()}) as registry:
            _, _, statuses = marketplaces.search(self.config, {})
            registry['tradera'].assert_not_called()
        self.assertEqual(statuses['tradera']['status'], 'disabled')

    def test_missing_credentials_do_not_break_blocket(self):
        ok = Mock()
        ok.search.return_value = ([], {'scanned': 0, 'pages': 0, 'excluded': 0, 'warnings': []})
        with patch.dict(os.environ, {'TRADERA_ENABLED': 'true', 'TRADERA_APP_ID': '', 'TRADERA_APP_KEY': ''}), \
             patch.dict(marketplaces.REGISTRY, {'blocket': Mock(return_value=ok)}):
            _, _, statuses = marketplaces.search(self.config, {})
        self.assertEqual(statuses['blocket']['status'], 'ok')
        self.assertIn('credentials missing', statuses['tradera']['error'])

    def test_optional_failure_only_disables_affected_source(self):
        broken, working = Mock(), Mock()
        broken.extras.side_effect = scraper.ScrapeError('feedback access denied')
        store, telegram = Store(), Telegram()
        state = monitor.prepare_state({}, 'recipient')
        with patch('monitor.time.sleep'):
            monitor.notify_rows([self.listing(), blocket_row()], state, store, telegram, 'now',
                                {'tradera': broken, 'blocket': working})
        working.extras.assert_called_once()
        self.assertEqual(len(telegram.messages), 2)

    def test_telegram_maximum_auction_message_fits(self):
        row = self.listing(ItemType='Auction', ReservePriceReached='false')
        row.update(title='🎮&' * 300, location='🎮&' * 100, analysis=analysis(),
                   seller_rating={'score': 100, 'count': 1000000})
        row['analysis']['summary'] = '🎮' * 180
        row['analysis']['seller_message_sv'] = '🎮' * 360
        for field in ('included', 'positives', 'warnings', 'questions'):
            row['analysis'][field] = ['🎮&' * 120] * 3
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = ''
            def handle_data(self, data):
                self.text += data
        parser = Parser()
        parser.feed(monitor.message_for(row))
        self.assertLessEqual(len(parser.text.encode('utf-16-le')) // 2, 4096)

    def test_transport_auth_is_not_in_url_and_writes_are_rejected(self):
        client = t.Client(0, '1234', 'secret-value')
        payload = f'<s:Envelope xmlns:s="{t.SOAP}"><s:Body><SearchResponse xmlns="{t.NS}"><SearchResult><TotalNumberOfPages>0</TotalNumberOfPages></SearchResult></SearchResponse></s:Body></s:Envelope>'.encode()
        with patch.object(client.opener, 'open', return_value=io.BytesIO(payload)) as request:
            response = client.call('Search', {'query': 'ps5 & stuff', 'pageNumber': 1, 'categoryId': 0, 'orderBy': 'PriceAscending'})
        req = request.call_args.args[0]
        self.assertNotIn('secret-value', req.full_url)
        self.assertIn(b'<', req.data)
        self.assertIn(b'ps5 &amp; stuff', req.data)
        self.assertEqual(t.number(response, 'TotalNumberOfPages'), 0)
        with self.assertRaises(ValueError):
            client.call('Buy', {})

    def test_transport_errors_are_sanitized_and_xml_entities_rejected(self):
        client = t.Client(0, '1234', 'secret-value')
        for error in (urllib.error.URLError('secret-value'), urllib.error.HTTPError('secret-value', 403, 'secret-value', {}, None)):
            with patch.object(client.opener, 'open', side_effect=error):
                with self.assertRaises(scraper.ScrapeError) as caught:
                    client.call('Search', {})
            self.assertNotIn('secret-value', str(caught.exception))
        for payload in (b'<!DOCTYPE x><x/>', b'<broken', b'<empty/>'):
            with patch.object(client.opener, 'open', return_value=io.BytesIO(payload)):
                with self.assertRaises(scraper.ScrapeError):
                    client.call('Search', {})


if __name__ == '__main__':
    unittest.main()
