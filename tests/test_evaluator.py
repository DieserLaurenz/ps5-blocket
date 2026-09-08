import copy
import json
import unittest
import urllib.error
from unittest.mock import patch

import evaluator as e
import monitor as m
import scraper


NOW = 1788876000
CONFIG = {'assessment': {'enabled': True, 'max_calls_per_run': 3, 'max_calls_per_day': 20}}


def row(identifier='123', price=3000):
    return {'id': identifier, 'title': 'PS5 Slim Disc', 'description': 'Fungerar bra med två kontroller.',
            'price': price, 'shipping': True, 'location': 'Stockholm', 'url': 'https://www.blocket.se/recommerce/forsale/item/' + identifier}


def analysis():
    return {'generation': 'slim', 'edition': 'disc', 'condition': 'used', 'summary': 'Laut Anzeige funktionsfähig.',
            'included': ['Zwei Controller'], 'positives': [], 'warnings': ['Rechnung nicht erwähnt'],
            'questions': ['Ist die Rechnung vorhanden?'], 'confidence': 'medium'}


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.state = {'market': {'attempted_at': NOW, 'fetched_at': NOW, 'samples': []}}
        self.saved = []

    def enrich(self, rows, key='test', dry_run=False):
        e.enrich(rows, CONFIG, self.state, lambda s: self.saved.append(copy.deepcopy(s)), None,
                 api_key=key, now=NOW, dry_run=dry_run)

    def test_cached_analysis_and_price_only_change(self):
        with patch('evaluator.generate', return_value=analysis()) as generate:
            first = row()
            self.enrich([first])
            second = row(price=2500)
            self.enrich([second])
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(first['assessment_id'], second['assessment_id'])
        self.assertEqual(second['ai_status'], 'cached')

    def test_changed_description_is_reanalyzed(self):
        with patch('evaluator.generate', return_value=analysis()) as generate:
            self.enrich([row()])
            changed = row()
            changed['description'] += ' Kvitto finns.'
            self.enrich([changed])
        self.assertEqual(generate.call_count, 2)

    def test_budget_and_no_dropped_rows(self):
        rows = [row(str(i)) for i in range(8)]
        with patch('evaluator.generate', return_value=analysis()) as generate:
            self.enrich(rows)
        self.assertEqual(generate.call_count, 3)
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum('analysis' in r for r in rows), 3)
        self.assertEqual(self.saved[0]['ai_budget']['calls'], 1)

    def test_daily_budget_survives_runs(self):
        with patch('evaluator.generate', return_value=analysis()) as generate:
            for i in range(21):
                self.enrich([row(str(i))])
        self.assertEqual(generate.call_count, 20)

    def test_rate_limit_has_persistent_cooldown(self):
        rows = [row(), row('456')]
        with patch('evaluator.generate', side_effect=e.AIError('429')) as generate:
            self.enrich(rows)
            self.enrich([row('789')])
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(len(rows), 2)
        self.assertGreater(self.state['ai_budget']['blocked_until'], NOW)

    def test_missing_key_and_dry_run_do_not_call_ai(self):
        with patch('evaluator.generate') as generate:
            self.enrich([row()], key='')
            self.enrich([row()], dry_run=True)
        generate.assert_not_called()

    def test_schema_rejects_malformed_response(self):
        invalid = analysis()
        invalid['warnings'] = 'everything is fine'
        with self.assertRaises(ValueError):
            e.validate_analysis(invalid)
        with self.assertRaises(ValueError):
            e.validate_analysis({'summary': 'trust me'})

    def test_output_and_contact_data_bounded(self):
        value = analysis()
        value['summary'] = 'Contact https://evil.invalid or foo@example.com ' + 'x' * 5000
        cleaned = e.validate_analysis(value)
        self.assertLessEqual(len(cleaned['summary']), 180)
        self.assertNotIn('evil.invalid', cleaned['summary'])
        self.assertNotIn('foo@example.com', cleaned['summary'])

    def test_api_request_contains_no_price_or_tools(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(analysis())}]}}]}).encode()
        with patch('evaluator.urllib.request.urlopen', return_value=Response()) as call:
            e.generate(row(), 'secret-test')
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        self.assertNotIn('tools', payload)
        self.assertNotIn('price', json.loads(payload['contents'][0]['parts'][0]['text']))
        self.assertNotIn('secret-test', request.full_url)

    def test_comparison_excludes_self_and_other_models(self):
        market = {'fetched_at': NOW, 'samples': [{'id': str(i), 'price': 4000, 'generation': 'slim', 'edition': 'disc'} for i in range(5)]}
        market['samples'] += [{'id': '123', 'price': 3000, 'generation': 'slim', 'edition': 'disc'},
                              {'id': 'pro', 'price': 9000, 'generation': 'pro', 'edition': 'disc'}]
        comparison = e.compare_price(row(), market, NOW)
        self.assertEqual(comparison['median'], 4000)
        self.assertEqual(comparison['count'], 5)
        self.assertEqual(comparison['percent'], -25)

    def test_insufficient_unknown_stale_and_truncated_samples(self):
        market = {'fetched_at': NOW, 'samples': []}
        self.assertNotIn('median', e.compare_price(row(), market, NOW))
        generic = row()
        generic['title'] = 'PS5'
        self.assertIn('Version', e.compare_price(generic, market, NOW)['label'])
        self.assertNotIn('median', e.compare_price(row(), market, NOW + 86401))
        self.assertIn('unvollständig', e.compare_price(row(), {**market, 'truncated': True}, NOW)['label'])

    def test_reference_failure_retains_previous_sample(self):
        state = {'market': {'attempted_at': NOW - 100000, 'fetched_at': NOW - 1000, 'samples': []}}
        with patch('scraper.collect', side_effect=scraper.ScrapeError('503')):
            result = e.refresh_market({}, state, None, NOW)
        self.assertEqual(result['fetched_at'], NOW - 1000)
        self.assertTrue(result['refresh_failed'])

    def test_reference_search_extends_above_notification_budget(self):
        config = {'max_price': 4000, 'assessment': {'market_max_price': 10000}}
        with patch('scraper.collect', return_value=([], [], [])) as collect:
            e.refresh_market(config, {}, None, NOW)
        self.assertEqual(collect.call_args.args[0]['max_price'], 10000)
        self.assertEqual(config['max_price'], 4000)

    def test_assessment_update_does_not_reset_lowest_price(self):
        from test_monitor import Store, Telegram
        state = {'notified': {'123': {'price': 2500}}}
        enriched = row(price=3000)
        enriched['assessment_id'], enriched['analysis'] = 'fingerprint', analysis()
        store, telegram = Store(), Telegram()
        with patch('monitor.time.sleep'):
            self.assertEqual(m.notify_rows([enriched], state, store, telegram, 'now'), 1)
            self.assertEqual(m.notify_rows([enriched], state, store, telegram, 'now'), 0)
        self.assertEqual(state['notified']['123']['price'], 2500)
        self.assertIn('Bewertung aktualisiert', telegram.messages[0])
        self.assertNotIn('günstiger', telegram.messages[0])

    def test_unchanged_github_state_does_not_create_commit(self):
        store = m.GitHubStore('owner/repo', 'test')
        store.sha, store.document = 'sha', json.dumps({'version': 1}, indent=2)
        with patch('monitor.request_json') as request:
            store.save({'version': 1})
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
