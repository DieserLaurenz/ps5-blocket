"""Interactive secret setup: no token on disk, command line, or terminal output."""
import argparse
import getpass
import json
import secrets
import subprocess
import sys
import time

from monitor import request_json, ServiceError, Telegram


def gh(*args, secret=None):
    result = subprocess.run(['gh', *args], input=secret, text=True, capture_output=True)
    if result.returncode:
        raise ServiceError('GitHub-Befehl fehlgeschlagen. Anmeldung/Rechte mit gh auth status prüfen.')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description='Telegram verbinden und den Remote-Suchdienst aktivieren.')
    parser.add_argument('--repo', default='DieserLaurenz/ps5-blocket')
    args = parser.parse_args()
    try:
        repo = json.loads(gh('repo', 'view', args.repo, '--json', 'visibility'))
        if repo['visibility'] != 'PUBLIC':
            raise ServiceError('Abbruch: Kostenloser Betrieb ist für ein öffentliches Repository vorbereitet.')
        print('1. Bei @BotFather /newbot verwenden oder Token deines bestehenden Bots holen.')
        token = getpass.getpass('Bot-Token (Eingabe bleibt unsichtbar): ').strip()
        if not token or ':' not in token:
            raise ServiceError('Ungültiges Tokenformat.')
        me = request_json(f'https://api.telegram.org/bot{token}/getMe')
        if not me.get('ok'):
            raise ServiceError('Bot-Token konnte nicht bestätigt werden.')
        username = me['result']['username']
        challenge = '/connect_' + secrets.token_hex(4)
        print(f'2. Öffne https://t.me/{username}, drücke Start und sende diesem Bot: {challenge}')
        print('Warte bis zu 5 Minuten auf deine private Nachricht ...')
        deadline = time.monotonic() + 300
        chat_id = None
        while time.monotonic() < deadline:
            updates = request_json(f'https://api.telegram.org/bot{token}/getUpdates', data={'timeout': 0, 'limit': 100})
            if not updates.get('ok'):
                raise ServiceError('Bot-Nachrichten nicht abrufbar. Einen eigenen Bot ohne aktiven Webhook verwenden.')
            matches = [u['message'] for u in updates['result'] if u.get('message', {}).get('text', '').strip() == challenge
                       and u['message'].get('chat', {}).get('type') == 'private']
            if matches:
                chat_id = str(matches[-1]['chat']['id'])
                break
            time.sleep(3)
        if chat_id is None:
            raise ServiceError('Keine Verbindungsnachricht erhalten. Setup erneut starten.')
        gh('secret', 'set', 'TELEGRAM_BOT_TOKEN', '--repo', args.repo, secret=token)
        gh('secret', 'set', 'TELEGRAM_CHAT_ID', '--repo', args.repo, secret=chat_id)
        Telegram(token, chat_id).send('Telegram verbunden. Ich aktiviere jetzt deine PS5-Suche auf GitHub.')
        gh('variable', 'set', 'PS5_MONITOR_ENABLED', '--body', 'true', '--repo', args.repo)
        gh('workflow', 'enable', 'monitor.yml', '--repo', args.repo)
        gh('workflow', 'run', 'monitor.yml', '--repo', args.repo)
        print(f'Fertig. Suche aktiviert: https://github.com/{args.repo}/actions/workflows/monitor.yml')
        print('Du erhältst passende Angebote, neue Tiefpreise und täglich eine Statusmeldung.')
        return 0
    except (ServiceError, ValueError, KeyError, OSError):
        print('Setup fehlgeschlagen. Bot-Token, private Verbindungsnachricht und GitHub-Anmeldung prüfen.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nSetup abgebrochen.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
