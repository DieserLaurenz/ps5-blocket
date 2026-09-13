import copy
from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

import ifk_monitor as m


NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)


def fixture_html(home='IFK Göteborg', away='Västerås SK', league='Allsvenskan 2026', link=''):
    return ('<ul class="matches__list"><li class="match match--played">'
            f'<div class="teams"><span class="home-team">{home}</span>'
            f'<span class="home-team">{away}</span></div>'
            '<div class="team-arena"><span class="sub-title">Gamla Ullevi</span></div>'
            '<div class="date-type"><span class="title">fre 9/10 19.00</span>'
            f'<span class="sub-title">{league}</span></div>'
            + (f'<a href="{link}">Köp biljett</a>' if link else '') + '</li></ul>')


def fixture(link=''):
    return m.parse_fixtures(fixture_html(link=link), 2026, NOW.date())[0]


def post(title='Då släpps biljetterna till VSK och DIF', body=None, identifier=46930):
    return {'id': identifier, 'title': {'rendered': title},
            'content': {'rendered': body or 'Biljettsläpp till hemmamatcherna i Allsvenskan. Allmänt biljettsläpp måndag 11.00.'},
            'link': 'https://ifkgoteborg.se/nyheter/nyheter/2026/ticket-info/',
            'categories': [94], 'date_gmt': '2026-09-07T09:08:24', 'modified_gmt': '2026-09-07T09:08:24'}


class Store:
    def __init__(self):
        self.saved = []

    def save(self, state):
        self.saved.append(copy.deepcopy(state))


class Telegram:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def send(self, text, **kwargs):
        if self.fail:
            raise m.ServiceError('Telegram unavailable')
        self.messages.append(text)
        return len(self.messages)


class ParsingTests(unittest.TestCase):
    def test_home_match_and_ticket_link(self):
        row = fixture('https://ifkgoteborg.ebiljett.nu/Tickets/Select/abc')
        self.assertEqual(row['title'], 'IFK Göteborg – Västerås SK')
        self.assertEqual(row['date'], '2026-10-09')
        self.assertTrue(row['url'].endswith('/abc'))

    def test_excludes_away_women_youth_and_past(self):
        for source in (fixture_html(home='AIK', away='IFK Göteborg'),
                       fixture_html(league='Elitettan 2026'), fixture_html(league='P19 Allsvenskan')):
            self.assertEqual(m.parse_fixtures(source, 2026, NOW.date()), [])
        self.assertEqual(m.parse_fixtures(fixture_html(), 2026, date(2026, 10, 10)), [])

    def test_block_or_broken_markup_does_not_mean_empty(self):
        for source in ('<title>AXS Access Info</title>', '<ul class="matches__list"><li class="match">broken</li></ul>'):
            with self.assertRaises(m.ServiceError):
                m.parse_fixtures(source, 2026, NOW.date())
        self.assertEqual(m.parse_fixtures('<ul class="matches__list"></ul>', 2026, NOW.date()), [])

    def test_mens_season_discovery_includes_next_year_only(self):
        selector = ('<select id="match-schedule"><option value="2026">Herr 2026</option>'
                    '<option value="2027">Herr 2027</option><option value="dam-2026">Dam 2026</option>'
                    '<option value="2025">Herr 2025</option></select>')
        with patch.object(m, 'fetch', side_effect=[(selector, {}), ('"<ul class=\\"matches__list\\"></ul>"', {}),
                                                 ('"<ul class=\\"matches__list\\"></ul>"', {})]) as fetch:
            self.assertEqual(m.get_fixtures(NOW.date()), [])
            self.assertEqual([c.args[1]['match_type'] for c in fetch.call_args_list[1:]], [2026, 2027])

    def test_ticket_announcements_only(self):
        news = [post(), post('Truppen mot AIK'), post('Biljettinfo till Gent borta'),
                post('Biljettsläpp dam', 'Damlaget spelar hemmamatch på Valhalla i Elitettan.'),
                post('Biljettsläpp', 'En händelse utan tydlig lagtillhörighet.')]
        self.assertEqual(len(m.parse_news(news, [fixture()])), 1)

    def test_mixed_announcement_includes_mens_home_info(self):
        news = post('Kommande biljettsläpp', 'Hemmatch i Allsvenskan på Gamla Ullevi. Damlaget i Elitettan.')
        self.assertEqual(len(m.parse_news([news], [])), 1)

    def test_swapped_script_and_whitespace_do_not_notify(self):
        p = post()
        first = m.parse_news([p], [fixture()])[0]
        p['content']['rendered'] += '<script>changing_banner()</script>   '
        second = m.parse_news([p], [fixture()])[0]
        self.assertEqual(first['fingerprint'], second['fingerprint'])

    def test_escapes_message_and_rejects_unsafe_link(self):
        row = fixture('javascript:alert(1)')
        self.assertEqual(row['url'], '')
        row = fixture('https://ifkgoteborg.ebiljett.nu/Tickets/Select/abc')
        row['title'] = '<b>Injected & title</b>'
        self.assertIn('&lt;b&gt;Injected &amp; title&lt;/b&gt;', m.ticket_message(row))

    def test_news_paginates(self):
        import json
        with patch.object(m, 'fetch', side_effect=[(json.dumps([post()]), {'X-WP-TotalPages': '2'}),
                                                 (json.dumps([post(identifier=2)]), {'X-WP-TotalPages': '2'})]) as fetch:
            self.assertEqual(len(m.get_news([fixture()], NOW)), 2)
            self.assertIn('page=2', fetch.call_args_list[1].args[0])


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.state = m.prepare_state({}, 'recipient')
        self.store, self.telegram = Store(), Telegram()
        self.rows = [fixture()]
        self.news = m.parse_news([post()], self.rows)

    def run_check(self):
        return m.process(self.rows, self.news, self.state, self.store, self.telegram, NOW)

    def test_baseline_then_unchanged(self):
        self.assertEqual(self.run_check(), 1)
        self.assertTrue(self.state['initialized'])
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(len(self.telegram.messages), 1)

    def test_new_link_then_no_repeat_after_disappearing_or_rescheduling(self):
        self.run_check()
        self.rows[0]['url'] = 'https://ifkgoteborg.ebiljett.nu/Tickets/Select/abc'
        self.assertEqual(self.run_check(), 1)
        self.rows[0]['url'] = ''
        self.assertEqual(self.run_check(), 0)
        self.rows = [fixture('https://ifkgoteborg.ebiljett.nu/Tickets/Select/abc')]
        self.rows[0]['when'] = 'lör 10/10 19.00'
        self.assertEqual(self.run_check(), 0)
        self.rows[0]['url'] += 'new'
        self.assertEqual(self.run_check(), 1)

    def test_article_update_and_new_article_alert_once(self):
        self.run_check()
        p = post()
        p['content']['rendered'] += ' Allmän försäljning från 14 september.'
        self.news = m.parse_news([p, post(identifier=2)], self.rows)
        self.assertEqual(self.run_check(), 2)
        self.assertTrue(any('Aktualisierte Ticketinfo' in s for s in self.telegram.messages))
        self.assertEqual(self.run_check(), 0)

    def test_failed_delivery_is_retried(self):
        self.run_check()
        self.rows[0]['url'] = 'https://ifkgoteborg.ebiljett.nu/Tickets/Select/abc'
        self.telegram.fail = True
        with self.assertRaises(m.ServiceError):
            self.run_check()
        self.assertFalse(self.state['tickets'])
        self.telegram.fail = False
        self.assertEqual(self.run_check(), 1)

    def test_startup_failure_preserves_baseline_and_retries_welcome(self):
        self.telegram.fail = True
        with self.assertRaises(m.ServiceError):
            self.run_check()
        self.assertTrue(self.store.saved[-1]['initialized'])
        self.assertNotIn('welcome_sent', self.state)
        self.telegram.fail = False
        self.assertEqual(self.run_check(), 1)

    def test_partial_delivery_persisted(self):
        self.run_check()
        self.news = m.parse_news([post(identifier=2), post(identifier=3)], self.rows)
        original = self.telegram.send
        def fail_second(text, **kwargs):
            if len(self.telegram.messages) == 2:
                raise m.ServiceError('second send fails')
            return original(text, **kwargs)
        self.telegram.send = fail_second
        with self.assertRaises(m.ServiceError):
            self.run_check()
        self.assertIn('2', self.store.saved[-1]['news'])
        self.assertNotIn('3', self.store.saved[-1]['news'])

    def test_invalid_state_or_changed_recipient_fails(self):
        for state in ({'version': 99}, {**self.state, 'tickets': {'id': 'invalid'}},
                      {**self.state, 'recipient': 'other'}):
            with self.assertRaises(m.ServiceError):
                m.prepare_state(state, 'recipient')

    def test_dry_run_does_not_construct_sender_or_store(self):
        with patch.object(m, 'get_fixtures', return_value=[]), patch.object(m, 'get_news', return_value=[]), \
             patch.object(m, 'Telegram') as telegram, patch.object(m, 'LocalStore') as store:
            self.assertEqual(m.main(['--dry-run']), 0)
            telegram.assert_not_called()
            store.assert_not_called()

    def test_source_failure_preserves_baseline_and_deduplicates_warning(self):
        self.run_check()
        baseline = copy.deepcopy(self.state)
        self.store.load = lambda: copy.deepcopy(baseline)
        self.telegram.recipient = 'recipient'
        with patch.dict(m.os.environ, {'GITHUB_ACTIONS': 'false'}, clear=False), \
             patch.object(m, 'Telegram', return_value=self.telegram), \
             patch.object(m, 'LocalStore', return_value=self.store), \
             patch.object(m, 'get_fixtures', side_effect=m.ServiceError('HTTP 403')):
            self.assertEqual(m.main([]), 1)
            saved = self.store.saved[-1]
            self.assertEqual(saved['news'], baseline['news'])
            self.assertEqual(saved['tickets'], baseline['tickets'])
            self.store.load = lambda: copy.deepcopy(saved)
            self.assertEqual(m.main([]), 1)
        self.assertEqual(len(self.telegram.messages), 2)  # welcome, one failure alert


if __name__ == '__main__':
    unittest.main()
