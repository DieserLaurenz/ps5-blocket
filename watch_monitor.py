"""Hamilton alerts, independent from the PS5 and IFK notification histories."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import sys

from monitor import LocalStore, ServiceError, Telegram, request_json
from scraper import atomic_write
import watch_sources as sources

ROOT = Path(__file__).resolve().parent


def config_from(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    if config.get('reference') != sources.REFERENCE or config.get('destination') != 'SE':
        raise ServiceError('Dieser Monitor unterstützt nur H36215140 mit Versand nach Schweden.')
    for key in ('max_total_sek', 'bargain_total_sek'):
        if set(config.get(key, {})) != {'new', 'used'}:
            raise ServiceError('Preisgrenzen für neu und gebraucht fehlen.')
        for value in config[key].values():
            if sources.money(value) <= 0:
                raise ServiceError('Ungültige Preisgrenze.')
    if any(config['bargain_total_sek'][c] > config['max_total_sek'][c] for c in ('new', 'used')):
        raise ServiceError('Schnäppchengrenze liegt über Alarmgrenze.')
    if not isinstance(config.get('detail_limit'), int) or not 1 <= config['detail_limit'] <= 50:
        raise ServiceError('Detailgrenze muss zwischen 1 und 50 liegen.')
    if sources.money(config.get('minimum_drop_sek', 0)) < 1:
        raise ServiceError('Mindestpreissenkung muss positiv sein.')
    if not config.get('sources') or len(set(config['sources'])) != len(config['sources']) or any(s not in sources.BASES for s in config['sources']):
        raise ServiceError('Unbekannte oder doppelte Quelle.')
    if not isinstance(config.get('chrono24_unverified_hints', False), bool):
        raise ServiceError('Ungültige Einstellung für ungeprüfte Chrono24-Hinweise.')
    return config


def empty_state():
    return {'version': 1, 'reference': sources.REFERENCE, 'recipient': None, 'notified': {}}


def prepare_state(state, recipient):
    if not state:
        state = empty_state()
    if state.get('version') != 1 or state.get('reference') != sources.REFERENCE or not isinstance(state.get('notified'), dict):
        raise ServiceError('Unbekannter/beschädigter Uhrenstatus; keine Nachrichten versendet.')
    if state.get('recipient') != recipient:
        if state.get('recipient') is not None or state['notified']:
            raise ServiceError('Telegram-Empfänger geändert; Verlauf muss ausdrücklich migriert werden.')
        state['recipient'] = recipient
    for key, row in state['notified'].items():
        if not isinstance(row, dict) or not key.startswith(tuple(s + ':' for s in sources.BASES)):
            raise ServiceError('Beschädigter Angebotsverlauf.')
        if sources.money(row.get('lowest_total')) <= 0:
            raise ServiceError('Beschädigter Preisverlauf.')
    if 'health_at' in state:
        sources.money(state['health_at'])
    hints = state.setdefault('unverified_notified', {})
    if not isinstance(hints, dict):
        raise ServiceError('Beschädigter Hinweisverlauf.')
    for key, timestamp in hints.items():
        if not re.fullmatch(r'chrono24:\d+', key) or sources.money(timestamp) <= 0:
            raise ServiceError('Beschädigter Hinweisverlauf.')
    return state


class GitHubStore:
    def __init__(self, repo, token):
        if not repo or not token:
            raise ServiceError('GitHub-Konfiguration fehlt.')
        self.base = f'https://api.github.com/repos/{repo}/contents/watch-state.json'
        self.headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Hamilton-Watch-Monitor'}
        self.sha = None
        self.document = None

    def load(self):
        data = request_json(self.base + '?ref=watch-state', headers=self.headers)
        self.sha = data['sha']
        state = json.loads(base64.b64decode(data['content']))
        if not state:
            raise ServiceError('Remote-Uhrenstatus leer; kein automatischer Reset.')
        self.document = json.dumps(state, indent=2)
        return state

    def save(self, state):
        if not self.sha:
            raise ServiceError('Remote-Uhrenstatus wurde nicht geladen.')
        document = json.dumps(state, indent=2)
        if document == self.document:
            return
        result = request_json(self.base, headers=self.headers, method='PUT', data={
            'message': 'Update Hamilton notification state', 'branch': 'watch-state', 'sha': self.sha,
            'content': base64.b64encode(document.encode()).decode()})
        self.sha, self.document = result['content']['sha'], document


def qualifies(row, config):
    return (row['reference'] == config['reference'] and row['destination'] == 'SE'
            and row['origin'] in sources.EU and row['condition'] in config['max_total_sek']
            and row['total_sek'] <= config['max_total_sek'][row['condition']])


def message(row, config, old=None):
    label = '⌚ Neues passendes Angebot' if old is None else '📉 Neuer gemeldeter Tiefstpreis'
    if row['total_sek'] <= config['bargain_total_sek'][row['condition']]:
        label += ' · unter Schnäppchengrenze'
    condition = 'Neu/ungetragen laut Anbieter' if row['condition'] == 'new' else 'Gebraucht laut Anbieter'
    difference = f' (zuvor gemeldet: {old:g} SEK)' if old is not None else ''
    approximate = 'ca. ' if row.get('total_estimated') else ''
    return (f'<b>{label}</b> · {escape(row["source"])}\n\n'
            f'<b>Hamilton H36215140</b>\n{escape(row["title"][:180])}\n'
            f'💰 <b>{approximate}{row["total_sek"]:g} SEK inkl. Versand</b>{difference}\n'
            f'Uhr {row["price_sek"]:g} + Versand {row["shipping_sek"]:g} SEK\n'
            f'🚚 Nach Schweden · Angebotsstandort {escape(row["origin"])}\n'
            f'{condition}\n{escape(row["condition_text"][:240])}\n'
            f'Lieferbarkeit: {escape(row["availability"][:240])}\n'
            f'Lieferumfang/Garantie: {escape(row["scope"][:350])}\n\n'
            f'{escape(row.get("total_note", ""))}\n'
            f'<a href="{escape(row["url"], quote=True)}">Angebot öffnen</a>\n'
            'Anbieterangaben, keine Echtheitsprüfung. Endpreis und Zahlung ohne BankID vor Kauf prüfen. '
            'SEK-Preise können durch Wechselkurse schwanken.')


def health_message(report, config):
    lines = ['⌚ Hamilton H36215140 · Tagesstatus',
             f'Alarm: neu ≤ {config["max_total_sek"]["new"]:g}, gebraucht ≤ {config["max_total_sek"]["used"]:g} SEK inkl. Versand.',
             f'{len(report["matches"])} passende / {len(report["offers"])} mit bestätigtem Schwedenversand.']
    for c in report['coverage']:
        lines.append(f'{c["source"]}: {c["status"]}, {c["checked"]}/{c["found"]} Angebote geprüft'
                     + (f' — {c["errors"][0]}' if c['errors'] else ''))
    if report.get('hints'):
        lines.append(f'{len(report["hints"])} zusätzliche Chrono24-Suchtreffer ohne bestätigten Zustand/Schweden-Versand. '
                     'Diese zählen nicht als passende Angebote.')
    for condition, label in [('new', 'Neu'), ('used', 'Gebraucht')]:
        rows = [r for r in report['offers'] if r['condition'] == condition]
        if rows:
            row = min(rows, key=lambda r: r['total_sek'])
            approximate = 'ca. ' if row.get('total_estimated') else ''
            lines.append(f'Günstigstes geprüftes Angebot ({label}): {approximate}{row["total_sek"]:g} SEK — {row["source"]}\n{row["url"]}')
    lines.append('Keine vollständige Marktabdeckung. Unbekannter Versand/Importgesamtpreis wird aus geprüften Kaufangeboten ausgeschlossen.')
    return '\n'.join(lines)


def hint_message(row):
    from watch_chrono_browser import listing_id
    if row.get('reference') != sources.REFERENCE or row.get('source') != 'chrono24' or listing_id(row['url']) != row['id']:
        raise ServiceError('Ungültiger Chrono24-Hinweis.')
    url = sources.safe_url('chrono24', row['url'])
    return ('<b>🔎 Chrono24 · ungeprüfter Hinweis</b>\n\n'
            '<b>Neu im Monitor entdeckt</b> – nicht zwingend gerade inseriert.\n'
            'Gefunden in der Suche nach Hamilton H36215140.\n\n'
            f'Suchtreffer (unbestätigt): {escape(row["search_text"][:350])}\n\n'
            '<b>Kein bestätigtes Kaufangebot.</b> Die Detailprüfung war nicht möglich.\n'
            'Exakte Referenz und Gebrauchszustand noch zu prüfen; kann auch Neuware sein.\n'
            'Versand nach Schweden, Versandkosten und Gesamtpreis sind NICHT bestätigt. '
            'Ein angezeigter Suchpreis kann für ein anderes Lieferland gelten.\n'
            'Keine Prüfung gegen deine Preisgrenze möglich.\n\n'
            f'<a href="{escape(url, quote=True)}">Inserat selbst prüfen</a>')


def notify(report, config, store, telegram, now):
    state = prepare_state(store.load(), telegram.recipient)
    sent = 0
    for row in report['matches']:
        prior = state['notified'].get(row['id'])
        old = prior['lowest_total'] if prior else None
        if old is None or old - row['total_sek'] >= config['minimum_drop_sek']:
            mid = telegram.send(message(row, config, old), html=True)
            prior = {'lowest_total': row['total_sek'], 'message_id': mid, 'photo_attempts': 0,
                     'photo_sent': not bool(row['photos'])}
            state['notified'][row['id']] = prior
            store.save(state)
            sent += 1
        if prior and not prior.get('photo_sent', True) and prior.get('photo_attempts', 0) < 3:
            prior['photo_attempts'] = prior.get('photo_attempts', 0) + 1
            store.save(state)
            try:
                telegram.send_photos(row['photos'][:10], 'Hamilton H36215140 · Bilder des Angebots', reply_to=prior.get('message_id'))
                prior['photo_sent'] = True
                store.save(state)
            except ServiceError:
                print('WARN: Angebotsbilder konnten nicht versendet werden; maximal drei Versuche.')
    if config.get('chrono24_unverified_hints', False):
        for row in report.get('hints', []):
            if row['id'] in state['unverified_notified'] or row['id'] in state['notified']:
                continue
            telegram.send(hint_message(row), html=True)
            state['unverified_notified'][row['id']] = now
            store.save(state)
            sent += 1
    health_key = [(c['source'], c['status']) for c in report['coverage']]
    health_key = json.dumps(health_key)
    if now - state.get('health_at', 0) >= 86400 or health_key != state.get('health_key'):
        telegram.send(health_message(report, config))
        state.update(health_at=now, health_key=health_key)
        store.save(state)
    return sent


def write_report(report, directory):
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write(directory / 'watch-offers.json', json.dumps(report, indent=2, ensure_ascii=False))
    rows = []
    for row in report['offers']:
        rows.append('<tr>' + ''.join(f'<td>{escape(str(row[k]))}</td>' for k in
                    ('source', 'condition', 'price_sek', 'shipping_sek', 'total_sek', 'availability'))
                    + f'<td><a href="{escape(row["url"], quote=True)}">Angebot</a> {escape(row.get("total_note", ""))}</td></tr>')
    status = escape(json.dumps(report['coverage'], indent=2, ensure_ascii=False))
    excluded = escape(json.dumps(report['excluded'], indent=2, ensure_ascii=False))
    hints = ''.join(f'<li>{escape(row["search_text"])} — '
                    f'<a href="{escape(row["url"], quote=True)}">Inserat selbst prüfen</a></li>'
                    for row in report.get('hints', []))
    html = ('<!doctype html><html lang="de"><meta charset="utf-8"><title>Hamilton H36215140</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}td,th{padding:10px;text-align:left}'
            'table{border-collapse:collapse}tr{border-bottom:1px solid #ddd}pre{white-space:pre-wrap}</style>'
            f'<h1>Hamilton H36215140</h1><p>Stand: {escape(report["checked_at"])} · keine Live-Garantie</p>'
            f'<p>Alarmgrenzen inkl. Versand: neu {report["limits"]["new"]:g} / gebraucht {report["limits"]["used"]:g} SEK.</p>'
            '<p>Alle unten stehenden Angebote haben bestätigten Schwedenversand; auch Angebote über der Alarmgrenze.</p>'
            '<table><tr><th>Quelle</th><th>Zustand</th><th>Uhr SEK</th><th>Versand SEK</th><th>Gesamt SEK</th><th>Lieferbarkeit</th><th>Link</th></tr>'
            + ''.join(rows) + '</table><h2>Ungeprüfte Chrono24-Hinweise</h2>'
            '<p>Keine bestätigten Kaufangebote. Referenzdetails, Zustand, Schweden-Versand und Gesamtpreis unbestätigt; '
            'keine Prüfung gegen die Preisgrenze. Angezeigte Suchpreise können für ein anderes Lieferland gelten.</p>'
            f'<ul>{hints}</ul><h2>Quellenstatus</h2><pre>{status}</pre><h2>Ausgeschlossen</h2><pre>{excluded}</pre></html>')
    atomic_write(directory / 'watch-offers.html', html)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'watch-config.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'output')
    parser.add_argument('--dry-run', action='store_true', help='Live lesen und lokalen Bericht schreiben, ohne Telegram/Statusänderung')
    parser.add_argument('--local-state', type=Path, help='Lokaler statt GitHub-Verlauf')
    parser.add_argument('--chrono-snapshot', type=Path, help='Frischer Bericht des getrennten Chrono24-Browserschritts')
    args = parser.parse_args()
    try:
        config = config_from(args.config)
        now = datetime.now(timezone.utc)
        http_config = dict(config)
        if args.chrono_snapshot:
            http_config['sources'] = [s for s in config['sources'] if s != 'chrono24']
        offers, coverage, excluded = sources.collect(http_config)
        hints = []
        if args.chrono_snapshot and 'chrono24' in config['sources']:
            from watch_chrono_browser import load_snapshot
            browser = load_snapshot(args.chrono_snapshot, datetime.now(timezone.utc))
            offers.extend(browser['offers'])
            coverage.append(browser['coverage'])
            excluded.extend(browser['excluded'])
            if config.get('chrono24_unverified_hints', False):
                hints = browser['hints']
        offers.sort(key=lambda row: row['total_sek'])
        coverage.sort(key=lambda row: config['sources'].index(row['source']))
        report = {'checked_at': now.isoformat(), 'limits': config['max_total_sek'], 'offers': offers,
                  'matches': [o for o in offers if qualifies(o, config)], 'coverage': coverage, 'excluded': excluded,
                  'hints': hints}
        write_report(report, args.output)
        print(json.dumps({'coverage': coverage, 'offers': len(offers), 'matches': len(report['matches']),
                          'unverified_hints': len(hints),
                          'totals_sek': [o['total_sek'] for o in offers]}, ensure_ascii=True))
        if not args.dry_run:
            store = LocalStore(args.local_state) if args.local_state else GitHubStore(os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_TOKEN'))
            telegram = Telegram(os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID'))
            print(f'Alerts sent: {notify(report, config, store, telegram, now.timestamp())}')
        return 1 if all(c['status'] == 'error' for c in coverage) else 0
    except (ServiceError, OSError, ValueError, KeyError, TypeError, ImportError):
        # Never print exceptions that might include API tokens or private paths.
        print('ERROR: Uhrenmonitor fehlgeschlagen. Konfiguration, Abhängigkeiten und Status prüfen.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
