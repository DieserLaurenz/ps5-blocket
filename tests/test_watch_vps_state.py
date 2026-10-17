import json
from pathlib import Path
import tempfile
import unittest

from monitor import ServiceError
from watch_monitor import WatchLocalStore, empty_state, prepare_state


class MigratedStateTests(unittest.TestCase):
    def test_missing_or_empty_state_never_resets_notification_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with self.assertRaises(ServiceError):
                WatchLocalStore(path).load()
            path.write_text('{}', encoding='utf-8')
            with self.assertRaises(ServiceError):
                WatchLocalStore(path).load()

    def test_migrated_recipient_and_history_are_preserved(self):
        state = empty_state()
        state.update(recipient='original', unverified_notified={'chrono24:123': 123456})
        with tempfile.TemporaryDirectory() as directory:
            store = WatchLocalStore(Path(directory) / 'state.json')
            store.save(state)
            loaded = prepare_state(store.load(), 'original')
            self.assertEqual(loaded, state)
            with self.assertRaises(ServiceError):
                prepare_state(store.load(), 'different')
