"""One scheduled search with durable Telegram deduplication. Standard library only."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import scraper


class ServiceError(Exception):
    pass


def request_json(url, *, data=None, headers=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method,
                                    headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Telegram URLs contain tokens. Never print raw exceptions or request URLs.
        raise ServiceError(f'API-Aufruf fehlgeschlagen (HTTP {exc.code}).') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ServiceError('API-Verbindung oder JSON-Antwort fehlgeschlagen.') from None


class Telegram:
    def __init__(self, token, chat_id):
        if not token or not chat_id:
            raise ServiceError('TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID fehlen. Setup-Telegram.cmd ausführen.')
        self.token, self.chat_id = token, str(chat_id)

    @property
    def recipient(self):
        return hashlib.sha256((self.token.split(':')[0] + ':' + self.chat_id).encode()).hexdigest()[:20]

    def send(self, text):
        result = request_json(f'https://api.telegram.org/bot{self.token}/sendMessage', data={
            'chat_id': self.chat_id, 'text': text,
            'link_preview_options': {'is_disabled': True},
        })
        if result.get('ok') is not True:
            raise ServiceError('Telegram hat die Nachricht nicht bestätigt.')


class LocalStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scraper.atomic_write(self.path, json.dumps(state, indent=2))


class GitHubStore:
    """Persist only notification IDs/prices on a dedicated branch, never credentials."""
    def __init__(self, repo, token):
        if not repo or not token:
            raise ServiceError('GITHUB_REPOSITORY oder GITHUB_TOKEN fehlt.')
        self.base = f'https://api.github.com/repos/{repo}'
        self.headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'PS5-Blocket-Monitor'}
        self.sha = None

    def load(self):
        # Branch and file are provisioned at deployment. Missing state is an error,
        # not a first run: this avoids silently resending every alert after data loss.
        data = request_json(self.base + '/contents/state.json?ref=monitor-state', headers=self.headers)
        self.sha = data['sha']
        return json.loads(base64.b64decode(data['content']))

    def save(self, state):
        if not self.sha:
            raise ServiceError('Statusdatei wurde nicht geladen.')
        result = request_json(self.base + '/contents/state.json', headers=self.headers, method='PUT', data={
            'message': 'Update monitor notification state', 'branch': 'monitor-state', 'sha': self.sha,
            'content': base64.b64encode(json.dumps(state, indent=2).encode()).decode(),
        })
        self.sha = result['content']['sha']


def prepare_state(state, recipient):
    if state and state.get('version') != 1:
        raise ServiceError('Unbekanntes Statusformat; keine Benachrichtigungen gesendet.')
    if not state or state.get('recipient') != recipient:
        return {'version': 1, 'recipient': recipient, 'notified': {}}
    if not isinstance(state.get('notified'), dict):
        raise ServiceError('Beschädigte Benachrichtigungshistorie.')
    return state


def message_for(row, old_price=None):
    label = 'Neue passende PS5' if old_price is None else f'PS5 günstiger: −{old_price - row["price"]:g} SEK'
    delivery = 'Versand möglich' if row['shipping'] else 'Abholung in Göteborg'
    return (f'{label}\n\n{row["title"][:300]}\n'
            f'{row["price"]:g} SEK · {row["location"]}\n{delivery}\n'
            'Preis ggf. zzgl. Versand/Käuferschutz. Zustand und Lieferumfang prüfen.\n\n' + row['url'])


def notify_rows(rows, state, store, telegram, now):
    sent = 0
    for row in rows:
        previous = state['notified'].get(row['id'])
        old_price = previous['price'] if previous else None
        # Only a new low since the last alert, not every temporary up/down fluctuation.
        if old_price is not None and row['price'] >= old_price:
            continue
        telegram.send(message_for(row, old_price))
        state['notified'][row['id']] = {'price': row['price'], 'sent_at': now}
        store.save(state)  # Persist each confirmed send, also before a later send fails.
        sent += 1
        time.sleep(1)
    return sent


def execute(config, store, telegram, dry_run=False):
    state = prepare_state(store.load(), telegram.recipient) if not dry_run else {}
    client = scraper.Client(config['request_delay'])
    docs, urls, warnings = scraper.collect(config, client)
    rows, excluded = scraper.select(docs, config, client)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    result = {'at': now, 'scanned': len(docs), 'matches': len(rows), 'pages': len(urls),
              'excluded': len(excluded), 'warnings': warnings, 'sent': 0}
    if dry_run:
        result['preview'] = [message_for(row) for row in rows]
    else:
        result['sent'] = notify_rows(rows, state, store, telegram, now)
        # A daily health message confirms continued operation even without any deals.
        day = now[:10]
        if state.get('last_health_day') != day:
            qualifier = 'Suche unvollständig: Seitenlimit erreicht.' if warnings else 'Suche abgeschlossen.'
            telegram.send(f'PS5-Suche aktiv · {len(rows)} passende Angebote bis {config["max_price"]} SEK.\n'
                          f'{qualifier}\nZeitplan: alle 5 Minuten (GitHub kann Läufe verzögern).\n'
                          'Versand in Schweden oder Abholung Göteborg. Nächste Statusmeldung morgen.')
            state['last_health_day'] = day
            state['last_health_at'] = now
            store.save(state)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description='Ein Suchlauf mit Telegram; für Cron und GitHub Actions.')
    parser.add_argument('--dry-run', action='store_true', help='Live suchen ohne Nachrichten oder Statusänderung')
    parser.add_argument('--config', type=Path, default=scraper.ROOT / 'config.json')
    parser.add_argument('--state', type=Path, default=scraper.ROOT / 'output' / 'notifications.json')
    parser.add_argument('--test-telegram', action='store_true')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        telegram = None if args.dry_run else Telegram(os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID'))
        if args.test_telegram:
            if telegram is None:
                raise ServiceError('--dry-run und --test-telegram sind nicht kombinierbar.')
            telegram.send('Test erfolgreich: Dein PS5-Suchdienst kann dir Telegram-Nachrichten senden.')
            print('Telegram-Testnachricht bestätigt.')
            return 0
        store = (GitHubStore(os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_TOKEN'))
                 if os.environ.get('GITHUB_ACTIONS') == 'true' and not args.dry_run else LocalStore(args.state))
        execute(config, store, telegram, args.dry_run)
        return 0
    except (ServiceError, scraper.ScrapeError, ValueError, OSError, KeyError, TypeError) as exc:
        # ServiceError and ScrapeError are sanitized; other failures contain local data only.
        print(f'Monitor fehlgeschlagen: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
