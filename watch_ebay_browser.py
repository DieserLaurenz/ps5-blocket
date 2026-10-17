"""Read eBay with Camoufox and validate details; never notify from search prices."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from scraper import atomic_write
import watch_sources as sources
from watch_browser_probe import navigate

DETAIL_LIMIT = 5


def analyze(snapshot):
    coverage = {'source': 'ebay', 'status': 'error', 'checked': 0, 'found': 0,
                'errors': [], 'mode': 'camoufox'}
    result = {'offers': [], 'excluded': [], 'coverage': coverage}
    search = snapshot.get('search', {})
    if (search.get('url') != sources.SEARCHES['ebay'] or search.get('final_url') != sources.SEARCHES['ebay']
            or search.get('http_status') != 200):
        coverage['errors'].append('eBay-Browsersuche nicht erreichbar')
        return result
    try:
        links, truncated = sources.listing_links('ebay', search['html'])
    except (ValueError, KeyError, TypeError):
        coverage['errors'].append('eBay-Suchformat nicht erkannt')
        return result
    coverage['found'] = len(links)
    details = snapshot.get('details', [])
    if not isinstance(details, list) or len(details) > DETAIL_LIMIT:
        raise ValueError('Invalid eBay details')
    seen = set()
    for detail in details:
        url = detail.get('url')
        if url not in links or url in seen:
            raise ValueError('Unrelated or duplicate eBay detail')
        seen.add(url)
        if detail.get('http_status') != 200 or detail.get('final_url') != url:
            coverage['errors'].append('eBay-Artikelseite nicht erreichbar')
            continue
        try:
            row = sources.parse_ebay(detail['html'], url)
            result['offers'].append(row)
            coverage['checked'] += 1
        except sources.Ineligible as exc:
            coverage['checked'] += 1
            result['excluded'].append({'source': 'ebay', 'url': url, 'reason': str(exc)})
        except (ValueError, KeyError, TypeError):
            coverage['errors'].append('eBay-Angebotsdaten oder Schweden-Gesamtpreis nicht verifiziert')
    if truncated or len(seen) < len(links):
        coverage['errors'].append('Nur erste Suchseite und maximal fünf Artikel geprüft')
    if snapshot.get('error'):
        coverage['errors'].append('eBay-Browserlauf vorzeitig beendet')
    coverage['status'] = ('partial' if coverage['checked'] else 'error') if coverage['errors'] else 'ok'
    return result


def load_snapshot(path, now):
    try:
        path = Path(path)
        if path.stat().st_size > 50_000_000:
            raise ValueError('Snapshot too large')
        data = json.loads(path.read_text(encoding='utf-8'))
        age = (now - datetime.fromisoformat(data['checked_at'])).total_seconds()
        if data.get('version') != 1 or data.get('reference') != sources.REFERENCE or not -60 <= age <= 1800:
            raise ValueError('Invalid or stale eBay snapshot')
        return analyze(data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'offers': [], 'excluded': [], 'coverage': {'source': 'ebay', 'status': 'error',
                'checked': 0, 'found': 0, 'mode': 'camoufox',
                'errors': ['eBay-Browserbericht fehlt, ist veraltet oder ungültig']}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = {'version': 1, 'reference': sources.REFERENCE,
            'checked_at': datetime.now(timezone.utc).isoformat(), 'search': {}, 'details': []}
    try:
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=False, os='linux', locale='sv-SE') as browser:
            context = browser.new_context(locale='sv-SE', timezone_id='Europe/Stockholm')
            page = context.new_page()
            def fetch(url):
                html, nav = navigate(page, url)
                if len(html.encode('utf-8')) > 8_000_000:
                    raise ValueError('eBay page too large')
                return {'url': url, 'final_url': page.url, 'http_status': nav['http_status'], 'html': html}
            data['search'] = fetch(sources.SEARCHES['ebay'])
            if data['search']['http_status'] == 200 and data['search']['final_url'] == sources.SEARCHES['ebay']:
                links, _ = sources.listing_links('ebay', data['search']['html'])
                for url in links[:DETAIL_LIMIT]:
                    detail = fetch(url)
                    data['details'].append(detail)
                    if detail['http_status'] in (403, 429):
                        break
            context.close()
    except Exception as exc:
        data['error'] = type(exc).__name__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(args.output, json.dumps(data, ensure_ascii=True))
    result = analyze(data)
    print(json.dumps({'ebay_browser': result['coverage'], 'verified_offers': len(result['offers']),
                      'excluded': len(result['excluded'])}), flush=True)
    return 1 if result['coverage']['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
