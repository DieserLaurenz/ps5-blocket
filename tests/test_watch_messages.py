import copy
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

from test_watch import CHRONO, MemoryStore, schema_html
import watch_monitor as monitor
import watch_sources as sources


class TelegramMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.stack = []
        self.feed(text)
        assert not self.stack, 'Unclosed Telegram HTML'

    def handle_starttag(self, tag, attrs):
        assert tag in ('b', 'i', 'code', 'a'), tag
        if tag == 'a':
            assert dict(attrs)['href'].startswith('https://')
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


class MessageLayoutTests(unittest.TestCase):
    def setUp(self):
        self.config = monitor.config_from(Path(__file__).resolve().parents[1] / 'watch-config.json')
        self.row = sources.parse_schema('chrono24', schema_html(), CHRONO)
        self.hint = {'id': 'chrono24:123456', 'source': 'chrono24', 'reference': sources.REFERENCE,
                     'url': CHRONO, 'search_text': "Hamilton Jazzmaster Performer Men's H36215140 11 967 SEK + 220 SEK leverans IT"}
        self.report = {'matches': [], 'offers': [self.row], 'hints': [self.hint], 'coverage': [
            {'source': 'chrono24', 'status': 'partial', 'search_readable': True,
             'checked': 0, 'found': 4, 'errors': ['Long technical details']},
            {'source': 'uret', 'status': 'ok', 'checked': 1, 'found': 1, 'errors': []},
            {'source': 'ebay', 'status': 'error', 'checked': 0, 'found': 0, 'errors': ['HTTP 403']}]}

    def test_german_amounts(self):
        self.assertEqual(monitor.amount(11967), '11.967 SEK')
        self.assertEqual(monitor.amount(11589.75), '11.589,75 SEK')
        self.assertEqual(monitor.amount(0), '0 SEK')

    def test_hint_is_compact_and_does_not_duplicate_raw_title(self):
        text = monitor.hint_message(self.hint)
        self.assertIn('<b>11.967 SEK</b> · Suchpreis, unbestätigt', text)
        self.assertIn('Italien · laut Suchtreffer', text)
        self.assertNotIn("Men's", text)
        self.assertNotIn('220 SEK', text)  # No destination-independent shipping promise.
        self.assertLess(len(re.sub('<[^>]+>', '', text)), 500)

    def test_hint_does_not_mutate_source_data_or_prices(self):
        before = copy.deepcopy(self.hint)
        monitor.hint_message(self.hint)
        self.assertEqual(self.hint, before)
        self.assertNotIn('total_sek', self.hint)

    def test_shipping_only_is_not_presented_as_watch_price(self):
        for card in ('Hamilton H36215140 + 220 SEK leverans IT',
                     'Hamilton H36215140 Pris på begäran + 220 SEK leverans IT'):
            self.hint['search_text'] = card
            self.assertNotIn('<b>220 SEK</b>', monitor.hint_message(self.hint))

    def test_unknown_price_is_not_zero(self):
        self.hint['search_text'] = 'Hamilton H36215140'
        text = monitor.hint_message(self.hint)
        self.assertIn('Preis nicht erfasst', text)
        self.assertNotIn('<b>0 SEK</b>', text)

    def test_on_request_translated(self):
        self.hint['search_text'] = 'Hamilton H36215140 Pris på begäran ES'
        self.assertIn('<b>Preis auf Anfrage</b>', monitor.hint_message(self.hint))

    def test_unknown_country_not_invented(self):
        self.hint['search_text'] = 'Hamilton H36215140 8 000 SEK'
        self.assertNotIn('📍', monitor.hint_message(self.hint))

    def test_verified_offer_has_total_and_shipping_evidence(self):
        text = monitor.message(self.row, self.config)
        self.assertIn('<b>11.100 SEK</b> inkl. Versand nach Schweden', text)
        self.assertIn('Uhr 11.000 SEK · Versand 100 SEK', text)
        self.assertIn('Neu / ungetragen', text)
        self.assertIn('Standort Italien', text)

    def test_drop_amount_and_previous_price(self):
        text = monitor.message(self.row, self.config, old=11500)
        self.assertIn('↓ 400 SEK', text)
        self.assertIn('(11.500 SEK)', text)

    def test_summary_human_labels_and_named_links(self):
        text = monitor.health_message(self.report, self.config)
        self.assertIn('Suchtreffer lesbar', text)
        self.assertIn('Zugriff blockiert', text)
        self.assertIn('Gebraucht: kein geprüftes Angebot', text)
        self.assertNotIn('Long technical details', text)
        self.assertNotIn('\nhttps://', text)
        self.assertIn('<a href=', text)

    def test_all_messages_balanced_safe_telegram_markup(self):
        self.row['scope'] = '<unsafe> & unknown'
        self.row['availability'] = '<script>bad</script>'
        for text in (monitor.message(self.row, self.config), monitor.hint_message(self.hint),
                     monitor.health_message(self.report, self.config)):
            with self.subTest(text=text):
                TelegramMarkup(text)
                self.assertLess(len(text), 4096)

    def test_status_is_sent_in_html_mode(self):
        bot = Mock(recipient='test-recipient')
        report = {**self.report, 'hints': []}
        monitor.notify(report, self.config, MemoryStore(), bot, 100000)
        bot.send.assert_called_once_with(monitor.health_message(report, self.config), html=True)


if __name__ == '__main__':
    unittest.main()
