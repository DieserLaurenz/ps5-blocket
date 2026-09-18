import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_watch import CHRONO, MemoryStore, product, schema_html
from monitor import ServiceError
import watch_chrono_browser as browser
import watch_monitor as monitor
import watch_sources as sources

NOW = datetime(2026, 9, 18, 16, tzinfo=timezone.utc)


def search_html(extra=''):
    return ('<h1>Hamilton H36215140</h1>'
            f'<a class="wt-listing-item-link" href="{CHRONO}">Hamilton Performer 8 000 SEK</a>' + extra)


def snapshot(detail=None):
    return {'version': 1, 'reference': sources.REFERENCE, 'checked_at': NOW.isoformat(),
            'search': {'url': sources.SEARCHES['chrono24'], 'final_url': sources.SEARCHES['chrono24'],
                       'http_status': 200, 'html': search_html()},
            'details': [] if detail is None else [detail]}


def detail(status=200, html=None, final_url=CHRONO):
    return {'url': CHRONO, 'final_url': final_url, 'http_status': status,
            'html': schema_html() if html is None else html}


class SearchTests(unittest.TestCase):
    def test_only_real_cards_dedup_no_footer_recommendations(self):
        extra = (f'<a href="{CHRONO.replace("123456", "999")}">Recommendation</a>'
                 f'<a class="wt-listing-item-link" href="{CHRONO}">Hamilton Performer</a>')
        rows, truncated = browser.search_candidates(search_html(extra))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'chrono24:123456')
        self.assertNotIn('total_sek', rows[0])
        self.assertFalse(truncated)

    def test_heading_is_required(self):
        for html in (search_html().replace('H36215140', 'H36215141'), '<title>Challenge</title>'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                browser.search_candidates(html)

    def test_explicit_empty_vs_missing_markup(self):
        html = '<h1>Hamilton H36215140</h1>'
        with self.assertRaises(ValueError):
            browser.search_candidates(html)
        self.assertEqual(browser.search_candidates(html + '<p>0 visa även annonser</p>')[0], [])

    def test_wrong_reference_ad_is_not_hint(self):
        rows, _ = browser.search_candidates(search_html().replace('Hamilton Performer', 'Hamilton H36105140'))
        self.assertEqual(rows, [])

    def test_pagination_detected(self):
        self.assertTrue(browser.search_candidates(search_html('<a href="?pageNr=2">2</a>'))[1])

    def test_unsafe_listing_urls(self):
        for url in ('https://evil.example/hamilton/x--id123.htm',
                    'https://www.chrono24.se:9000/hamilton/x--id123.htm',
                    'https://user:pw@www.chrono24.se/hamilton/x--id123.htm',
                    'https://www.chrono24.se/info/--id123.htm'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                browser.listing_id(url)

    def test_query_tracking_removed(self):
        rows, _ = browser.search_candidates(search_html().replace(CHRONO, CHRONO + '?tracking=abc'))
        self.assertEqual(rows[0]['url'], CHRONO)


class SnapshotTests(unittest.TestCase):
    def test_search_success_detail_blocked_is_hint_not_offer(self):
        report = browser.analyze(snapshot(detail(403)))
        self.assertEqual(report['offers'], [])
        self.assertEqual(len(report['hints']), 1)
        self.assertEqual(report['coverage']['status'], 'partial')
        self.assertEqual(report['coverage']['checked'], 0)

    def test_200_detail_challenge_is_not_an_offer(self):
        report = browser.analyze(snapshot(detail(html='<h1>Verify human</h1>')))
        self.assertEqual(report['offers'], [])
        self.assertEqual(len(report['hints']), 1)

    def test_search_blocked_never_uses_embedded_links(self):
        data = snapshot(); data['search']['http_status'] = 403
        report = browser.analyze(data)
        self.assertEqual(report['coverage']['status'], 'error')
        self.assertEqual(report['hints'], [])

    def test_search_redirect_not_valid(self):
        data = snapshot(); data['search']['final_url'] = sources.BASES['chrono24']
        self.assertEqual(browser.analyze(data)['coverage']['status'], 'error')

    def test_detail_verified_has_no_parallel_hint(self):
        report = browser.analyze(snapshot(detail()))
        self.assertEqual(report['coverage']['status'], 'ok')
        self.assertEqual(report['offers'][0]['total_sek'], 11100)
        self.assertEqual(report['hints'], [])

    def test_unsuitable_detail_is_excluded_not_hint(self):
        for field, value in [('availability', 'https://schema.org/OutOfStock'), ('priceCurrency', 'USD')]:
            p = product(); p['offers'][field] = value
            report = browser.analyze(snapshot(detail(html=schema_html(p))))
            self.assertEqual(report['offers'], [])
            self.assertEqual(report['hints'], [])
            self.assertEqual(len(report['excluded']), 1)

    def test_sweden_evidence_still_required(self):
        p = product(); p['offers']['shippingDetails']['shippingDestination']['addressCountry'] = 'CH'
        self.assertEqual(browser.analyze(snapshot(detail(html=schema_html(p))))['offers'], [])

    def test_removed_listing_not_a_hint(self):
        for d in (detail(404), detail(410), detail(final_url=sources.SEARCHES['chrono24'])):
            report = browser.analyze(snapshot(d))
            self.assertEqual(report['hints'], [])
            self.assertEqual(len(report['excluded']), 1)

    def test_unvisited_cards_retained_after_first_block(self):
        data = snapshot(detail(403))
        data['search']['html'] += search_html().replace('<h1>Hamilton H36215140</h1>', '').replace('123456', '999')
        self.assertEqual(len(browser.analyze(data)['hints']), 2)

    def test_empty_valid_search_is_success(self):
        data = snapshot(); data['search']['html'] = '<h1>Hamilton H36215140</h1><p>0 visa även annonser</p>'
        report = browser.analyze(data)
        self.assertEqual(report['coverage']['status'], 'ok')
        self.assertEqual(report['hints'], [])

    def test_unrelated_details_rejected(self):
        data = snapshot(detail()); data['details'][0]['url'] = CHRONO.replace('123456', '999')
        with self.assertRaises(ValueError): browser.analyze(data)

    def test_freshness_missing_and_corrupt_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.json'
            self.assertEqual(browser.load_snapshot(path, NOW)['coverage']['status'], 'error')
            for data in ({}, [], snapshot(), {**snapshot(), 'version': 2}):
                path.write_text(json.dumps(data), encoding='utf-8')
                result = browser.load_snapshot(path, NOW + timedelta(hours=1))
                self.assertEqual(result['coverage']['status'], 'error')
                self.assertEqual(result['hints'], [])
            path.write_text(json.dumps(snapshot(detail(403))), encoding='utf-8')
            self.assertEqual(len(browser.load_snapshot(path, NOW)['hints']), 1)
            self.assertEqual(browser.load_snapshot(path, NOW - timedelta(hours=1))['hints'], [])


class HintNotifyTests(unittest.TestCase):
    def setUp(self):
        self.config = monitor.config_from(Path(__file__).resolve().parents[1] / 'watch-config.json')
        self.config['chrono24_unverified_hints'] = True
        parsed = browser.analyze(snapshot(detail(403)))
        self.report = {'offers': [], 'matches': [], 'hints': parsed['hints'], 'coverage': [parsed['coverage']]}
        self.store = MemoryStore()
        self.bot = Mock(recipient='recipient')
        self.bot.send.return_value = 123

    def notify(self):
        return monitor.notify(self.report, self.config, self.store, self.bot, NOW.timestamp())

    def test_hint_once_and_separate_history(self):
        self.assertEqual(self.notify(), 1)
        self.assertEqual(self.notify(), 0)
        self.assertEqual(self.store.state['notified'], {})
        self.assertEqual(len(self.store.state['unverified_notified']), 1)
        self.assertEqual(self.bot.send.call_count, 2)  # Hint + health, not two hints.
        self.bot.send_photos.assert_not_called()

    def test_returning_id_does_not_repeat(self):
        self.notify()
        hints = self.report.pop('hints')
        self.notify()
        self.report['hints'] = hints
        self.assertEqual(self.notify(), 0)

    def test_later_verified_offer_still_alerts(self):
        self.notify()
        row = sources.parse_schema('chrono24', schema_html(), CHRONO)
        self.report.update(matches=[row], offers=[row], hints=[])
        self.assertEqual(self.notify(), 1)
        self.assertIn(row['id'], self.store.state['notified'])

    def test_verified_alert_then_block_does_not_send_hint(self):
        row = sources.parse_schema('chrono24', schema_html(), CHRONO)
        self.report.update(matches=[row], offers=[row])
        self.assertEqual(self.notify(), 1)
        self.assertEqual(self.store.state['unverified_notified'], {})

    def test_disabled_hints(self):
        self.config['chrono24_unverified_hints'] = False
        self.assertEqual(self.notify(), 0)
        self.assertEqual(self.store.state['unverified_notified'], {})

    def test_send_failure_not_recorded(self):
        self.bot.send.side_effect = ServiceError('failure')
        with self.assertRaises(ServiceError): self.notify()
        self.assertEqual(self.store.state, {})

    def test_old_state_preserved_and_extended(self):
        self.store.state = {'version': 1, 'reference': sources.REFERENCE, 'recipient': 'recipient',
                            'notified': {'uret:999': {'lowest_total': 10000}}, 'health_at': 1}
        self.notify()
        self.assertEqual(self.store.state['notified']['uret:999']['lowest_total'], 10000)

    def test_corrupt_hint_history_fails(self):
        for hints in ([], {'chrono24:123': -1}, {'other:123': 1}):
            state = {**monitor.empty_state(), 'unverified_notified': hints}
            with self.subTest(hints=hints), self.assertRaises((ServiceError, ValueError)):
                monitor.prepare_state(state, 'recipient')

    def test_hint_unambiguous_and_escaped(self):
        row = copy.deepcopy(self.report['hints'][0]); row['search_text'] = '<b>Cheap</b>'
        text = monitor.hint_message(row)
        self.assertIn('ungeprüfter Hinweis', text)
        self.assertIn('kann auch Neuware sein', text)
        self.assertIn('NICHT bestätigt', text)
        self.assertIn('&lt;b&gt;', text)
        self.assertNotIn('<b>Cheap</b>', text)
        self.assertNotIn('inkl. Versand', text)

    def test_hint_does_not_change_report_match_count(self):
        self.report.update(checked_at=NOW.isoformat(), limits=self.config['max_total_sek'], excluded=[])
        with tempfile.TemporaryDirectory() as tmp:
            monitor.write_report(self.report, Path(tmp))
            report = json.loads((Path(tmp) / 'watch-offers.json').read_text(encoding='utf-8'))
            self.assertEqual(report['matches'], [])
            self.assertEqual(len(report['hints']), 1)


class BrowserCaptureTests(unittest.TestCase):
    def test_capture_stops_on_first_403_but_keeps_search(self):
        page = Mock()
        context = Mock(); context.new_page.return_value = page
        chromium = Mock(); chromium.new_context.return_value = context

        def navigation(unused_page, url):
            page.url = url
            return ((search_html(), {'http_status': 200}) if url == sources.SEARCHES['chrono24']
                    else ('<h1>Challenge</h1>', {'http_status': 403}))

        data = snapshot(); data['search'] = {}
        with patch('watch_browser_probe.navigate', side_effect=navigation) as navigate:
            browser.capture(chromium, data)
        self.assertEqual(navigate.call_count, 2)
        self.assertEqual(data['details'][0]['http_status'], 403)
        self.assertEqual(len(browser.analyze(data)['hints']), 1)
        context.close.assert_called_once()

    def test_browser_error_keeps_already_read_search(self):
        page = Mock()
        context = Mock(); context.new_page.return_value = page
        chromium = Mock(); chromium.new_context.return_value = context

        def navigation(unused_page, url):
            page.url = url
            if url != sources.SEARCHES['chrono24']:
                raise RuntimeError('network failure')
            return search_html(), {'http_status': 200}

        data = snapshot(); data['search'] = {}
        with patch('watch_browser_probe.navigate', side_effect=navigation), self.assertRaises(RuntimeError):
            browser.capture(chromium, data)
        self.assertEqual(len(browser.analyze(data)['hints']), 1)
        context.close.assert_called_once()


class MainIntegrationTests(unittest.TestCase):
    def run_main(self, browser_report):
        http_coverage = [{'source': 'uret', 'status': 'ok', 'checked': 0, 'found': 0, 'errors': []}]
        with (patch.object(sys, 'argv', ['watch_monitor.py', '--dry-run', '--chrono-snapshot', 'snapshot.json']),
              patch.object(monitor.sources, 'collect', return_value=([], http_coverage, [])) as collect,
              patch.object(browser, 'load_snapshot', return_value=browser_report),
              patch.object(monitor, 'write_report') as write_report,
              patch.object(monitor, 'Telegram') as telegram,
              patch('builtins.print')):
            code = monitor.main()
        self.assertNotIn('chrono24', collect.call_args.args[0]['sources'])
        telegram.assert_not_called()
        return code, write_report.call_args.args[0]

    def test_browser_hints_merged_without_duplicate_http_fetch(self):
        code, report = self.run_main(browser.analyze(snapshot(detail(403))))
        self.assertEqual(code, 0)
        self.assertEqual(len(report['hints']), 1)
        self.assertEqual(report['matches'], [])
        self.assertEqual(report['coverage'][0]['source'], 'chrono24')

    def test_browser_error_does_not_disable_other_sources(self):
        data = snapshot(); data['search']['http_status'] = 403
        code, report = self.run_main(browser.analyze(data))
        self.assertEqual(code, 0)
        self.assertEqual(report['hints'], [])
        self.assertEqual(report['coverage'][0]['status'], 'error')


if __name__ == '__main__':
    unittest.main()
