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
        raise ServiceError('GitHub command failed. Check login and permissions with gh auth status.')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description='Connect Telegram and enable the remote search service.')
    parser.add_argument('--repo', default='DieserLaurenz/ps5-blocket')
    args = parser.parse_args()
    try:
        repo = json.loads(gh('repo', 'view', args.repo, '--json', 'visibility'))
        if repo['visibility'] != 'PUBLIC':
            raise ServiceError('Stopped: free operation is configured for a public repository.')
        print('1. Use /newbot with @BotFather or get the token for your existing bot.')
        token = getpass.getpass('Bot token (hidden input): ').strip()
        if not token or ':' not in token:
            raise ServiceError('Invalid token format.')
        me = request_json(f'https://api.telegram.org/bot{token}/getMe')
        if not me.get('ok'):
            raise ServiceError('Could not verify the bot token.')
        username = me['result']['username']
        challenge = '/connect_' + secrets.token_hex(4)
        print(f'2. Open https://t.me/{username}, press Start and send this bot: {challenge}')
        print('Waiting up to 5 minutes for your private message ...')
        deadline = time.monotonic() + 300
        chat_id = None
        while time.monotonic() < deadline:
            updates = request_json(f'https://api.telegram.org/bot{token}/getUpdates', data={'timeout': 0, 'limit': 100})
            if not updates.get('ok'):
                raise ServiceError('Cannot fetch bot messages. Use your own bot without an active webhook.')
            matches = [u['message'] for u in updates['result'] if u.get('message', {}).get('text', '').strip() == challenge
                       and u['message'].get('chat', {}).get('type') == 'private']
            if matches:
                chat_id = str(matches[-1]['chat']['id'])
                break
            time.sleep(3)
        if chat_id is None:
            raise ServiceError('No connection message received. Run setup again.')
        gh('secret', 'set', 'TELEGRAM_BOT_TOKEN', '--repo', args.repo, secret=token)
        gh('secret', 'set', 'TELEGRAM_CHAT_ID', '--repo', args.repo, secret=chat_id)
        Telegram(token, chat_id).send('Telegram connected. Enabling your PS5 search on GitHub now.')
        gh('variable', 'set', 'PS5_MONITOR_ENABLED', '--body', 'true', '--repo', args.repo)
        gh('workflow', 'enable', 'monitor.yml', '--repo', args.repo)
        gh('workflow', 'run', 'monitor.yml', '--repo', args.repo)
        print(f'Done. Search enabled: https://github.com/{args.repo}/actions/workflows/monitor.yml')
        print('You will receive matching listings, new price lows and a daily status update.')
        return 0
    except (ServiceError, ValueError, KeyError, OSError):
        print('Setup failed. Check your bot token, private connection message and GitHub login.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nSetup cancelled.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
