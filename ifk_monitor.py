"""Hourly IFK men's home-ticket alerts from the official club site. Stdlib only."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from monitor import LocalStore, ServiceError, Telegram, request_json

SITE = 'https://ifkgoteborg.se'
SCHEDULE = SITE + '/spelschema/'
STATE_BRANCH = 'ifk-state'


def clean(value):
    return ' '.join(value.replace('\xad', '').split())


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        if self.tag in ('script', 'style', 'svg'):
            return ''
        return clean(' '.join(c.text() if isinstance(c, Node) else c for c in self.children))

    def find(self, *, tag=None, cls=None):
        result = []
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get('class', '').split()):
                    result.append(child)
                result.extend(child.find(tag=tag, cls=cls))
        return result


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def text_html(source):
    return Document(source).root.text()


def safe_url(value, *, club=False):
    parts = urlsplit(urljoin(SITE, value))
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        return ''
    if club and parts.hostname not in ('ifkgoteborg.se', 'www.ifkgoteborg.se'):
        return ''
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))


def fetch(url, data=None):
    request = Request(url, data=urlencode(data).encode() if data else None,
                      headers={'User-Agent': 'IFK-Ticket-Monitor/1.0 (hourly public club news)',
                               **({'Content-Type': 'application/x-www-form-urlencoded'} if data else {})})
    try:
        with urlopen(request, timeout=35) as response:
            raw = response.read(15_000_001)
            if len(raw) > 15_000_000:
                raise ServiceError('IFK response exceeds size limit.')
            return raw.decode('utf-8-sig'), response.headers
    except HTTPError as exc:
        raise ServiceError(f'IFK source failed (HTTP {exc.code}).') from None
    except (URLError, TimeoutError, OSError, UnicodeError):
        raise ServiceError('IFK source connection or encoding failed.') from None


def parse_fixtures(source, year, today):
    root = Document(source).root
    if not root.find(cls='matches__list'):
        raise ServiceError('Unrecognized IFK fixture response; history preserved.')
    rows = []
    for match in root.find(tag='li', cls='match'):
        teams = match.find(cls='teams')
        names = teams[0].find(tag='span') if teams else []
        timing = match.find(cls='date-type')
        dates = timing[0].find(cls='title') if timing else []
        competitions = timing[0].find(cls='sub-title') if timing else []
        if len(names) != 2 or not dates or not competitions:
            raise ServiceError('IFK fixture markup changed; history preserved.')
        home, away = (n.text() for n in names)
        when, competition = dates[0].text(), competitions[0].text()
        parts = re.search(r'\b(\d{1,2})/(\d{1,2})\b', when)
        if not parts:
            raise ServiceError('IFK fixture date missing; history preserved.')
        try:
            match_date = date(year, int(parts[2]), int(parts[1]))
        except ValueError:
            raise ServiceError('Invalid IFK fixture date.') from None
        if match_date < today or home.casefold() != 'ifk göteborg':
            continue
        if re.search(r'\b(dam|elitettan|f19|f17|p19|p17|u21)\b', competition, re.I):
            continue
        links = [safe_url(a.attrs.get('href', '')) for a in match.find(tag='a')
                 if re.search(r'köp.*biljett', a.text(), re.I) and a.attrs.get('href')]
        arena = match.find(cls='team-arena')
        venue = arena[0].find(cls='sub-title') if arena else []
        rows.append({'id': f'{year}:{competition}:{home}:{away}', 'title': f'{home} – {away}',
                     'when': when, 'date': match_date.isoformat(), 'competition': competition,
                     'venue': venue[0].text() if venue else '', 'url': next((u for u in links if u), '')})
    return rows


def get_fixtures(today):
    # Discover available men's seasons from the actual selector (including next year).
    source, _ = fetch(SCHEDULE)
    selectors = [n for n in Document(source).root.find(tag='select') if n.attrs.get('id') == 'match-schedule']
    if len(selectors) != 1:
        raise ServiceError('IFK season selector missing.')
    years = [int(n.attrs['value']) for n in selectors[0].find(tag='option')
             if re.fullmatch(r'\d{4}', n.attrs.get('value', '')) and n.text().startswith('Herr ')
             and today.year <= int(n.attrs['value']) <= today.year + 1]
    if not years:
        raise ServiceError('No current men\'s season published; history preserved.')
    rows = []
    for year in sorted(set(years)):
        raw, _ = fetch(SITE + '/wp-admin/admin-ajax.php', {'action': 'ifk_match_schedule', 'match_type': year})
        html = json.loads(raw)
        if not isinstance(html, str):
            raise ServiceError('Invalid fixture response.')
        rows.extend(parse_fixtures(html, year, today))
    return rows


def parse_news(posts, fixtures):
    if not isinstance(posts, list):
        raise ServiceError('Invalid IFK news response.')
    result = []
    for post in posts:
        title = text_html(post['title']['rendered'])
        body = text_html(post['content']['rendered'])
        lower = (title + ' ' + body).casefold()
        # Require a ticket release/info headline, not ordinary previews mentioning tickets.
        if not re.search(r'biljettsläpp|biljettinfo|biljett.*släpp|släpp.*biljett', title, re.I):
            continue
        if re.search(r'\bborta\w*\b', title, re.I):
            continue
        mens = bool(re.search(r'\b(allsvenskan|herr\w*|europakval\w*|conference)\b|gamla ullevi', lower))
        women = bool(re.search(r'\b(dam\w*|elitettan|valhalla|f19|f17|p19|p17)\b', lower))
        home = bool(re.search(r'\bhemma\w*\b|gamla ullevi', lower))
        fixture_match = any(f['title'].split(' – ', 1)[1].casefold() in lower for f in fixtures)
        if not home or (women and not mens) or not (mens or fixture_match or 1 in post.get('categories', [])):
            continue
        link = safe_url(post['link'], club=True)
        if not link:
            raise ServiceError('Invalid official news link.')
        # Content hash catches announced general-sale dates added to an existing post.
        fingerprint = digest(title + '\n' + body)
        marker = re.search(r'allmänt biljettsläpp|allmän försäljning', body, re.I)
        excerpt = body[marker.start():marker.start() + 1100] if marker else body[:1100]
        result.append({'id': str(post['id']), 'title': title, 'url': link,
                       'fingerprint': fingerprint, 'excerpt': excerpt,
                       'published': post['date_gmt'], 'modified': post['modified_gmt']})
    return result


def get_news(fixtures, now):
    posts = []
    page = 1
    while True:
        query = urlencode({'search': 'biljett', 'per_page': 100, 'page': page,
                           'orderby': 'modified', 'order': 'desc',
                           'modified_after': (now - timedelta(days=120)).strftime('%Y-%m-%dT%H:%M:%S'),
                           '_fields': 'id,date_gmt,modified_gmt,link,title,content,categories'})
        raw, headers = fetch(SITE + '/wp-json/wp/v2/posts?' + query)
        batch = json.loads(raw)
        if not isinstance(batch, list):
            raise ServiceError('Invalid IFK news response.')
        posts.extend(batch)
        pages = int(headers.get('X-WP-TotalPages', '0'))
        if not pages and batch:
            raise ServiceError('IFK news pagination metadata missing.')
        if pages > 10:
            raise ServiceError('IFK news exceeds pagination limit; history preserved.')
        if page >= pages:
            break
        page += 1
    return parse_news(posts, fixtures)


class GitHubStore:
    def __init__(self, repo, token):
        if not repo or not token:
            raise ServiceError('GitHub state credentials missing.')
        self.base = f'https://api.github.com/repos/{repo}/contents/state.json'
        self.headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'IFK-Ticket-Monitor'}
        self.sha = None

    def load(self):
        data = request_json(self.base + '?ref=' + STATE_BRANCH, headers=self.headers)
        self.sha = data['sha']
        return json.loads(base64.b64decode(data['content']))

    def save(self, state):
        if not self.sha:
            raise ServiceError('IFK state has not been loaded.')
        result = request_json(self.base, method='PUT', headers=self.headers, data={
            'message': 'Update IFK ticket notification state', 'branch': STATE_BRANCH, 'sha': self.sha,
            'content': base64.b64encode(json.dumps(state, indent=2).encode()).decode()})
        self.sha = result['content']['sha']


def prepare_state(state, recipient):
    if not state:
        return {'version': 1, 'recipient': recipient, 'initialized': False,
                'tickets': {}, 'news': {}, 'errors': {}}
    if state.get('version') != 1 or not isinstance(state.get('initialized'), bool):
        raise ServiceError('Invalid IFK state; no notifications sent.')
    if any(not isinstance(state.get(k), dict) for k in ('tickets', 'news', 'errors')):
        raise ServiceError('Corrupt IFK history; no notifications sent.')
    if any(not isinstance(v, list) or any(not isinstance(h, str) for h in v)
           for k in ('tickets', 'news') for v in state[k].values()):
        raise ServiceError('Corrupt IFK notification fingerprints.')
    if state.get('recipient') != recipient:
        raise ServiceError('IFK Telegram recipient changed; initialize a new state explicitly.')
    return state


def ticket_key(row):
    return digest(row['id'])


def ticket_message(row):
    return ('⚽ <b>IFK Göteborg · Neuer Ticketlink (Herren)</b>\n\n'
            f'<b>{escape(row["title"][:250])}</b>\n'
            f'📅 {escape(row["when"])} · {escape(row["competition"])}\n'
            f'📍 {escape(row["venue"])}\n\n'
            'Die offizielle IFK-Seite verlinkt jetzt Tickets für dieses Heimspiel. '
            'Verfügbarkeit und Vorverkaufsbedingungen bitte im Shop prüfen.\n\n'
            f'<a href="{escape(row["url"], quote=True)}">Tickets öffnen</a> · '
            f'<a href="{SCHEDULE}">IFK-Spielplan</a>')


def news_message(row, updated=False):
    label = 'Aktualisierte Ticketinfo' if updated else 'Neue Ticketankündigung'
    return (f'🔵⚪ <b>IFK Göteborg · {label} (Herren)</b>\n\n'
            f'<b>{escape(row["title"][:250])}</b>\n\n'
            f'{escape(row["excerpt"])}\n\n'
            'Originalauszug auf Schwedisch; eine Ankündigung kann einen späteren '
            'Verkaufsstart oder einen Vorverkauf betreffen.\n\n'
            f'<a href="{escape(row["url"], quote=True)}">Offizielle Ticketinfo lesen</a>')


def process(fixtures, news, state, store, telegram, now):
    sent = 0
    if not state['initialized']:
        state['tickets'] = {ticket_key(r): [digest(r['url'])] for r in fixtures if r['url']}
        state['news'] = {r['id']: [r['fingerprint']] for r in news}
        state['initialized'] = True
        # Persist the baseline before sending startup confirmation: never flood old posts.
        state['started_at'] = now.isoformat()
        store.save(state)
    if not state.get('welcome_sent'):
        telegram.send('⚽ <b>IFK-Ticketbot eingerichtet</b>\n\n'
                      'Ich prüfe stündlich die offizielle IFK-Seite auf neue Ticketlinks '
                      'für Herren-Heimspiele und neue oder aktualisierte Verkaufsankündigungen.\n\n'
                      f'Ausgangsstand: {len(fixtures)} kommende Heimspiele, '
                      f'{sum(bool(r["url"]) for r in fixtures)} Ticketlinks. '
                      'Bereits vorhandene Meldungen sind gespeichert.\n\n'
                      'Quelle ist ifkgoteborg.se; die gesperrte Ticketshop-Liste wird nicht überwacht.', html=True)
        state['welcome_sent'] = True
        store.save(state)
        sent += 1
    for row in fixtures:
        if not row['url']:
            continue
        key, fingerprint = ticket_key(row), digest(row['url'])
        if fingerprint in state['tickets'].get(key, []):
            continue
        telegram.send(ticket_message(row), html=True)
        state['tickets'].setdefault(key, []).append(fingerprint)
        store.save(state)
        sent += 1
    for row in sorted(news, key=lambda r: (r['modified'], r['id'])):
        old = state['news'].get(row['id'], [])
        if row['fingerprint'] in old:
            continue
        telegram.send(news_message(row, updated=bool(old)), html=True)
        state['news'].setdefault(row['id'], []).append(row['fingerprint'])
        store.save(state)
        sent += 1
    if state['errors']:
        telegram.send('✅ IFK-Ticketbot: Die offizielle Seite ist wieder erreichbar. Die Prüfung war erfolgreich.')
    state['errors'] = {}
    state['last_success'] = now.isoformat()
    store.save(state)
    return sent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Fetch and parse without messages or state changes')
    parser.add_argument('--state', default=str(Path(__file__).parent / 'output' / 'ifk-notifications.json'))
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    state = store = telegram = None
    try:
        if not args.dry_run:
            telegram = Telegram(os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID'))
            store = (GitHubStore(os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_TOKEN'))
                     if os.environ.get('GITHUB_ACTIONS') == 'true' else LocalStore(args.state))
            state = prepare_state(store.load(), telegram.recipient)
        fixtures = get_fixtures(now.date())
        news = get_news(fixtures, now)
        print(json.dumps({'home_matches': len(fixtures), 'ticket_links': sum(bool(r['url']) for r in fixtures),
                          'ticket_announcements': len(news), 'matches': fixtures,
                          'news': [{k: r[k] for k in ('id', 'title', 'url')} for r in news]}, ensure_ascii=True))
        if args.dry_run:
            return 0
        sent = process(fixtures, news, state, store, telegram, now)
        print(f'IFK check successful; {sent} messages sent.')
        return 0
    except (ServiceError, ValueError, KeyError, TypeError) as exc:
        # Never print raw HTTP or Telegram exceptions (may include credentials).
        message = str(exc) if isinstance(exc, ServiceError) else 'IFK source or state format changed.'
        print(message, file=sys.stderr)
        if state is not None and store and telegram:
            try:
                day = now.date().isoformat()
                if state['errors'].get('alert_day') != day:
                    telegram.send('⚠️ IFK-Ticketbot: Die Prüfung ist fehlgeschlagen. '
                                  'Neue Ticketmeldungen können sich verzögern. Der nächste Versuch erfolgt stündlich.\n'
                                  + message)
                    state['errors'] = {'alert_day': day}
                    store.save(state)
            except ServiceError:
                print('Could not send/persist IFK failure alert.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
