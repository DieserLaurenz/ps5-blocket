"""Read-only Tradera v3 SOAP adapter, using the official, stable WSDL contracts.

No login scraping, bidding, buying, or seller messaging. Credentials only in SOAP
headers; never in URLs, saved state, or error messages. Standard library only.
"""
from datetime import datetime, timezone
import html
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import scraper

NS = 'http://api.tradera.com'
SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
METHODS = {'Search': 'SearchService', 'GetItem': 'PublicService', 'GetFeedbackSummary': 'PublicService'}


def child(node, path):
    return node.find('/'.join('{' + NS + '}' + part for part in path.split('/')))


def value(node, path, default=''):
    found = child(node, path)
    return found.text if found is not None and found.text is not None else default


def number(node, path, default=None):
    raw = value(node, path)
    return int(raw) if re.fullmatch(r'-?\d+', raw, flags=re.ASCII) else default


def elements(node, tag):
    return node.findall('{' + NS + '}' + tag)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward authentication XML to a different URL, even on a 307/308.
        return None


class Client:
    def __init__(self, delay=1, app_id=None, app_key=None):
        self.app_id = app_id if app_id is not None else os.environ.get('TRADERA_APP_ID', '')
        self.app_key = app_key if app_key is not None else os.environ.get('TRADERA_APP_KEY', '')
        if not re.fullmatch(r'[0-9]+', self.app_id) or not self.app_key:
            raise scraper.ScrapeError('Tradera credentials missing. Run Setup-Tradera.cmd or add GitHub secrets.')
        self.delay, self.last = delay, 0
        self.deadline = time.monotonic() + 100
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, method, params):
        if method not in METHODS:
            raise ValueError('Only read-only Tradera methods are allowed')
        if time.monotonic() >= self.deadline:
            raise scraper.ScrapeError('Tradera time budget reached; deferred until a later run.')
        envelope = ET.Element('{' + SOAP + '}Envelope')
        header = ET.SubElement(envelope, '{' + SOAP + '}Header')
        auth = ET.SubElement(header, '{' + NS + '}AuthenticationHeader')
        for key, val in [('AppId', self.app_id), ('AppKey', self.app_key)]:
            ET.SubElement(auth, '{' + NS + '}' + key).text = val
        body = ET.SubElement(envelope, '{' + SOAP + '}Body')
        action = ET.SubElement(body, '{' + NS + '}' + method)

        def append(parent, values):
            for key, val in values.items():
                node = ET.SubElement(parent, '{' + NS + '}' + key)
                if isinstance(val, dict):
                    append(node, val)
                else:
                    node.text = str(val)
        append(action, params)
        request = urllib.request.Request(f'https://api.tradera.com/v3/{METHODS[method]}.asmx',
            data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True),
            headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': '"' + NS + '/' + method + '"',
                     'User-Agent': 'PS5-Monitor/1.0 (personal read-only search)'})
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        try:
            with self.opener.open(request, timeout=min(20, max(1, self.deadline - time.monotonic()))) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('Unsafe or oversized XML')
            root = ET.fromstring(raw)
            result = root.find('.//{' + NS + '}' + method + 'Result')
            if result is None or root.find('.//{' + SOAP + '}Fault') is not None or elements(result, 'Errors'):
                raise ValueError('API fault or incomplete response')
            return result
        except urllib.error.HTTPError as exc:
            raise scraper.ScrapeError(f'Tradera API failed (HTTP {exc.code}); no automatic retry.') from None
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError):
            raise scraper.ScrapeError('Tradera connection or XML response failed.') from None


def photo_urls(values):
    """Only API-returned image URLs on Tradera's image CDN; never seller links."""
    result = []
    for item in values:
        if not isinstance(item, str):
            continue
        try:
            parsed = urllib.parse.urlsplit(item)
        except ValueError:
            continue
        if (parsed.scheme not in ('http', 'https') or parsed.netloc != 'img.tradera.net'
                or not parsed.path.startswith('/images/') or re.search(r'[\s<>"\\]', item)):
            continue
        url = urllib.parse.urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, ''))
        if url not in result:
            result.append(url)
    return result


def timestamp(text):
    try:
        date = datetime.fromisoformat(text.replace('Z', '+00:00'))
        # The SOAP API may omit an offset; its local dates use Swedish time.
        if date.tzinfo is None:
            from zoneinfo import ZoneInfo
            date = date.replace(tzinfo=ZoneInfo('Europe/Stockholm'))
        return date.astimezone(timezone.utc)
    except (ValueError, KeyError):
        return None


def listing(item, config, now=None):
    now = now or datetime.now(timezone.utc)
    identifier, category = number(item, 'Id'), number(item, 'CategoryId')
    title = value(item, 'ShortDescription')
    end = timestamp(value(item, 'EndDate'))
    start = timestamp(value(item, 'StartDate'))
    if identifier is None or category is None or min(identifier, category) <= 0 or scraper.title_reason(title):
        return None
    if value(item, 'Status/Ended') != 'false' or end is None or end <= now or (start and start > now):
        return None
    kind = value(item, 'ItemType')
    options = config.get('sources', {}).get('tradera', {})
    if kind == 'Auction':
        if not options.get('include_auctions', True):
            return None
        # NextBid is the actionable minimum, not an already-outbid displayed price.
        price = number(item, 'NextBid')
        if not price and number(item, 'TotalBids', 0) == 0:
            price = number(item, 'OpeningBid')
        sale_type = 'auction'
    elif kind in ('PureBuyItNow', 'ShopItem'):
        price, sale_type = number(item, 'BuyItNowPrice'), 'fixed'
        if number(item, 'RemainingQuantity', 0) <= 0:
            return None
    else:
        return None
    if price is None or not 0 < price <= config['max_price']:
        return None
    country = scraper.normalized(value(item, 'Seller/CountryName'))
    if country not in ('sverige', 'sweden', 'se', 'swe'):
        return None  # Sweden only; do not infer the seller's country from the website.
    location = value(item, 'Seller/City')
    pickup = value(item, 'AcceptsPickup') == 'true' and scraper.normalized(location) in {
        scraper.normalized(city) for city in config['pickup_cities']}
    shipping_options = elements(item, 'ShippingOptions')
    shipping = bool(shipping_options)
    description = html.unescape(re.sub('<[^>]*>', ' ', value(item, 'LongDescription')))
    terms = scraper.normalized(description + ' ' + value(item, 'ShippingCondition'))
    if re.search(r'\b(skickar inte|skickas inte|endast avhamtning|endast upphamtning|only pickup|no shipping)\b', terms):
        shipping = False
    if (not shipping and not pickup) or scraper.fault_in_description(scraper.normalized(description)):
        return None
    photos = photo_urls([img.text for img in item.findall('{' + NS + '}ImageLinks/{' + NS + '}string')])
    if not photos:
        photos = photo_urls([value(img, 'Url') for img in elements(item, 'DetailedImageLinks')])
    if not photos:
        photos = photo_urls([value(item, 'ThumbnailLink')])
    costs = [number(option, 'Cost') for option in shipping_options]
    costs = [cost for cost in costs if cost is not None and cost >= 0]
    return {'source': 'tradera', 'id': str(identifier), 'category_id': str(category),
            'title': title, 'price': price, 'currency': 'SEK', 'location': location,
            'shipping': shipping, 'pickup': pickup, 'shipping_cost': min(costs) if shipping and costs else None,
            'sale_type': sale_type, 'ends_at': end.isoformat(timespec='seconds'),
            'current_bid': number(item, 'MaxBid'), 'buy_now_price': number(item, 'BuyItNowPrice'),
            'reserve_not_met': value(item, 'ReservePriceReached') == 'false',
            'description': description, 'detail_checked': bool(description),
            'photos': photos, 'photos_checked': True, 'seller_rating': None,
            'seller_id': number(item, 'Seller/Id'), 'notes': [],
            'url': f'https://www.tradera.com/item/{category}/{identifier}'}


class Source:
    def __init__(self, config, state):
        self.config, self.state = config, state
        self.options = config.get('sources', {}).get('tradera', {})
        self.client = Client(self.options.get('request_delay', 1))
        self.feedback = {}
        self.extra_deadline = None

    def search(self):
        docs, warnings, pages = {}, [], 0
        # 20 searches + 24 details per five-minute run remain below each method's
        # documented 10,000/day default quota, with room for manual checks.
        max_pages = min(10, max(1, int(self.options.get('max_pages', 10))))
        for query in self.config['queries'][:2]:
            seen = set()
            for page in range(1, max_pages + 1):
                result = self.client.call('Search', {'query': query, 'categoryId': 0,
                                          'pageNumber': page, 'orderBy': 'PriceAscending'})
                pages += 1
                total_pages = number(result, 'TotalNumberOfPages')
                items = elements(result, 'Items')
                if total_pages is None or total_pages < 0 or (not items and total_pages >= page):
                    raise scraper.ScrapeError('Tradera search pagination response is incomplete.')
                ids = tuple(number(item, 'Id') for item in items)
                if items and (any(identifier is None or identifier <= 0 for identifier in ids) or ids in seen):
                    raise scraper.ScrapeError('Tradera repeated a search page or returned invalid IDs.')
                seen.add(ids)
                for item in items:
                    docs[str(number(item, 'Id'))] = item
                if page >= total_pages:
                    break
            else:
                warnings.append('Tradera search page limit reached; some listings may be missing.')
        candidates = []
        for identifier, item in docs.items():
            prices = [number(item, field) for field in ('NextBid', 'BuyItNowPrice', 'MaxBid')]
            if (not scraper.title_reason(value(item, 'ShortDescription'))
                    and value(item, 'IsEnded') == 'false'
                    and any(price is not None and 0 < price <= self.config['max_price'] for price in prices)):
                candidates.append(identifier)
        # Fair rotation: a large result set must not starve everything after the cheapest 24.
        checked = self.state.setdefault('tradera_checked', {})
        candidates.sort(key=lambda identifier: (checked.get(identifier, 0), int(identifier)))
        limit = min(24, max(1, int(self.options.get('detail_limit', 24))))
        rows, inspected = [], 0
        for identifier in candidates[:limit]:
            try:
                item = self.client.call('GetItem', {'itemId': identifier})
                if str(number(item, 'Id')) != identifier:
                    raise scraper.ScrapeError('Tradera returned a different item ID.')
            except scraper.ScrapeError:
                warnings.append('Tradera detail lookup failed; remaining candidates deferred.')
                break
            checked[identifier] = time.time()
            inspected += 1
            row = listing(item, self.config)
            if row:
                rows.append(row)
        self.state['tradera_checked'] = {key: checked[key] for key in candidates if key in checked}
        if len(candidates) > limit:
            warnings.append(f'Tradera detail budget: {len(candidates) - limit} candidates deferred to later runs.')
        return rows, {'scanned': len(docs), 'pages': pages, 'excluded': inspected - len(rows), 'warnings': warnings}

    def extras(self, row):
        if self.extra_deadline is None:
            self.extra_deadline = time.monotonic() + 30
        self.client.deadline = self.extra_deadline
        seller = row.get('seller_id')
        if not seller or seller <= 0:
            return
        if seller not in self.feedback:
            response = self.client.call('GetFeedbackSummary', {'getFeedbackSummaryRequest': {'UserId': seller}})
            positive, negative = number(response, 'LastTwelveMonth/TotalPositive'), number(response, 'LastTwelveMonth/TotalNegative')
            rating = None
            if number(response, 'UserId') == seller and positive is not None and negative is not None and min(positive, negative) >= 0 and positive + negative > 0:
                rating = {'positive': positive, 'negative': negative, 'count': positive + negative,
                          'score': round(100 * positive / (positive + negative), 1), 'best': 100, 'period': '12 months'}
            self.feedback[seller] = rating
        row['seller_rating'] = self.feedback[seller]
