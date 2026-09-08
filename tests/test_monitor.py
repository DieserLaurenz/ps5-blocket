import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor as m


def row(price=3000, identifier='123'):
    return {'id': identifier, 'title': 'PS5 Digital', 'price': price, 'location': 'Göteborg',
            'shipping': False, 'url': f'https://www.blocket.se/recommerce/forsale/item/{identifier}'}


class Store:
    def __init__(self):
        self.states = []

    def save(self, state):
        self.states.append(copy.deepcopy(state))


class Telegram:
    def __init__(self, fail_after=None):
        self.messages = []
        self.fail_after = fail_after

    def send(self, text):
        if self.fail_after is not None and len(self.messages) >= self.fail_after:
            raise m.ServiceError('test failure')
        self.messages.append(text)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.store, self.telegram = Store(), Telegram()
        self.state = m.prepare_state({}, 'recipient')
        self.sleep = patch('monitor.time.sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def notify(self, rows):
        return m.notify_rows(rows, self.state, self.store, self.telegram, '2026-09-08T12:00:00Z')

    def test_new_then_unchanged_then_price_drop(self):
        self.assertEqual(self.notify([row()]), 1)
        self.assertEqual(self.notify([row()]), 0)
        self.assertEqual(self.notify([row(2500)]), 1)
        self.assertIn('500 SEK', self.telegram.messages[-1])

    def test_price_oscillations_do_not_spam(self):
        self.notify([row()])
        self.notify([row(3500)])
        self.notify([row()])
        self.assertEqual(len(self.telegram.messages), 1)

    def test_partial_failure_persists_only_confirmed_sends(self):
        self.telegram.fail_after = 1
        with self.assertRaises(m.ServiceError):
            self.notify([row(), row(identifier='456')])
        self.assertIn('123', self.store.states[-1]['notified'])
        self.assertNotIn('456', self.store.states[-1]['notified'])

    def test_failed_send_can_be_retried(self):
        self.telegram.fail_after = 0
        with self.assertRaises(m.ServiceError):
            self.notify([row()])
        self.assertFalse(self.state['notified'])
        self.telegram.fail_after = None
        self.assertEqual(self.notify([row()]), 1)

    def test_recipient_change_starts_fresh(self):
        self.notify([row()])
        self.assertFalse(m.prepare_state(self.state, 'different')['notified'])

    def test_unknown_state_does_not_silently_reset(self):
        with self.assertRaises(m.ServiceError):
            m.prepare_state({'version': 99}, 'recipient')

    def test_local_store_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            m.LocalStore(path).save(self.state)
            self.assertEqual(m.LocalStore(path).load(), self.state)

    def test_token_not_in_network_error(self):
        import urllib.error
        token = '123:secret'
        error = urllib.error.URLError('https://api.telegram.org/bot' + token)
        with patch('monitor.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(m.ServiceError) as caught:
                m.Telegram(token, '456').send('test')
        self.assertNotIn(token, str(caught.exception))

    def test_github_state_load_failure_stops_before_search(self):
        with patch('monitor.request_json', side_effect=m.ServiceError('404')):
            with self.assertRaises(m.ServiceError):
                m.GitHubStore('owner/repo', 'test').load()

    def test_no_telegram_parse_mode(self):
        with patch('monitor.request_json', return_value={'ok': True}) as request:
            m.Telegram('123:test', '456').send('<b>untrusted title</b>')
        self.assertNotIn('parse_mode', request.call_args.kwargs['data'])


if __name__ == '__main__':
    unittest.main()
