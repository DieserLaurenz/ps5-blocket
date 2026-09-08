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
        raise ServiceError(f'API request failed (HTTP {exc.code}).') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ServiceError('API connection or JSON response failed.') from None


class Telegram:
    def __init__(self, token, chat_id):
        if not token or not chat_id:
            raise ServiceError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are missing. Run Setup-Telegram.cmd.')
        self.token, self.chat_id = token, str(chat_id)
        self.delivered_photos = 0

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
            raise ServiceError('Telegram did not confirm the message.')
        return (result.get('result') or {}).get('message_id')

    def send_photos(self, urls, caption, reply_to=None):
        """Send one photo or an album, with a short caption and a reply to the full alert."""
        if not 1 <= len(urls) <= 10:
            raise ValueError('A Telegram photo batch must contain 1 to 10 images.')
        data = {'chat_id': self.chat_id, 'disable_notification': True}
        if reply_to is not None:
            data['reply_parameters'] = {'message_id': reply_to, 'allow_sending_without_reply': True}
        if len(urls) == 1:
            method = 'sendPhoto'
            data.update(photo=urls[0], caption=caption, parse_mode='HTML')
        else:
            method = 'sendMediaGroup'
            data['media'] = [{'type': 'photo', 'media': url,
                              **({'caption': caption, 'parse_mode': 'HTML'} if i == 0 else {})}
                             for i, url in enumerate(urls)]
        result = request_json(f'https://api.telegram.org/bot{self.token}/{method}', data=data)
        if result.get('ok') is not True:
            raise ServiceError('Telegram did not confirm the photos.')
        self.delivered_photos += len(urls)


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
            raise ServiceError('GITHUB_REPOSITORY or GITHUB_TOKEN is missing.')
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
            raise ServiceError('State file has not been loaded.')
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
        raise ServiceError('Unknown state format; no notifications sent.')
    if not state or state.get('recipient') != recipient:
        return {'version': 1, 'recipient': recipient, 'notified': {}}
    if not isinstance(state.get('notified'), dict):
        raise ServiceError('Corrupted notification history.')
    return state


def message_for(row, old_price=None, assessment_update=False, preview=False):
    """Trusted HTML layout; seller/model text is always bounded then escaped."""
    label = ('👀 Format preview · current listing' if preview else
             '🧠 PS5 · Assessment updated' if assessment_update else
             '🎮 New PS5 match' if old_price is None else f'📉 PS5 price drop: −{old_price - row["price"]:g} SEK')
    analysis = row.get('analysis') or {}
    inferred_generation, inferred_edition = evaluator.variant(row['title'])
    generation = analysis.get('generation', inferred_generation)
    edition = analysis.get('edition', inferred_edition)
    model = {'original': 'PS5 Original', 'slim': 'PS5 Slim', 'pro': 'PS5 Pro'}.get(generation, 'PS5 · Version unknown')
    model += ' · ' + {'disc': 'Disc', 'digital': 'Digital'}.get(edition, 'Edition unknown')
    condition = {'new': 'New according to seller', 'used': 'Used according to seller',
                 'faulty': 'Faulty according to seller'}.get(analysis.get('condition'), 'Condition unknown')
    delivery = '🚚 <b>Shipping available</b>' if row['shipping'] else '🤝 <b>Pickup in Göteborg</b>'
    # Construct the link from the listing ID, not seller/LLM-provided markup or URLs.
    identifier = str(row['id'])
    if not identifier.isascii() or not identifier.isdecimal():
        raise ValueError('Invalid Blocket listing ID')
    price = f'{row["price"]:,g}'
    rating = row.get('seller_rating')
    if rating:
        scale = f'/{rating["best"]:g}' if rating.get('best') is not None else ' (scale not listed)'
        reputation = f'⭐ <b>Seller rating: {rating["score"]:g}{scale}</b> · {rating["count"]} reviews on Blocket'
    else:
        reputation = '⭐ Seller rating: unavailable publicly' if row.get('photos_checked') else '⭐ Seller rating: not checked'
    photos = scraper.photo_urls(row.get('photos') or [row.get('image')], row['id'])
    photo_note = (f'📷 {len(photos)} listing photos' if row.get('photos_checked') else '📷 Cover photo only · gallery not checked') if photos else '📷 No public listing photos found'
    return (f'<b>{label}</b>\n\n<b>{escape(row["title"][:300])}</b>\n'
            f'💰 <b>{price} SEK</b>\n'
            f'🕹 {model}\n🔎 {condition}\n'
            f'📍 {escape(row["location"][:100])}\n{delivery}\n'
            f'{reputation}\n{photo_note}\n'
            '<i>Shipping and buyer protection may cost extra.</i>'
            + evaluator.assessment_text(row)
            + evaluator.seller_message_text(row)
            + f'\n\n🔗 <a href="https://www.blocket.se/recommerce/forsale/item/{identifier}">View listing on Blocket</a>')


def load_alert_extras(row, client):
    if client is not None and not row.get('photos_checked'):
        extras = scraper.parse_listing_extras(client.get(row['url']), row['id'])
        extras['photos'] = extras['photos'] or row.get('photos', [])
        row.update(extras)


def deliver_photos(row, record, telegram, save):
    """Resume only unconfirmed batches; media failures never roll back the text alert."""
    urls = scraper.photo_urls(record.get('photo_urls', []), row['id'])
    if record.get('photo_failures', 0) >= 3 or record.get('photo_retry_after', 0) > time.time():
        return
    while record.get('photos_sent', 0) < len(urls):
        offset = record.get('photos_sent', 0)
        batch = urls[offset:offset + 10]
        caption = (f'📷 <b>{escape(row["title"][:200])}</b> · photos {offset + 1}–{offset + len(batch)}/{len(urls)}\n'
                   f'https://www.blocket.se/recommerce/forsale/item/{row["id"]}')
        try:
            telegram.send_photos(batch, caption, record.get('message_id'))
        except ServiceError:
            record['photo_failures'] = record.get('photo_failures', 0) + 1
            record['photo_retry_after'] = time.time() + 3600
            save()
            print(f'Photo delivery failed for listing {row["id"]}; text alert preserved. '
                  'At most three attempts, one hour apart.', file=sys.stderr)
            return
        record['photos_sent'] = offset + len(batch)
        record['photo_failures'] = 0
        record.pop('photo_retry_after', None)
        save()
        time.sleep(1)


def notify_rows(rows, state, store, telegram, now, client=None):
    sent = 0
    for row in rows:
        previous = state['notified'].get(row['id'])
        old_price = previous['price'] if previous else None
        # Only a new low since the last alert, not every temporary up/down fluctuation.
        assessment_update = bool(row.get('assessment_id') and previous
                                 and previous.get('assessment_id') != row['assessment_id'])
        if old_price is not None and row['price'] >= old_price and not assessment_update:
            deliver_photos(row, previous, telegram, lambda: store.save(state))
            continue
        try:
            load_alert_extras(row, client)
        except scraper.ScrapeError:
            # Do not repeat optional requests after a Blocket access/network failure.
            client = None
            print('Optional gallery lookup failed; using available search metadata.', file=sys.stderr)
        message_id = telegram.send(message_for(row, old_price, assessment_update and row['price'] >= old_price), html=True)
        state['notified'][row['id']] = {'price': min(row['price'], old_price) if old_price is not None else row['price'],
                                      'sent_at': now, 'assessment_id': row.get('assessment_id'),
                                      'message_id': message_id,
                                      'photo_urls': scraper.photo_urls(row.get('photos') or [row.get('image')], row['id']),
                                      'photos_sent': 0}
        store.save(state)  # Persist each confirmed send, also before a later send fails.
        deliver_photos(row, state['notified'][row['id']], telegram, lambda: store.save(state))
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
            load_alert_extras(rows[0], client)
            message_id = telegram.send(message_for(rows[0], preview=True), html=True)
            record = {'message_id': message_id, 'photo_urls': rows[0].get('photos', [])}
            deliver_photos(rows[0], record, telegram, lambda: None)
            result['sent'] = 1
        result['format_preview'] = True
    else:
        result['sent'] = notify_rows(rows, state, store, telegram, now, client)
        # A daily health message confirms continued operation even without any deals.
        day = now[:10]
        if state.get('last_health_day') != day:
            qualifier = 'Search incomplete: page limit reached.' if warnings else 'Search completed.'
            telegram.send(f'✅ <b>PS5 search active</b>\n\n'
                          f'🎮 <b>{len(rows)} matching listings</b> up to {config["max_price"]:g} SEK\n'
                          f'{qualifier}\n\n⏱ Scheduled every 5 minutes (GitHub delays are possible).\n'
                          '🚚 Shipping within Sweden · 🤝 Pickup in Göteborg\n'
                          '<i>Next status update tomorrow.</i>', html=True)
            state['last_health_day'] = day
            state['last_health_at'] = now
            store.save(state)
    result['photos_sent'] = getattr(telegram, 'delivered_photos', 0)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description='One search with Telegram alerts; for cron and GitHub Actions.')
    parser.add_argument('--dry-run', action='store_true', help='Live search without messages or state changes')
    parser.add_argument('--config', type=Path, default=scraper.ROOT / 'config.json')
    parser.add_argument('--state', type=Path, default=scraper.ROOT / 'output' / 'notifications.json')
    parser.add_argument('--test-telegram', action='store_true')
    parser.add_argument('--preview-format', action='store_true', help='Send one current listing as a Telegram format preview')
    parser.add_argument('--diagnose-ai', action='store_true', help='List available Gemini models without generating content')
    args = parser.parse_args()
    try:
        if args.diagnose_ai:
            evaluator.diagnose_models(os.environ.get('GEMINI_API_KEY', ''))
            return 0
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        telegram = None if args.dry_run else Telegram(os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID'))
        if args.test_telegram:
            if telegram is None:
                raise ServiceError('--dry-run and --test-telegram cannot be combined.')
            telegram.send('Test successful: your PS5 monitor can send you Telegram messages.')
            print('Telegram test message confirmed.')
            return 0
        store = (GitHubStore(os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_TOKEN'))
                 if os.environ.get('GITHUB_ACTIONS') == 'true' and not args.dry_run else LocalStore(args.state))
        execute(config, store, telegram, args.dry_run, args.preview_format)
        return 0
    except (ServiceError, scraper.ScrapeError, evaluator.AIError, ValueError, OSError, KeyError, TypeError) as exc:
        # ServiceError and ScrapeError are sanitized; other failures contain local data only.
        print(f'Monitor failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
