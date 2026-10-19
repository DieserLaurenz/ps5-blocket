import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from test_watch import MemoryStore
from test_watch_discovery import montredo_html
from test_watch_messages import TelegramMarkup
import watch_discovery as d
import watch_monitor as m
from monitor import ServiceError


def xml(day='2026-10-16', sek='11.1450', usd='1.1253'):
    return (f'<Envelope xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="{day}">'
            f'<Cube currency="SEK" rate="{sek}"/><Cube currency="USD" rate="{usd}"/></Cube></Cube></Envelope>')


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.config = m.config_from(Path(__file__).resolve().parents[1] / 'watch-config.json')
        self.rates = d.exchange_rates(xml(), date(2026, 10, 19))
        self.row = d.montredo(montredo_html())[0]
        self.rows = [self.row]
        d.add_prices(self.rows, self.rates)
        self.store = MemoryStore()
        self.bot = Mock(recipient='same-chat')
        self.report = {'offers': [], 'matches': [], 'hints': self.rows, 'coverage': []}

    def notify(self, now=200000):
        return m.notify(self.report, self.config, self.store, self.bot, now)

    def test_eur_to_sek_uses_full_precision_and_is_explicit_estimate(self):
        self.assertEqual(self.row['price_sek'], 11925.15)
        self.assertTrue(self.row['price_estimated'])
        self.assertNotIn('total_sek', self.row)
        text = m.hint_message(self.row)
        self.assertIn('ca. 11.925,15 SEK', text)
        self.assertIn('1.070 EUR', text)
        self.assertIn('2026-10-16', text)
        self.assertIn('Versand separat', text)
        TelegramMarkup(text)

    def test_usd_conversion_is_cross_rate_and_sek_does_not_need_fx(self):
        usd = d.hint('everywatch', d.URLS['everywatch'] + '/watch-123', 'Hamilton H36215140', 1125.3, 'USD')
        sek = d.hint('prisjakt', 'https://www.prisjakt.nu/go-to-shop/1/offer/2', 'Hamilton H36215140', 14395, 'SEK')
        d.add_prices([usd], self.rates)
        d.add_prices([sek], None)
        self.assertEqual(usd['price_sek'], 11145)
        self.assertEqual(sek['price_sek'], 14395)
        self.assertFalse(sek['price_estimated'])

    def test_invalid_rates_rejected_and_weekend_rate_allowed(self):
        for doc in (xml(day='2026-10-20'), xml(day='2026-10-11'), xml(sek='NaN'),
                    xml(sek='0'), xml(usd='0'), '<!DOCTYPE x>' + xml(), '<Envelope/>'):
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                d.exchange_rates(doc, date(2026, 10, 19))
        self.assertEqual(self.rates['EUR'], 11.145)

    def test_rate_failure_preserves_original_price_without_inventing_sek(self):
        rows = d.montredo(montredo_html())
        d.add_prices(rows, None)
        self.assertNotIn('price_sek', rows[0])
        self.assertEqual(m.discovery_price(rows[0]), '1.070 EUR')

    def test_cached_rates_avoid_repeat_requests_and_allow_safe_network_fallback(self):
        now = datetime.now(timezone.utc)
        doc = xml(now.date().isoformat())
        client = Mock()
        client.get.return_value = doc
        factory = Mock(return_value=client)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'discovery.json'
            first = d.capture_rates(output, factory)
            self.assertEqual(d.capture_rates(output, factory), first)
            factory.assert_called_once()
            first['fetched_at'] = now.replace(year=now.year - 1).isoformat()
            (output.parent / 'ecb-rates.json').write_text(json.dumps(first), encoding='utf-8')
            client.get.side_effect = ServiceError('HTTP 503')
            self.assertEqual(d.capture_rates(output, factory)['xml'], doc)

    def test_migration_preserves_known_ids_without_resending_old_hints(self):
        self.store.state = m.empty_state()
        self.store.state.update(recipient='same-chat', unverified_notified={'montredo:48900': 100, 'chrono24:12': 100})
        self.assertEqual(self.notify(), 0)  # A price-bearing status summary is sent once.
        self.assertEqual(self.store.state['discovery_prices']['montredo:48900']['lowest_native'], 1070)
        self.assertIn('chrono24:12', self.store.state['unverified_notified'])
        self.assertEqual(self.store.state['notified'], {})
        self.bot.send.assert_called_once()
        self.assertIn('11.925,15 SEK', self.bot.send.call_args.args[0])

    def test_real_price_drop_alerts_once_and_fx_only_changes_do_not(self):
        self.assertEqual(self.notify(), 1)
        self.bot.reset_mock()
        d.add_prices(self.rows, {**self.rates, 'EUR': 10.0})
        self.assertEqual(self.notify(200001), 0)
        self.bot.send.assert_not_called()
        self.row['native_price'] = 1065
        d.add_prices(self.rows, self.rates)
        self.assertEqual(self.notify(200002), 0)
        self.row['native_price'] = 1060
        d.add_prices(self.rows, self.rates)
        self.assertEqual(self.notify(200003), 1)
        text = self.bot.send.call_args.args[0]
        self.assertIn('Preissenkung', text)
        self.assertIn('111,45 SEK', text)
        TelegramMarkup(text)
        self.assertEqual(self.notify(200004), 0)
        self.assertEqual(self.store.state['discovery_prices']['montredo:48900']['lowest_native'], 1060)

    def test_prisjakt_sek_threshold_is_100_and_price_increase_does_not_reset_low(self):
        self.row = d.hint('prisjakt', 'https://www.prisjakt.nu/go-to-shop/1/offer/2', 'Hamilton H36215140', 14395, 'SEK')
        self.report['hints'] = [self.row]
        d.add_prices([self.row], None)
        self.notify()
        for price, expected in ((15000, 0), (14300, 0), (14295, 1), (14395, 0), (14295, 0)):
            self.row['native_price'] = price
            d.add_prices([self.row], None)
            self.assertEqual(self.notify(200001), expected)

    def test_status_names_both_merchants_and_concrete_prices(self):
        row = d.hint('prisjakt', 'https://www.prisjakt.nu/go-to-shop/1/offer/2', 'Hamilton H36215140', 14395, 'SEK')
        row['seller'] = 'Uret.se'
        d.add_prices([row], None)
        self.report['hints'].append(row)
        text = m.health_message(self.report, self.config)
        self.assertIn('Prisjakt / Uret.se', text)
        self.assertIn('14.395 SEK', text)
        self.assertIn('ca. 11.925,15 SEK', text)
        TelegramMarkup(text)

    def test_corrupt_price_history_rejected(self):
        self.notify()
        for value in ('NaN', -1, 0):
            state = copy.deepcopy(self.store.state)
            state['discovery_prices']['montredo:48900']['lowest_native'] = value
            with self.assertRaises((ValueError, ServiceError)):
                m.prepare_state(state, 'same-chat')


if __name__ == '__main__':
    unittest.main()
