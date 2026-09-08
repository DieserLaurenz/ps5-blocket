"""Public Blocket search -> local PS5 deals. Python 3.10+, no dependencies."""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEARCH = 'https://www.blocket.se/recommerce/forsale/search'


class ScrapeError(Exception):
    pass


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.current = (dict(attrs), [])

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            attrs, chunks = self.current
            self.scripts.append((attrs, ''.join(chunks)))
            self.current = None


def scripts(source):
    parser = ScriptParser()
    parser.feed(source)
    return parser.scripts


def parse_search(source):
    for attrs, raw in scripts(source):
        if 'data-react-query-state' not in attrs:
            continue
        try:
            data = json.loads(raw) if raw.lstrip().startswith('{') else json.loads(base64.b64decode(raw))
            for query in data.get('queries', []):
                keys = query.get('queryKey', [])
                if not any(isinstance(k, dict) and k.get('scope') == 'search' for k in keys):
                    continue
                value = query.get('state', {}).get('data', {})
                if isinstance(value, dict) and isinstance(value.get('docs'), list) and 'metadata' in value:
                    return value['docs'], value['metadata']
        except (ValueError, TypeError) as exc:
            raise ScrapeError('Blocket-Suchdaten sind nicht lesbar.') from exc
    raise ScrapeError('Keine Suchdaten gefunden: Seitenformat geändert oder Zugriff blockiert. Kein leeres Ergebnis gespeichert.')


def walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def parse_detail(source):
    for attrs, raw in scripts(source):
        if attrs.get('type') != 'application/ld+json':
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for obj in walk_json(data):
            if obj.get('@type') == 'Product':
                offer = obj.get('offers', {})
                if isinstance(offer, list):
                    offer = offer[0] if offer else {}
                return {
                    'description': html.unescape(re.sub('<[^>]+>', ' ', obj.get('description', ''))),
                    'availability': offer.get('availability', ''),
                }
    raise ScrapeError('Anzeigenbeschreibung nicht gefunden')


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))


PS5 = re.compile(r'\b(?:ps\s*5|play\s*station\s*5)\b', re.I)
ACCESSORY = re.compile(
    r'\b(?:portal|remote player|psvr\w*|vr\s*2?|dualsense|dual\s+sense|handkontroll\w*|'
    r'kontroll\w*|kontrol\w*|mediakontroll|controller\w*|dosa|\w*headset|horlurar|\w*kamera|camera|'
    r'sidepanel\w*|sidopanel\w*|cover\w*|skal|fodral|\w*stall|stand|stativ|\w*hallare|vaggfaste|mount|button\w*|trigger\w*|adapter|dock\w*|ladd\w*|charging|kabel\w*|cable\w*|'
    r'ratt\w*|gamingratt|racing\w*|wheel|pedal\w*|riffmaster|nacon|scuf|'
    r'ssd|harddisk|skivlasare|disc drive|disk drive|flakt\w*|steelseries|arctis|hyperx|wd black|spel|spelpaket|'
    r'games?|fifa|battlefield|star wars|spider.man|stray|quarry)\b', re.I)
BUNDLE = re.compile(r'\b(?:med|with|utan|without|inkl\w*)\b|[+&]', re.I)
FAULT = re.compile(r'\b(?:defekt\w*|trasig\w*|reparationsobjekt|reservdel\w*|'
                   r'broken|faulty|bannad\w*|banned|startar inte|fungerar inte|'
                   r'funkar inte|overhett\w*|hdmi.problem|problem med|reparation\w*)\b', re.I)
TRADE = re.compile(r'\b(?:bytes|byte mot|sokes|kop\s*es|wanted|hyr\w*|uthyr\w*)\b', re.I)


def title_reason(title):
    text = normalized(title)
    model = PS5.search(text)
    if not model:
        return 'Keine PS5 im Titel'
    if TRADE.search(text):
        return 'Tausch, Gesuch oder Vermietung'
    if FAULT.search(text):
        return 'Defekt oder Reparatur im Titel'
    if re.search(r'\b(?:ps\s*[1-4]|playstation\s*[1-4])\b', text):
        return 'Mehrere PlayStation-Generationen im Titel; Konsole nicht eindeutig'
    accessories = list(ACCESSORY.finditer(text))
    if accessories:
        first = accessories[0]
        # Console + controller/game bundles are useful, standalone accessories are not.
        console_before = model.end() <= first.start()
        bridge = text[model.end():first.start()] if console_before else ''
        if not console_before or not BUNDLE.search(bridge) or re.search(r'\bps\s*[1-4]\b', bridge):
            return 'Zubehör oder Spiel im Titel'
        return ''
    # Positive console evidence prevents unknown game names from passing a blacklist.
    if re.search(r'\b(?:spelkonsol|konsol|console)\b', text):
        return ''
    prefix = re.sub(r'[^\w\s]', ' ', text[:model.start()]).strip()
    plain_prefix = re.fullmatch(r'(?:(?:sony|saljer|saljes|prissankt|ny|nytt|nypris)\s*)*', prefix)
    if plain_prefix and re.search(r'\b(?:slim|digital|disc|disk|pro|\d+\s*(?:gb|tb))\b', text[model.end():]):
        return ''
    remaining = re.sub(r'[^\w\s]', ' ', PS5.sub('', text)).strip()
    if re.fullmatch(r'(?:(?:sony|saljer|saljes|ny|nytt|ooppnad|obruten|vit|svart|nyskick|fint|skick|'
                    r'bra|billig|billigt|prissankt|pris|sankt|original|edition|standard|fat)\s*)*', remaining):
        return ''
    return 'Keine eindeutige Konsolenbezeichnung (möglicherweise Spiel oder Zubehör)'


def fault_in_description(text):
    for match in FAULT.finditer(text):
        prefix = text[max(0, match.start() - 30):match.start()]
        if not re.search(r'\b(inga|ingen|inget|utan|no|not)\s+(?:kanda\s+)?$', prefix):
            return match
    return None


def delivery(doc, cities):
    flags = doc.get('flags', [])
    shipping = 'shipping_exists' in flags
    location = str(doc.get('location', ''))
    pickup = any(normalized(location).strip() == normalized(city).strip() for city in cities)
    return shipping, pickup


def listing(doc, config, require_delivery=True):
    reason = title_reason(str(doc.get('heading', '')))
    price = doc.get('price') or {}
    amount = price.get('amount')
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        reason = reason or 'Kein positiver Festpreis'
    elif price.get('currency_code') != 'SEK' or amount > config['max_price']:
        reason = reason or 'Außerhalb der Preisgrenze oder andere Währung'
    if doc.get('trade_type') not in (None, 'Säljes'):
        reason = reason or 'Kein Verkaufsangebot'
    shipping, pickup = delivery(doc, config['pickup_cities'])
    if require_delivery and not shipping and not pickup:
        reason = reason or 'Kein ausgewiesener Versand und keine Abholung in Göteborg'
    if reason:
        return None, reason
    identifier = str(doc.get('id', ''))
    if not identifier.isdigit():
        return None, 'Ungültige Anzeigen-ID'
    title = doc['heading']
    model = 'Pro' if re.search(r'\bpro\b', title, re.I) else 'Slim' if re.search(r'\bslim\b', title, re.I) else 'PS5'
    model += ' Digital' if 'digital' in title.casefold() else ' Disc' if re.search(r'\b(disc|disk|skiva)\b', title, re.I) else ''
    return {
        'id': identifier, 'title': title, 'price': amount, 'currency': 'SEK',
        'location': str(doc.get('location', '')), 'shipping': shipping, 'pickup': pickup,
        'free_shipping': shipping and 'seller_pays_shipping' in doc.get('flags', []),
        'shipping_source': 'Blocket-Versandmarkierung' if shipping else '',
        'url': f'https://www.blocket.se/recommerce/forsale/item/{identifier}',
        'image': (doc.get('image') or {}).get('url', ''), 'model': model,
        'description': '', 'detail_checked': False, 'notes': [],
    }, ''


class Client:
    def __init__(self, delay):
        self.delay = delay
        self.last_request = 0

    def get(self, url):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = urllib.request.Request(url, headers={
            'User-Agent': 'PS5-DealFinder/1.0 (personal public listing search)',
            'Accept-Language': 'sv-SE,sv;q=0.9',
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            raise ScrapeError(f'Blocket HTTP {exc.code}; Abruf abgebrochen (keine automatischen Wiederholungen).') from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ScrapeError(f'Netzwerkfehler: {exc}') from exc


def search_url(config, query, page):
    params = {'q': query, 'sort': 'PRICE_ASC', 'price_to': config['max_price'], 'page': page}
    if not config['all_categories']:
        params['product_category'] = '2.93.3905.63'
    return SEARCH + '?' + urllib.parse.urlencode(params)


def collect(config, client):
    docs, searches, warnings = {}, [], []
    for query in config['queries']:
        previous_ids = set()
        for page in range(1, config['max_pages'] + 1):
            url = search_url(config, query, page)
            print(f'Suche {query!r}, Seite {page} ...', flush=True)
            batch, metadata = parse_search(client.get(url))
            paging = metadata.get('paging', {})
            if int(paging.get('current', page)) != page:
                raise ScrapeError('Blocket liefert eine unerwartete Ergebnisseite.')
            searches.append(url)
            ids = {str(d.get('id')) for d in batch}
            if ids and ids <= previous_ids:
                raise ScrapeError('Blocket wiederholt dieselbe Seite; Ergebnis wäre unvollständig.')
            previous_ids.update(ids)
            for doc in batch:
                docs[str(doc.get('id'))] = doc
            last = paging.get('last')
            if not batch or metadata.get('is_end_of_paging') or (last is not None and page >= int(last)):
                break
            if page == config['max_pages']:
                warnings.append(f'Seitenlimit für {query} erreicht; Suche unvollständig.')
    return list(docs.values()), searches, warnings


def select(docs, config, client=None):
    rows, excluded = [], []
    for doc in docs:
        row, reason = listing(doc, config, require_delivery=client is None)
        if row:
            rows.append(row)
        else:
            excluded.append({'id': doc.get('id'), 'title': doc.get('heading'), 'reason': reason})
    rows.sort(key=lambda r: (r['price'], r['id']))
    kept = []
    for index, row in enumerate(rows):
        if client and index < config['detail_limit']:
            print(f'Prüfe Beschreibung: {row["title"]}', flush=True)
            # Network/access errors propagate to stop, rather than repeatedly hitting a block.
            source = client.get(row['url'])
            try:
                detail = parse_detail(source)
            except ScrapeError:
                row['notes'].append('Beschreibung nicht automatisch lesbar')
            else:
                row['description'] = detail['description']
                row['detail_checked'] = bool(detail['description'])
                if any(status in detail['availability'].lower() for status in ('soldout', 'outofstock', 'discontinued')):
                    excluded.append({'id': row['id'], 'title': row['title'], 'reason': 'Nicht mehr verfügbar'})
                    continue
                description = normalized(row['description'])
                fault = fault_in_description(description)
                if fault:
                    excluded.append({'id': row['id'], 'title': row['title'], 'reason': 'Möglicher Defekt in Beschreibung: ' + fault.group()})
                    continue
                if re.search(r'\b(?:kan (?:aven )?skickas|skickas garna|frakt (?:ar )?mojligt|frakt erbjuds|can (?:be )?ship(?:ped)?)\b', description):
                    if not row['shipping']:
                        row['shipping'] = True
                        row['shipping_source'] = 'Versand laut Beschreibung; Konditionen mit Verkäufer klären'
                if re.search(r'\b(skickar inte|skickas inte|endast avhamtning|endast upphamtning|only pickup|no shipping)\b', description):
                    row['shipping'] = False
                    row['free_shipping'] = False
                    if not row['pickup']:
                        excluded.append({'id': row['id'], 'title': row['title'], 'reason': 'Beschreibung schließt Versand aus'})
                        continue
                    row['notes'].append('Laut Beschreibung nur Abholung')
        if not row['shipping'] and not row['pickup']:
            excluded.append({'id': row['id'], 'title': row['title'], 'reason': 'Kein bestätigter Versand und keine Abholung in Göteborg'})
            continue
        if not row['detail_checked']:
            row['notes'].append('Nur Titel und Suchdaten geprüft')
        if row['price'] < 1500:
            row['notes'].append('Sehr niedriger Preis: Lieferumfang genau prüfen')
        kept.append(row)
    return kept, excluded


def update_history(rows, previous, now):
    history = dict(previous)
    for row in rows:
        old = previous.get(row['id'])
        row['new'] = old is None
        row['price_drop'] = max(0, old['price'] - row['price']) if old else 0
        row['first_seen'] = old['first_seen'] if old else now
        history[row['id']] = {'price': row['price'], 'first_seen': row['first_seen'], 'last_seen': now}
    return history


def atomic_write(path, text, encoding='utf-8'):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text, encoding=encoding)
    temp.replace(path)


def render(report):
    template = (ROOT / 'dashboard.html').read_text(encoding='utf-8')
    # Escape '<' so third-party listing content cannot terminate the data script.
    payload = json.dumps(report, ensure_ascii=True).replace('<', '\\u003c')
    return template.replace('__REPORT_JSON__', payload)


def save_report(output, report, history=None):
    output.mkdir(parents=True, exist_ok=True)
    atomic_write(output / 'angebote.json', json.dumps(report, ensure_ascii=False, indent=2))
    fields = ['title', 'price', 'currency', 'location', 'model', 'shipping', 'pickup', 'url', 'new', 'price_drop']
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore', delimiter=';')
    writer.writeheader()
    for row in report['listings']:
        safe = {key: ("'" + val if isinstance(val, str) and val.startswith(('=', '+', '-', '@', '\t', '\r')) else val) for key, val in row.items()}
        writer.writerow(safe)
    atomic_write(output / 'angebote.csv', buffer.getvalue(), 'utf-8-sig')
    atomic_write(output / 'angebote.html', render(report))
    if history is not None:
        atomic_write(output / 'history.json', json.dumps(history, ensure_ascii=False, indent=2))


def run(config, output, imports=None, cached=False):
    client = Client(config['request_delay'])
    source_time = None
    if cached:
        cache = json.loads((output / 'last-search.json').read_text(encoding='utf-8'))
        docs, searches, warnings = cache['docs'], cache['searches'], list(cache['warnings'])
        source_time = cache['fetched_at']
        warnings.append('Suchdaten aus lokalem Cache; Beschreibungen erneut abgefragt.')
    elif imports:
        docs = {}
        for path in imports:
            batch, _ = parse_search(Path(path).read_text(encoding='utf-8'))
            docs.update({str(d.get('id')): d for d in batch})
        docs, searches, warnings = list(docs.values()), [], ['HTML-Import: Zeitpunkt der Angebote entspricht den gespeicherten Seiten; keine Live-Abfrage.']
    else:
        docs, searches, warnings = collect(config, client)
        output.mkdir(parents=True, exist_ok=True)
        atomic_write(output / 'last-search.json', json.dumps({'fetched_at': datetime.now(timezone.utc).isoformat(), 'docs': docs, 'searches': searches, 'warnings': warnings}, ensure_ascii=False))
    rows, excluded = select(docs, config, None if imports else client)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    history_path = output / 'history.json'
    previous = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else {}
    history = update_history(rows, previous, now)
    report = {'generated_at': now, 'search_fetched_at': source_time or now,
              'mode': 'import' if imports else 'cached' if cached else 'live', 'config': config,
              'scanned': len(docs), 'listings': rows, 'excluded': excluded,
              'searches': searches, 'warnings': warnings}
    save_report(output, report, None if imports else history)
    print(f'\n{len(rows)} passende Angebote aus {len(docs)} Anzeigen. Preis ohne Versand/Käuferschutz.')
    for row in rows[:15]:
        print(f'{row["price"]:>5g} SEK | {row["location"]} | {row["title"]}\n  {row["url"]}')
    for warning in warnings:
        print('Hinweis: ' + warning)
    print(f'\nÜbersicht: {output / "angebote.html"}')
    return report


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
    parser = argparse.ArgumentParser(description='Günstige PS5: Versand in Schweden oder Abholung Göteborg.')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--max-price', type=int, help='Maximalpreis in SEK')
    parser.add_argument('--all-categories', action='store_true', help='Auch falsch kategorisierte Konsolen suchen (mehr Seiten)')
    parser.add_argument('--output', type=Path, default=ROOT / 'output')
    parser.add_argument('--open', action='store_true', help='Ergebnis im Browser öffnen')
    parser.add_argument('--watch', type=int, metavar='SEKUNDEN', help='Wiederholt suchen, mindestens 600 Sekunden')
    parser.add_argument('--import-html', nargs='+', metavar='DATEI', help='Gespeicherte Suchseiten offline auswerten')
    parser.add_argument('--recheck-cache', action='store_true', help='Letzte Suchdaten neu filtern und Beschreibungen erneut abrufen')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        if args.max_price is not None:
            config['max_price'] = args.max_price
        if args.all_categories:
            config['all_categories'] = True
        for key in ('max_price', 'deal_price', 'max_pages', 'detail_limit'):
            if type(config[key]) is not int or config[key] < (0 if key == 'detail_limit' else 1):
                raise ValueError(f'{key} muss eine gültige ganze Zahl sein')
        if config['request_delay'] < 1:
            raise ValueError('request_delay muss mindestens 1 Sekunde sein')
        for key in ('queries', 'pickup_cities'):
            if not isinstance(config[key], list) or not config[key] or not all(isinstance(x, str) and x.strip() for x in config[key]):
                raise ValueError(f'{key} muss eine nichtleere Liste von Texten sein')
        if args.watch is not None and args.watch < 600:
            raise ValueError('--watch muss mindestens 600 Sekunden sein')
        if args.watch and args.import_html:
            raise ValueError('--watch und --import-html sind nicht kombinierbar')
        if args.recheck_cache and (args.watch or args.import_html):
            raise ValueError('--recheck-cache ist nicht mit --watch oder --import-html kombinierbar')
        while True:
            report = run(config, args.output.resolve(), args.import_html, args.recheck_cache)
            if args.open:
                webbrowser.open((args.output.resolve() / 'angebote.html').as_uri())
                args.open = False
            if not args.watch:
                return 0
            if any(row['new'] or row['price_drop'] for row in report['listings']):
                print('\aNeue Treffer oder Preissenkungen!', flush=True)
            print(f'Nächste Suche in {args.watch} Sekunden. Ende mit Strg+C.', flush=True)
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print('\nBeendet.')
        return 0
    except (ScrapeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'FEHLER: {exc}\nVorhandene Ergebnisse bleiben erhalten und sind möglicherweise veraltet.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
