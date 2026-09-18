"""Read Chrono24 in an ordinary browser; keep unverified discoveries separate.

The browser step receives no Telegram/GitHub secrets. Its temporary HTML snapshot
is re-parsed by the notification step, never committed or uploaded as an artifact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from scraper import atomic_write
import watch_sources as sources

MAX_SNAPSHOT_BYTES = 32_000_000
DETAIL_LIMIT = 5


def listing_id(url):
    clean = sources.safe_url('chrono24', url)
    match = re.fullmatch(r'/hamilton/[^/]+--id(\d+)\.htm', urlsplit(clean).path)
    if not match or urlsplit(clean).port not in (None, 443):
        raise ValueError('Unexpected Chrono24 listing URL')
    return 'chrono24:' + match[1]


def search_candidates(html):
    doc = sources.Document(html).root
    headings = doc.find(tag='h1')
    if len(headings) != 1 or not re.fullmatch(r'Hamilton\s+H36215140', headings[0].text(), re.I):
        raise ValueError('Exact-reference search heading missing')
    # Only actual result cards, not recommendations, footer links or image links.
    cards = doc.find(tag='a', cls='wt-listing-item-link')
    candidates = {}
    for card in cards:
        text = card.text()
        if not text or len(text) > 2000:
            raise ValueError('Unrecognized result card')
        url = sources.safe_url('chrono24', card.attrs.get('href', ''))
        ident = listing_id(url)
        refs = set(re.findall(r'\bH\d{8}\b', text, re.I))
        if refs and {r.upper() for r in refs} != {sources.REFERENCE}:
            continue  # Promoted offer for a different reference.
        candidates[ident] = {'id': ident, 'source': 'chrono24', 'url': url,
                             'search_text': text[:350], 'reference': sources.REFERENCE}
    if not cards and not re.search(r'\b0 visa även annonser\b|inga annonser', doc.text(), re.I):
        raise ValueError('Search results missing; not a verified empty search')
    truncated = any('next' in a.attrs.get('rel', '').split() for a in doc.find(tag='a'))
    # Chrono24 may not mark its pagination with rel=next.
    truncated = truncated or any(re.search(r'[?&]pageNr=[2-9]\d*(?:&|$)', a.attrs.get('href', ''))
                                 for a in doc.find(tag='a'))
    return list(candidates.values()), truncated


def analyze(snapshot):
    """Re-validate public HTML; search-card prices never become verified totals."""
    coverage = {'source': 'chrono24', 'status': 'error', 'checked': 0, 'found': 0,
                'errors': [], 'mode': 'browser', 'search_readable': False}
    result = {'offers': [], 'hints': [], 'excluded': [], 'coverage': coverage}
    search = snapshot.get('search', {})
    if (search.get('url') != sources.SEARCHES['chrono24'] or
            search.get('final_url') != sources.SEARCHES['chrono24'] or search.get('http_status') != 200):
        coverage['errors'].append('Browser-Suche nicht erreichbar; keine neuen Hinweise')
        return result
    try:
        candidates, truncated = search_candidates(search['html'])
    except (ValueError, KeyError, TypeError):
        coverage['errors'].append('Browser-Suchformat nicht erkannt; kein leeres Ergebnis angenommen')
        return result
    coverage.update(found=len(candidates), search_readable=True)
    details = snapshot.get('details', [])
    if not isinstance(details, list) or len(details) > DETAIL_LIMIT:
        raise ValueError('Invalid browser details')
    by_url = {}
    allowed = {c['url'] for c in candidates}
    for detail in details:
        if not isinstance(detail, dict) or detail.get('url') not in allowed or detail['url'] in by_url:
            raise ValueError('Unrelated or duplicate browser detail')
        by_url[detail['url']] = detail
    for candidate in candidates:
        detail = by_url.get(candidate['url'])
        if detail and detail.get('http_status') == 200 and detail.get('final_url') == candidate['url']:
            try:
                row = sources.parse_schema('chrono24', detail['html'], candidate['url'])
                result['offers'].append(row)
                coverage['checked'] += 1
                continue
            except sources.Ineligible as exc:
                coverage['checked'] += 1
                result['excluded'].append({'source': 'chrono24', 'url': candidate['url'], 'reason': str(exc)})
                continue
            except (ValueError, KeyError, TypeError):
                pass
        # A genuine removed listing is not a live lead even if the search is stale.
        if detail and (detail.get('http_status') in (404, 410) or
                       (detail.get('http_status') == 200 and detail.get('final_url') == sources.SEARCHES['chrono24'])):
            coverage['checked'] += 1
            result['excluded'].append({'source': 'chrono24', 'url': candidate['url'], 'reason': 'Inserat entfernt/weitergeleitet'})
            continue
        result['hints'].append(candidate)
    if result['hints']:
        coverage['errors'].append('Nur Suchtreffer: Zustand, Referenzdetails, Gesamtpreis und Schweden-Versand unbestätigt')
    if truncated:
        coverage['errors'].append('Nur erste Suchseite; weitere Ergebnisse möglich')
    if snapshot.get('error'):
        coverage['errors'].append('Browser-Prüfung vorzeitig beendet')
    coverage['status'] = 'partial' if coverage['errors'] else 'ok'
    return result


def load_snapshot(path, now):
    """A missing/stale browser step must not silently reuse old discoveries."""
    try:
        path = Path(path)
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError('Snapshot too large')
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        created = datetime.fromisoformat(snapshot['checked_at'])
        age = (now - created).total_seconds()
        if snapshot.get('version') != 1 or snapshot.get('reference') != sources.REFERENCE or not -60 <= age <= 1800:
            raise ValueError('Invalid or stale browser snapshot')
        return analyze(snapshot)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'offers': [], 'hints': [], 'excluded': [], 'coverage': {
            'source': 'chrono24', 'status': 'error', 'checked': 0, 'found': 0,
            'mode': 'browser', 'search_readable': False,
            'errors': ['Browser-Bericht fehlt, ist veraltet oder ungültig; keine neuen Hinweise']}}


def capture(browser, snapshot):
    from watch_browser_probe import navigate

    context = browser.new_context(locale='sv-SE', timezone_id='Europe/Stockholm',
                                  viewport={'width': 1440, 'height': 1000}, accept_downloads=False)
    try:
        page = context.new_page()

        def fetch(url):
            html, nav = navigate(page, url)
            if len(html.encode('utf-8')) > 8_000_000:
                raise ValueError('Browser page too large')
            return {'url': url, 'final_url': sources.safe_url('chrono24', page.url),
                    'http_status': nav['http_status'], 'html': html}

        snapshot['search'] = fetch(sources.SEARCHES['chrono24'])
        if snapshot['search']['http_status'] != 200 or snapshot['search']['final_url'] != sources.SEARCHES['chrono24']:
            return
        candidates, _ = search_candidates(snapshot['search']['html'])
        for candidate in candidates[:DETAIL_LIMIT]:
            detail = fetch(candidate['url'])
            snapshot['details'].append(detail)
            if detail['http_status'] in (403, 429):
                break  # No retries, proxy rotation or CAPTCHA interactions.
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/chrono-browser.json'))
    args = parser.parse_args()
    snapshot = {'version': 1, 'reference': sources.REFERENCE,
                'checked_at': datetime.now(timezone.utc).isoformat(), 'search': {}, 'details': []}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            try:
                capture(browser, snapshot)
            finally:
                browser.close()
    except Exception as exc:
        snapshot['error'] = type(exc).__name__  # Never log raw HTML, tokens or cookies.
    result = analyze(snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(args.output, json.dumps(snapshot, ensure_ascii=True))
    print(json.dumps({'chrono24_browser': result['coverage'], 'verified_offers': len(result['offers']),
                      'unverified_hints': len(result['hints'])}, ensure_ascii=True))
    return 0 if result['coverage']['search_readable'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
