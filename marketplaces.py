"""Small source registry and shared listing identity; legacy Blocket keys stay valid."""
import os
import scraper
import tradera


def name(row):
    source = row.get('source', 'blocket')
    if source not in ('blocket', 'tradera'):
        raise ValueError('Unknown listing source')
    return source.capitalize()


def key(row):
    name(row)
    identifier = str(row['id'])
    if not identifier.isascii() or not identifier.isdecimal():
        raise ValueError('Invalid listing ID')
    return ('tradera:' if row.get('source') == 'tradera' else '') + identifier


def url(row):
    key(row)
    if row.get('source') == 'tradera':
        category = str(row['category_id'])
        if not category.isascii() or not category.isdecimal():
            raise ValueError('Invalid Tradera category ID')
        return f'https://www.tradera.com/item/{category}/{row["id"]}'
    return f'https://www.blocket.se/recommerce/forsale/item/{row["id"]}'


def photos(row, values=None):
    values = values if values is not None else row.get('photos') or [row.get('image')]
    return tradera.photo_urls(values) if row.get('source') == 'tradera' else scraper.photo_urls(values, row['id'])


class Blocket:
    def __init__(self, config, state):
        self.config = config
        self.client = scraper.Client(config['request_delay'])

    def search(self):
        docs, pages, warnings = scraper.collect(self.config, self.client)
        rows, excluded = scraper.select(docs, self.config, self.client)
        return rows, {'scanned': len(docs), 'pages': len(pages), 'excluded': len(excluded), 'warnings': warnings}

    def extras(self, row):
        extras = scraper.parse_listing_extras(self.client.get(url(row)), row['id'])
        extras['photos'] = extras['photos'] or row.get('photos', [])
        row.update(extras)


REGISTRY = {'blocket': Blocket, 'tradera': tradera.Source}


def search(config, state):
    rows, clients, statuses = [], {}, {}
    settings = config.get('sources', {'blocket': {'enabled': True}})
    for source, options in settings.items():
        if source not in REGISTRY:
            raise ValueError('Unknown configured source')
        enabled = options.get('enabled', False)
        if source == 'tradera' and os.environ.get('TRADERA_ENABLED'):
            enabled = os.environ['TRADERA_ENABLED'].lower() == 'true'
        if not enabled:
            statuses[source] = {'status': 'disabled'}
            continue
        try:
            provider = REGISTRY[source](config, state)
            matches, stats = provider.search()
        except scraper.ScrapeError as exc:
            # Authentication/network failures affect only this source, not the other marketplace.
            statuses[source] = {'status': 'failed', 'error': str(exc)}
            continue
        clients[source] = provider
        rows.extend(matches)
        statuses[source] = {'status': 'incomplete' if stats['warnings'] else 'ok', 'matches': len(matches), **stats}
    if not clients:
        raise scraper.ScrapeError('No marketplace search succeeded: ' + '; '.join(
            source + ': ' + status.get('error', status['status']) for source, status in statuses.items()))
    return sorted(rows, key=lambda row: row['price']), clients, statuses
