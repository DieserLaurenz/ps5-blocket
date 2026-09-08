"""One scheduled search with durable Telegram deduplication. Standard library only."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
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
import evaluator


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

    def send(self, text, *, html=False):
        result = request_json(f'https://api.telegram.org/bot{self.token}/sendMessage', data={
            'chat_id': self.chat_id, 'text': text,
            'link_preview_options': {'is_disabled': True},
            **({'parse_mode': 'HTML'} if html else {}),
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
        self.document = None

    def load(self):
        # Branch and file are provisioned at deployment. Missing state is an error,
        # not a first run: this avoids silently resending every alert after data loss.
        data = request_json(self.base + '/contents/state.json?ref=monitor-state', headers=self.headers)
        self.sha = data['sha']
        state = json.loads(base64.b64decode(data['content']))
        self.document = json.dumps(state, indent=2)
        return state

    def save(self, state):
        if not self.sha:
            raise ServiceError('Statusdatei wurde nicht geladen.')
        document = json.dumps(state, indent=2)
        if document == self.document:
            return
        result = request_json(self.base + '/contents/state.json', headers=self.headers, method='PUT', data={
            'message': 'Update monitor notification state', 'branch': 'monitor-state', 'sha': self.sha,
            'content': base64.b64encode(document.encode()).decode(),
        })
        self.sha = result['content']['sha']
        self.document = document


def prepare_state(state, recipient):
    if state and state.get('version') != 1:
        raise ServiceError('Unbekanntes Statusformat; keine Benachrichtigungen gesendet.')
    if not state or state.get('recipient') != recipient:
        return {'version': 1, 'recipient': recipient, 'notified': {}}
    if not isinstance(state.get('notified'), dict):
        raise ServiceError('Beschädigte Benachrichtigungshistorie.')
    return state


def message_for(row, old_price=None, assessment_update=False, preview=False):
    """Trusted HTML layout; seller/model text is always bounded then escaped."""
    label = ('👀 Formatvorschau · aktuelles Angebot' if preview else
             '🧠 PS5 · Bewertung aktualisiert' if assessment_update else
             '🎮 Neue passende PS5' if old_price is None else f'📉 PS5 günstiger: −{old_price - row["price"]:g} SEK')
    analysis = row.get('analysis') or {}
    inferred_generation, inferred_edition = evaluator.variant(row['title'])
    generation = analysis.get('generation', inferred_generation)
    edition = analysis.get('edition', inferred_edition)
    model = {'original': 'PS5 Original', 'slim': 'PS5 Slim', 'pro': 'PS5 Pro'}.get(generation, 'PS5 · Version unklar')
    model += ' · ' + {'disc': 'Disc', 'digital': 'Digital'}.get(edition, 'Edition unklar')
    condition = {'new': 'Neu laut Anzeige', 'used': 'Gebraucht laut Anzeige',
                 'faulty': 'Defekt laut Anzeige'}.get(analysis.get('condition'), 'Zustand unklar')
    delivery = '🚚 <b>Versand möglich</b>' if row['shipping'] else '🤝 <b>Abholung in Göteborg</b>'
    # Construct the link from the listing ID, not seller/LLM-provided markup or URLs.
    identifier = str(row['id'])
    if not identifier.isascii() or not identifier.isdecimal():
        raise ValueError('Ungültige Blocket-Anzeigen-ID')
    price = f'{row["price"]:,g}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return (f'<b>{label}</b>\n\n<b>{escape(row["title"][:300])}</b>\n'
            f'💰 <b>{price} SEK</b>\n'
            f'🕹 {model}\n🔎 {condition}\n'
            f'📍 {escape(row["location"][:100])}\n{delivery}\n'
            '<i>Ggf. zzgl. Versand und Käuferschutz.</i>'
            + evaluator.assessment_text(row)
            + f'\n\n🔗 <a href="https://www.blocket.se/recommerce/forsale/item/{identifier}">Anzeige auf Blocket öffnen</a>')


def notify_rows(rows, state, store, telegram, now):
    sent = 0
    for row in rows:
        previous = state['notified'].get(row['id'])
        old_price = previous['price'] if previous else None
        # Only a new low since the last alert, not every temporary up/down fluctuation.
        assessment_update = bool(row.get('assessment_id') and previous
                                 and previous.get('assessment_id') != row['assessment_id'])
        if old_price is not None and row['price'] >= old_price and not assessment_update:
            continue
        telegram.send(message_for(row, old_price, assessment_update and row['price'] >= old_price), html=True)
        state['notified'][row['id']] = {'price': min(row['price'], old_price) if old_price is not None else row['price'],
                                      'sent_at': now, 'assessment_id': row.get('assessment_id')}
        store.save(state)  # Persist each confirmed send, also before a later send fails.
        sent += 1
        time.sleep(1)
    return sent


def execute(config, store, telegram, dry_run=False, preview_format=False):
    state = prepare_state(store.load(), telegram.recipient) if not dry_run else {}
    client = scraper.Client(config['request_delay'])
    docs, urls, warnings = scraper.collect(config, client)
    rows, excluded = scraper.select(docs, config, client)
    # Assessment can add information, but never removes a qualifying listing.
    evaluator.enrich(rows, config, state, store.save, client, dry_run=dry_run)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    result = {'at': now, 'scanned': len(docs), 'matches': len(rows), 'pages': len(urls),
              'excluded': len(excluded), 'warnings': warnings, 'sent': 0,
              'ai': [row.get('ai_status', 'disabled') for row in rows]}
    if dry_run:
        result['preview'] = [message_for(row) for row in rows]
    elif preview_format:
        # A manual preview must not reset or consume the normal alert history.
        if rows:
            telegram.send(message_for(rows[0], preview=True), html=True)
            result['sent'] = 1
        result['format_preview'] = True
    else:
        result['sent'] = notify_rows(rows, state, store, telegram, now)
        # A daily health message confirms continued operation even without any deals.
        day = now[:10]
        if state.get('last_health_day') != day:
            qualifier = 'Suche unvollständig: Seitenlimit erreicht.' if warnings else 'Suche abgeschlossen.'
            telegram.send(f'✅ <b>PS5-Suche aktiv</b>\n\n'
                          f'🎮 <b>{len(rows)} passende Angebote</b> bis {config["max_price"]:g} SEK\n'
                          f'{qualifier}\n\n⏱ Zeitplan: alle 5 Minuten (Verzögerungen durch GitHub möglich).\n'
                          '🚚 Versand in Schweden · 🤝 Abholung Göteborg\n'
                          '<i>Nächste Statusmeldung morgen.</i>', html=True)
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
    parser.add_argument('--preview-format', action='store_true', help='Ein aktuelles Angebot als Telegram-Formatvorschau senden')
    parser.add_argument('--diagnose-ai', action='store_true', help='Verfügbare Gemini-Modelle auflisten, ohne Generierung')
    args = parser.parse_args()
    try:
        if args.diagnose_ai:
            evaluator.diagnose_models(os.environ.get('GEMINI_API_KEY', ''))
            return 0
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
        execute(config, store, telegram, args.dry_run, args.preview_format)
        return 0
    except (ServiceError, scraper.ScrapeError, evaluator.AIError, ValueError, OSError, KeyError, TypeError) as exc:
        # ServiceError and ScrapeError are sanitized; other failures contain local data only.
        print(f'Monitor fehlgeschlagen: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
