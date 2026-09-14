"""Manual, read-only Chrono24 browser diagnostic. Never sends alerts or saves cookies."""
from __future__ import annotations

import json

import watch_sources as sources


def inspect_search(status, html):
    if status != 200:
        return {'status': 'http_blocked' if status in (403, 429) else 'http_error',
                'http_status': status, 'links': []}
    try:
        links, truncated = sources.listing_links('chrono24', html)
        return {'status': 'readable', 'http_status': status, 'links': links,
                'truncated': truncated}
    except (ValueError, KeyError, TypeError):
        return {'status': 'unrecognized_or_challenge', 'http_status': status, 'links': []}


def inspect_detail(status, html, url):
    if status != 200:
        return {'url': url, 'status': 'http_blocked' if status in (403, 429) else 'http_error',
                'http_status': status}
    try:
        row = sources.parse_schema('chrono24', html, url)
        return {'url': url, 'status': 'verified_sweden_offer', 'http_status': status,
                'reference': row['reference'], 'destination': row['destination'],
                'total_sek': row['total_sek'], 'condition': row['condition']}
    except sources.Ineligible as exc:
        return {'url': url, 'status': 'ineligible', 'http_status': status, 'reason': str(exc)}
    except (ValueError, KeyError, TypeError):
        return {'url': url, 'status': 'unrecognized_or_challenge', 'http_status': status}


def navigate(page, url):
    """One navigation with time for normal JavaScript rendering; no challenge interaction."""
    from playwright.sync_api import TimeoutError as BrowserTimeout

    statuses = []

    def response_seen(response):
        if response.request.is_navigation_request() and response.frame == page.main_frame:
            statuses.append(response.status)

    page.on('response', response_seen)
    timed_out = False
    try:
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
        except BrowserTimeout:
            timed_out = True
        # Keep the event loop running for browser scripts, including normal redirects.
        # Diagnostic only: a fixed bounded wait avoids treating early challenge HTML as final.
        page.wait_for_timeout(12000)
        html = page.content()
        result = {'http_status': statuses[-1] if statuses else None,
                  'navigation_statuses': statuses, 'navigation_timeout': timed_out,
                  'title': page.title()[:180], 'html_bytes': len(html.encode('utf-8'))}
        return html, result
    finally:
        page.remove_listener('response', response_seen)


def run(browser):
    # Locale is a browser preference, NOT evidence that the actual offer ships to Sweden.
    context = browser.new_context(locale='sv-SE', timezone_id='Europe/Stockholm',
                                  viewport={'width': 1440, 'height': 1000}, accept_downloads=False)
    try:
        page = context.new_page()
        html, nav = navigate(page, sources.SEARCHES['chrono24'])
        search = inspect_search(nav['http_status'], html)
        links = search.pop('links')
        search.update(nav, found=len(links))
        result = {'browser': browser.version, 'mode': 'headed Chromium under Xvfb',
                  'source': 'chrono24', 'search': search, 'details': [],
                  'verified_sweden_offers': 0}
        print('WATCH_BROWSER_SEARCH ' + json.dumps(search, ensure_ascii=True), flush=True)
        if search['status'] != 'readable':
            return result
        # Check at most three public product links; no pagination, logins or seller actions.
        for url in links[:3]:
            html, nav = navigate(page, url)
            detail = inspect_detail(nav['http_status'], html, url)
            detail.update(nav)
            result['details'].append(detail)
            if detail['status'] == 'verified_sweden_offer':
                result['verified_sweden_offers'] += 1
            print('WATCH_BROWSER_DETAIL ' + json.dumps(detail, ensure_ascii=True), flush=True)
            if detail['status'] == 'http_blocked':
                break
        return result
    finally:
        context.close()


def main():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            # Real Chromium, normal automation defaults; no stealth flags or proxy services.
            browser = playwright.chromium.launch(headless=False)
            try:
                result = run(browser)
            finally:
                browser.close()
        print('WATCH_BROWSER_RESULT ' + json.dumps(result, ensure_ascii=True), flush=True)
        # A successful workflow means at least one actual, strictly validated Sweden offer.
        return 0 if result['verified_sweden_offers'] else 2
    except Exception as exc:
        # Do not log raw HTML, cookies, request headers or potentially sensitive URL parameters.
        print('WATCH_BROWSER_ERROR ' + type(exc).__name__, flush=True)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
