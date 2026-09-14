"""Public watch pages. No logins, CAPTCHA solving, proxies or automatic block retries."""
from __future__ import annotations

from html import unescape
import json
import math
import os
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from ifk_monitor import Document, Node
from monitor import ServiceError

REFERENCE = 'H36215140'
EU = set('AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE'.split())
BASES = {'chrono24': 'https://www.chrono24.se', 'uret': 'https://www.uret.se', 'ebay': 'https://www.ebay.com'}
SEARCHES = {'chrono24': BASES['chrono24'] + '/hamilton/ref-h36215140.htm',
            'uret': BASES['uret'] + '/search/H36215140',
            'ebay': BASES['ebay'] + '/sch/i.html?_nkw=H36215140&_sop=15&_stposCountry=SE'}


class Ineligible(ValueError):
    """A parsed offer cannot safely be recommended; not a network failure."""


def money(value):
    if isinstance(value, bool):
        raise ValueError('Invalid price')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError('Invalid price')
    return round(number, 2)


def sek(text):
    match = re.search(r'(\d[\d\s\xa0\u202f]*(?:[,.]\d{1,2})?)\s*(?:SEK|kr)\b', text)
    if not match:
        raise ValueError('SEK price missing')
    return money(re.sub(r'\s', '', match[1]).replace(',', '.'))


def safe_url(source, value):
    url = urlsplit(urljoin(BASES[source] + '/', value))
    if url.scheme != 'https' or url.hostname != urlsplit(BASES[source]).hostname or url.username or url.password:
        raise ValueError('Unexpected listing host')
    return urlunsplit((url.scheme, url.netloc, url.path, '', ''))


def script_json(doc, *, script_id=None, script_type=None):
    for node in doc.find(tag='script'):
        if (script_id and node.attrs.get('id') == script_id) or (script_type and node.attrs.get('type') == script_type):
            yield json.loads(''.join(c for c in node.children if isinstance(c, str)))


def products(value):
    if isinstance(value, list):
        for item in value:
            yield from products(item)
    elif isinstance(value, dict):
        if value.get('@type') == 'Product':
            yield value
        if '@graph' in value:
            yield from products(value['@graph'])


def angular(doc, api_path):
    state = next(script_json(doc, script_id='ng-state'), None)
    if not isinstance(state, dict):
        raise ValueError('Uret product state missing')
    for entry in state.values():
        if isinstance(entry, dict) and entry.get('u') == BASES['uret'] + '/api' + api_path and entry.get('s') == 200:
            return entry['b']
    raise ValueError('Expected Uret API record missing')


def table(doc):
    result = {}
    for tr in doc.find(tag='tr'):
        cells = [c for c in tr.children if isinstance(c, Node) and c.tag in ('td', 'th')]
        if len(cells) == 2:
            result[cells[0].text().strip(':')] = cells[1].text()
    return result


def parse_schema(source, html, url):
    """Strict product-level shipping evidence, never search snippets/recommendations."""
    doc = Document(html).root
    url = safe_url(source, url)
    candidates = [p for value in script_json(doc, script_type='application/ld+json') for p in products(value)]
    if not candidates and any(re.search(r'Pris (?:vid|på) (?:förfrågan|begäran)', n.text(), re.I) for n in doc.find(tag='title')):
        raise Ineligible('Preis auf Anfrage')
    if len(candidates) != 1:
        raise ValueError('Single main product missing')
    p = candidates[0]
    offer = p.get('offers')
    if not isinstance(offer, dict) or offer.get('@type') != 'Offer':
        raise Ineligible('Kein eindeutiger Festpreis')
    brand = p.get('brand', {})
    brand = brand.get('name', '') if isinstance(brand, dict) else brand
    if str(brand).lower() != 'hamilton' or str(p.get('sku', '')).upper() != REFERENCE:
        raise Ineligible('Referenz nicht exakt bestätigt')
    if safe_url(source, offer.get('url', '')) != url:
        raise ValueError('Product URL differs from requested listing')
    if str(offer.get('availability', '')).rsplit('/', 1)[-1] not in ('InStock', 'PreOrder', 'BackOrder'):
        raise Ineligible('Nicht bestellbar')
    title = unescape(p.get('name', '')).strip()
    if re.search(r'\b(replica|replika|defekt|broken|parts only|for parts|strap only|bracelet only)\b', title, re.I):
        raise Ineligible('Defekt, Replik oder Zubehör')
    details = table(doc)
    condition_text = details.get('Skick', '')
    condition = str(offer.get('itemCondition', p.get('itemCondition', ''))).rsplit('/', 1)[-1]
    if condition == 'NewCondition' or condition_text.startswith('Som ny och oanvänd'):
        condition = 'new'
    elif condition in ('UsedCondition', 'RefurbishedCondition'):
        condition = 'used'
    else:
        raise Ineligible('Zustand nicht bestätigt')
    if re.search(r'\b(defekt|bristfällig|ofullständig|dålig|poor|incomplete)\b', condition_text, re.I):
        raise Ineligible('Schlechter oder defekter Zustand')
    shipping = offer.get('shippingDetails', [])
    shipping = shipping if isinstance(shipping, list) else [shipping]
    shipping = [s for s in shipping if isinstance(s, dict) and s.get('shippingDestination', {}).get('addressCountry') == 'SE']
    if not shipping:
        raise Ineligible('Versand nach Schweden nicht ausdrücklich bestätigt')
    rates = [s.get('shippingRate', {}) for s in shipping]
    rates = [money(r['value']) for r in rates if r.get('currency') == 'SEK' and 'value' in r]
    if offer.get('priceCurrency') != 'SEK' or not rates:
        raise Ineligible('Gesamtpreis in SEK einschließlich Versand unbekannt')
    origin = offer.get('availableAtOrFrom', {}).get('address', {}).get('addressCountry', '')
    if origin not in EU:
        raise Ineligible('Versandursprung außerhalb EU oder unbekannt; Importgesamtpreis ungeklärt')
    if offer.get('price') in (None, ''):
        raise Ineligible('Preis auf Anfrage')
    price = money(offer.get('price'))
    visible = doc.find(cls='wt-listing-detail-page-price') if source == 'chrono24' else []
    if visible:
        displayed = sek(visible[0].text())
        if abs(displayed - price) > max(50, price * .01):
            raise ValueError('Visible and structured price conflict')
        price = displayed
    if price <= 0:
        raise Ineligible('Preis auf Anfrage')
    images = p.get('image', [])
    images = images if isinstance(images, list) else [images]
    images = [i.get('contentUrl', '') if isinstance(i, dict) else i for i in images]
    image_host = 'img.chrono24.com' if source == 'chrono24' else 'i.ebayimg.com'
    images = [i for i in images if isinstance(i, str) and urlsplit(i).scheme == 'https' and urlsplit(i).hostname == image_host]
    ident = re.search(r'--id(\d+)\.htm', url) if source == 'chrono24' else re.search(r'/itm/(\d+)', url)
    if not ident:
        raise ValueError('Listing ID missing')
    return {'id': source + ':' + ident[1], 'source': source, 'title': title, 'reference': REFERENCE,
            'url': url, 'condition': condition, 'condition_text': condition_text[:300],
            'price_sek': price, 'shipping_sek': min(rates), 'total_sek': round(price + min(rates), 2),
            'destination': 'SE', 'origin': origin, 'photos': images[:10],
            'availability': details.get('Tillgänglighet', 'Laut Angebot bestellbar'),
            'scope': details.get('Leveransens omfattning', 'Box/Papiere nicht bestätigt'),
            'shipping_evidence': 'Angebotsdaten: Versandziel SE, Versandkosten SEK',
            'native_price': details.get('Pris', '')[:100]}


def parse_uret(html, url):
    doc = Document(html).root
    url = safe_url('uret', url)
    p = angular(doc, urlsplit(url).path)
    if p.get('model') != REFERENCE or p.get('brand') != 'Hamilton' or p.get('type') != 'watch':
        raise Ineligible('Referenz oder Produkttyp nicht exakt bestätigt')
    if safe_url('uret', p.get('url', '')) != url:
        raise ValueError('Uret product URL mismatch')
    body = doc.text()
    if p.get('active') != 1 or 'Lägg i varukorgen' not in body:
        raise Ineligible('Nicht bestellbar')
    # The Swedish shop currently incorrectly declares lang="en" in its HTML.
    # Use its visible country/language selector, product API host and VAT instead.
    if not re.search(r'Sverige\s*[•·]\s*Svenska', body):
        raise Ineligible('Schwedischer Shopkontext nicht bestätigt')
    tags = p.get('tags', {})
    state = tags.get('condition', {})
    condition = {'new': 'new', 'unworn': 'new', 'mint': 'used', 'very_good': 'used', 'good': 'used', 'used': 'used'}.get(state.get('value'))
    if not condition:
        raise Ineligible('Zustand unbekannt oder unzureichend')
    price = money(p['pricing']['current_price'])
    if price <= 0 or p['pricing'].get('vat', {}).get('rate') != 25:
        raise Ineligible('Schwedischer Bruttopreis nicht bestätigt')
    shipping_match = re.search(r'Fraktkostnad:\s*(\d[\d\s,.]*kr)', body)
    if not shipping_match:
        raise Ineligible('Versandkosten nicht ausdrücklich angegeben')
    shipping = sek(shipping_match[1])
    availability = re.search(r'(Beställningsvara[^.]*?|\d+\s*arbetsdagar)\s*Fraktkostnad:', body)
    images = []
    filenames = {i.get('filename') for i in p.get('images', [])}
    for img in doc.find(tag='img'):
        src = img.attrs.get('src', '') or img.attrs.get('srcset', '').split(' ')[0]
        parts = urlsplit(src)
        if (parts.scheme == 'https' and re.fullmatch(r'media\d+\.uret\.se', parts.hostname or '')
                and any(f and parts.path.endswith('/' + f) for f in filenames)):
            image = urlunsplit((parts.scheme, parts.netloc, parts.path, 'dh=1100&q=80', ''))
            if image not in images:
                images.append(image)
    return {'id': 'uret:' + str(p['id']), 'source': 'uret', 'title': 'Hamilton ' + p['name'].strip(),
            'reference': REFERENCE, 'url': url, 'condition': condition, 'condition_text': state.get('display', ''),
            'price_sek': price, 'shipping_sek': shipping, 'total_sek': round(price + shipping, 2),
            'destination': 'SE', 'origin': 'SE', 'photos': images[:10],
            'availability': availability[1] if availability else p.get('stock_status', {}).get('leadtime_view', 'Bestellbar'),
            'scope': '; '.join(tags[k].get('display', '') for k in ('box', 'papers', 'warranty_type') if k in tags),
            'shipping_evidence': 'Uret.se: Sverige, schwedischer Bruttopreis und sichtbare Versandkosten',
            'native_price': f'{price:g} SEK'}


class Client:
    def __init__(self):
        # Lazy import: all parsing/state tests run offline without third-party packages.
        from curl_cffi import requests
        profile = os.environ.get('WATCH_BROWSER_PROFILE', 'chrome124')
        if profile not in ('chrome124', 'chrome142'):
            raise ValueError('Unsupported browser profile')
        self.session = requests.Session(impersonate=profile, timeout=25,
                                        headers={'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.7'})
        self.last_request = 0

    def close(self):
        self.session.close()

    def get(self, url):
        delay = 1.5 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        try:
            response = self.session.get(url, allow_redirects=False)
        except Exception:
            raise ServiceError('Netzwerk-/TLS-Abruf fehlgeschlagen') from None
        if response.status_code != 200:
            raise ServiceError(f'HTTP {response.status_code}; keine automatische Wiederholung')
        if len(response.content) > 8_000_000:
            raise ServiceError('Antwort überschreitet Größenlimit')
        html = response.content.decode('utf-8-sig')
        if re.search(r'<title>[^<]*(?:captcha|access denied|pardon our interruption|just a moment)', html, re.I):
            raise ServiceError('Zugriffsprüfung statt Angebotsseite')
        return html


def listing_links(source, html):
    doc = Document(html).root
    if source == 'uret':
        data = angular(doc, '/search/' + REFERENCE)
        if not isinstance(data.get('products'), list):
            raise ValueError('Uret search products missing')
        links = [safe_url(source, p['url']) for p in data['products'] if p.get('model') == REFERENCE]
        truncated = data.get('pagination', {}).get('totalPages', 1) > 1
    else:
        links = []
        pattern = r'/hamilton/[^?]*--id\d+\.htm' if source == 'chrono24' else r'/itm/(?:[^/]+/)?\d+'
        for a in doc.find(tag='a'):
            href = a.attrs.get('href', '')
            if re.search(pattern, href):
                links.append(safe_url(source, href))
        if not links:
            # A challenge/redesign must never silently look like a successful empty search.
            if not re.search(r'(0 visa även annonser|inga annonser|0 results|No exact matches found)', doc.text(), re.I):
                raise ValueError('Keine verifizierbare Ergebnisliste gefunden')
        truncated = any(a.attrs.get('rel') == 'next' for a in doc.find(tag='a'))
    return list(dict.fromkeys(links)), truncated


def collect(config, client_factory=Client):
    offers, coverage, excluded = [], [], []
    for source in config['sources']:
        client = client_factory()
        status = {'source': source, 'status': 'ok', 'checked': 0, 'found': 0, 'errors': []}
        try:
            links, truncated = listing_links(source, client.get(SEARCHES[source]))
            status['found'] = len(links)
            if truncated or len(links) > config['detail_limit']:
                status['errors'].append('Ergebnis-/Detailgrenze erreicht; Abdeckung unvollständig')
            for url in links[:config['detail_limit']]:
                try:
                    html = client.get(url)
                    row = parse_uret(html, url) if source == 'uret' else parse_schema(source, html, url)
                    offers.append(row)
                    status['checked'] += 1
                except Ineligible as exc:
                    status['checked'] += 1
                    excluded.append({'source': source, 'url': url, 'reason': str(exc)})
                except (ServiceError, ValueError, KeyError, TypeError, StopIteration) as exc:
                    status['errors'].append(str(exc) if isinstance(exc, ServiceError) else 'Angebotsformat nicht erkannt')
                    if isinstance(exc, ServiceError) and ('HTTP 403' in str(exc) or 'HTTP 429' in str(exc)):
                        break
        except (ServiceError, ValueError, KeyError, TypeError, StopIteration) as exc:
            status['errors'].append(str(exc) if isinstance(exc, ServiceError) else 'Suchformat nicht erkannt')
        finally:
            client.close()
        if status['errors']:
            status['status'] = 'partial' if status['checked'] else 'error'
        coverage.append(status)
    return sorted(offers, key=lambda o: o['total_sek']), coverage, excluded
