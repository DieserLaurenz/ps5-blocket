"""Public discovery sources. Hints never qualify as verified Sweden-price offers."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from ifk_monitor import Document
from monitor import ServiceError
from scraper import atomic_write
import watch_sources as sources

URLS = {
    'montredo': 'https://www.montredo.com/products/hamilton-jazzmaster-automatic-blue-38mm-2-48900?country=SE',
    'prisjakt': 'https://www.prisjakt.nu/produkt.php?p=13967687',
    'klocksnack': 'https://klocksnack.se/forums/handla-s%C3%A4ljes-bytes-k%C3%B6pes.11/',
    'everywatch': 'https://everywatch.com/hamilton/jazzmaster/h36215140',
}
SOURCE_NAMES = tuple(URLS)
MAX_BYTES = 20_000_000


def hint_id(source, url):
    clean = sources.safe_url(source, url)
    path = urlsplit(clean).path
    patterns = {
        'montredo': r'/products/hamilton-jazzmaster-automatic-blue-38mm-2-(48900)',
        'prisjakt': r'/go-to-shop/(\d+)/offer/(\d+)',
        'klocksnack': r'/threads/[^/]+\.(\d+)/',
        'everywatch': r'/hamilton/jazzmaster/h36215140/watch-(\d+)',
    }
    match = re.fullmatch(patterns[source], path)
    if not match:
        raise ValueError('Unexpected discovery URL')
    return source + ':' + '-'.join(match.groups())


def hint(source, url, text, price=None, currency=None):
    row = {'id': hint_id(source, url), 'source': source, 'reference': sources.REFERENCE,
           'url': sources.safe_url(source, url), 'search_text': text[:650]}
    if source == 'montredo':
        row['url'] += '?country=SE'
    elif source == 'prisjakt':
        row['url'] += '?client_id=1551'
    if price is not None and sources.money(price) > 0 and currency in ('SEK', 'EUR', 'USD'):
        row.update(native_price=sources.money(price), native_currency=currency)
    return row


def montredo(html):
    doc = Document(html).root
    if sources.js_assignment(html, 'Shopify.country') != 'SE':
        raise ValueError('Montredo country context missing')
    refs = [n.text() for n in doc.find(cls='reference-block')]
    if refs != [sources.REFERENCE]:
        raise ValueError('Montredo main reference not confirmed')
    product = [p for value in sources.script_json(doc, script_type='application/ld+json')
               for p in sources.products(value)]
    if len(product) != 1 or product[0].get('sku') != '48900':
        raise ValueError('Montredo product missing')
    offer = product[0].get('offers', {})
    if sources.safe_url('montredo', offer.get('url', '')) != sources.safe_url('montredo', URLS['montredo']):
        raise ValueError('Montredo offer URL mismatch')
    if str(offer.get('availability', '')).rsplit('/', 1)[-1] == 'OutOfStock':
        return []
    if str(offer.get('availability', '')).rsplit('/', 1)[-1] not in ('InStock', 'PreOrder', 'BackOrder'):
        raise ValueError('Montredo availability unknown')
    if not any('Add to cart' in b.text() and 'disabled' not in b.attrs for b in doc.find(tag='button')):
        raise ValueError('Montredo purchase control missing')
    prices = {p.text() for p in doc.find(tag='sale-price')}
    if len(prices) != 1 or offer.get('priceCurrency') != 'EUR':
        raise ValueError('Montredo price format changed')
    visible = re.fullmatch(r'Sale price\s*€([\d,]+(?:\.\d{2})?)', prices.pop())
    if not visible:
        raise ValueError('Montredo visible price missing')
    price = sources.money(visible[1].replace(',', ''))
    if abs(price - sources.money(offer.get('price'))) > 2:
        raise ValueError('Montredo price conflict')
    return [hint('montredo', URLS['montredo'],
                 'Hamilton H36215140 · Montredo · Shop-Lieferland Schweden. '
                 'Euro-Produktpreis; Versandkosten und Checkout-Endpreis offen.', price, 'EUR')]


def prisjakt(html):
    doc = Document(html).root
    if not any(re.search(r'\bH36215140\b', n.text()) for n in doc.find(tag='h1')):
        raise ValueError('Prisjakt main product missing')
    rows = {}
    for a in doc.find(tag='a'):
        if a.attrs.get('data-test') != 'OfferClickoutButton' or not re.search(r'\bH36215140\b', a.text()):
            continue
        if not re.search(r'\bI lager\b', a.text()):
            continue
        seller = next((n.attrs.get('alt', '') for n in a.find(tag='img')), '')
        price = re.search(r'(?<![\w.,])\d[\d\s\xa0\u202f]*(?:[,.]\d{1,2})?\s*(?:SEK|kr)\b', a.text())
        if not price:
            raise ValueError('Prisjakt merchant price missing')
        row = hint('prisjakt', a.attrs['href'], f'{seller} · {a.text()}', sources.sek(price[0]), 'SEK')
        rows[row['id']] = row
    if not rows and not re.search(r'Inga (?:butiker|priser)|0 butiker', doc.text(), re.I):
        raise ValueError('Prisjakt offer list missing')
    return list(rows.values())


def forum_candidates(html):
    doc = Document(html).root
    if not any('Handla - Säljes, Bytes, Köpes' in n.text() for n in doc.find(tag='h1')):
        raise ValueError('Klocksnack sale forum missing')
    titles = doc.find(cls='structItem-title')
    if not titles:
        raise ValueError('Klocksnack thread list missing')
    links = []
    for title in titles:
        prefixes = [n.text() for n in title.find(cls='labelLink')]
        if not prefixes or not all(p in ('Säljes', 'Säljes/Bytes') for p in prefixes):
            continue
        for a in title.find(tag='a'):
            if a.attrs.get('data-tp-primary') == 'on' and re.search(r'\bHamilton\b|\bH36215140\b', a.text(), re.I):
                url = sources.safe_url('klocksnack', a.attrs['href'])
                hint_id('klocksnack', url)
                links.append(url)
    next_page = next((sources.safe_url('klocksnack', a.attrs['href'])
                      for a in doc.find(cls='pageNav-jump--next')), None)
    if next_page and not re.fullmatch(re.escape(URLS['klocksnack']) + r'page-\d+', next_page):
        raise ValueError('Unexpected forum pagination')
    return list(dict.fromkeys(links)), next_page, len(titles)


def forum_offer(html, url):
    doc = Document(html).root
    headings, posts = doc.find(tag='h1'), doc.find(cls='message-body')
    if len(headings) != 1 or not posts:
        raise ValueError('Forum thread missing')
    title, body = headings[0].text(), posts[0].text()
    if not re.match(r'Säljes(?:/Bytes)?\b', title):
        return []
    if not re.search(r'\bH36215140\b', body, re.I):
        return []
    if re.search(r'\b(såld|sålt|avslutad|sold|withdrawn|defekt|replica|replika)\b', title + ' ' + body, re.I):
        return []
    # A price in a reply/quote can refer to a different item. Leave price unparsed.
    return [hint('klocksnack', url, title + ' · Referenz H36215140 im ersten Beitrag. '
                 'Preis, Zustand und Versand bitte im Inserat prüfen.')]


def everywatch(html):
    doc = Document(html).root
    if 'Only Collectors Beyond This Point' in doc.text():
        raise ServiceError('EveryWatch-Zugriffsprüfung; keine lesbare Angebotsliste')
    if not any(n.text() == 'Hamilton Jazzmaster H36215140' for n in doc.find(tag='h1')):
        raise ValueError('EveryWatch reference page missing')
    active = [n for n in doc.find(tag='button', cls='active') if re.fullmatch(r'For Sale \(\d+\)', n.text())]
    if len(active) != 1:
        raise ValueError('EveryWatch live For Sale tab not confirmed')
    found = int(re.search(r'\d+', active[0].text())[0])
    rows = []
    cards = doc.find(tag='a', cls='ew-grid-watch-card')
    for a in cards:
        if 'Only with premium access' in a.text():
            continue  # Never read locked records from embedded application state.
        if not any(n.text() == sources.REFERENCE for n in a.find(cls='refno')):
            continue
        if re.search(r'\bSold\b|Removed|Auction', a.text(), re.I):
            continue
        price = a.find(cls='ew-watch-price')
        match = re.search(r'Price\s+([\d,]+(?:\.\d{2})?)\s+(USD|EUR|SEK)\b', price[0].text()) if price else None
        if not match:
            raise ValueError('EveryWatch visible card price missing')
        rows.append(hint('everywatch', a.attrs['href'], a.text(), match[1].replace(',', ''), match[2]))
    if found and not rows:
        raise ValueError('EveryWatch public cards missing')
    return rows, found


def new_snapshot():
    return {'version': 1, 'reference': sources.REFERENCE, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'pages': {s: [] for s in SOURCE_NAMES}}


def capture(output, browser_fallback=True):
    snapshot = new_snapshot()
    def save():
        atomic_write(output, json.dumps(snapshot, ensure_ascii=True))
    for source, url in URLS.items():
        client = sources.Client()
        def fetch(target):
            page = {'url': target}
            snapshot['pages'][source].append(page)
            try:
                page['html'] = client.get(target)
            except (ServiceError, ValueError) as exc:
                page['error'] = str(exc) if isinstance(exc, ServiceError) else 'Ungültige Antwort'
            save()
            return page.get('html', '')
        try:
            html = fetch(url)
            if source == 'klocksnack' and html:
                candidates = []
                for index in range(3):
                    links, next_url, _ = forum_candidates(html)
                    candidates.extend(links)
                    if not next_url or index == 2:
                        break
                    html = fetch(next_url)
                for target in list(dict.fromkeys(candidates))[:5]:
                    fetch(target)
            if source == 'everywatch' and browser_fallback:
                try:
                    everywatch(html)
                except (ServiceError, ValueError):
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=False,
                            executable_path=os.environ.get('WATCH_CHROME_BINARY') or None,
                            chromium_sandbox=bool(os.environ.get('WATCH_CHROME_BINARY')))
                        try:
                            page = browser.new_page(locale='en-US', viewport={'width': 1440, 'height': 1000})
                            response = page.goto(url, wait_until='domcontentloaded', timeout=35000)
                            if not response or response.status != 200 or page.url != url:
                                raise ValueError('EveryWatch browser navigation failed')
                            active = page.locator('button.tab-btn.active').filter(has_text=re.compile(r'^For Sale \(\d+\)$'))
                            active.wait_for(timeout=20000)
                            if active.inner_text().strip() != 'For Sale (0)':
                                page.locator('.ew-grid-watch-card').first.wait_for(timeout=20000)
                            # DOM only: do not inspect hidden paid records or internal APIs.
                            rendered = page.content()
                            if len(rendered.encode()) > 8_000_000:
                                raise ValueError('EveryWatch page too large')
                            everywatch(rendered)
                            snapshot['pages'][source] = [{'url': url, 'html': rendered, 'backend': 'playwright/chrome'}]
                        finally:
                            browser.close()
        except Exception as exc:
            snapshot['pages'][source].append({'url': url, 'error': type(exc).__name__})
        finally:
            client.close()
            save()
    return snapshot


def analyze(snapshot, selected=SOURCE_NAMES):
    result = {'hints': [], 'coverage': []}
    for source in selected:
        status = {'source': source, 'status': 'partial', 'checked': 0, 'found': 0,
                  'mode': 'discovery', 'search_readable': False, 'errors': [], 'note': ''}
        rows = []
        try:
            pages = snapshot.get('pages', {}).get(source, [])
            if not pages or pages[0].get('url') != URLS[source]:
                raise ValueError('Source snapshot missing')
            if any(p.get('error') for p in pages):
                status['errors'].extend(p['error'] for p in pages if p.get('error'))
            first = pages[0].get('html', '')
            if source == 'montredo':
                rows = montredo(first)
                status.update(checked=1, found=1, note='Euro-Shoppreis; Schweden-Endpreis unbestätigt')
            elif source == 'prisjakt':
                rows = prisjakt(first)
                status.update(checked=len(rows), found=len(rows), note='Händlerhinweise; Preise noch nicht beim Händler verifiziert')
            elif source == 'everywatch':
                rows, found = everywatch(first)
                status.update(checked=len(rows), found=found, note='Nur öffentlich sichtbare Angebote; Premium-Treffer ausgeschlossen')
                status['backend'] = pages[0].get('backend', 'curl_cffi')
            else:
                candidates, scanned, page_count = [], 0, 0
                expected = URLS[source]
                for page in pages:
                    if not page.get('html') or not page['url'].startswith(URLS[source]):
                        continue
                    if page['url'] != expected or page_count >= 3:
                        raise ValueError('Unexpected forum page sequence')
                    links, expected, count = forum_candidates(page['html'])
                    candidates.extend(links)
                    scanned += count
                    page_count += 1
                status['found'] = len(set(candidates))
                for url in dict.fromkeys(candidates):
                    detail = next((p for p in pages if p['url'] == url and p.get('html')), None)
                    if detail:
                        rows.extend(forum_offer(detail['html'], url))
                        status['checked'] += 1
                status['note'] = f'{page_count} aktuelle Forumsseiten / {scanned} Themen; kein vollständiges Archiv'
                if status['found'] > status['checked']:
                    status['errors'].append('Nicht alle passenden Forumsbeiträge geprüft')
            status['search_readable'] = True
        except (ServiceError, ValueError, KeyError, TypeError, AttributeError):
            status['status'] = 'error'
            status['errors'].append('Keine verifizierbare öffentliche Ergebnisliste')
            rows = []
        result['hints'].extend(rows)
        result['coverage'].append(status)
    return result


def load_snapshot(path, now, selected=SOURCE_NAMES):
    try:
        path = Path(path)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('Snapshot too large')
        data = json.loads(path.read_text(encoding='utf-8'))
        age = (now - datetime.fromisoformat(data['checked_at'])).total_seconds()
        if data.get('version') != 1 or data.get('reference') != sources.REFERENCE or not -60 <= age <= 1800:
            raise ValueError('Stale snapshot')
        return analyze(data, selected)
    except (OSError, ValueError, KeyError, TypeError):
        return analyze({}, selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/discovery.json'))
    parser.add_argument('--http-only', action='store_true')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    data = capture(args.output, not args.http_only)
    result = analyze(data)
    print(json.dumps({'coverage': result['coverage'], 'hints': len(result['hints'])}, ensure_ascii=True))
    return 1 if all(c['status'] == 'error' for c in result['coverage']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
