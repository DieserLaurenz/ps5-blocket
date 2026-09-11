"""Verify read-only Tradera access and enable the module without saving keys locally."""
import getpass
import subprocess
import sys

import tradera

REPO = 'DieserLaurenz/ps5-blocket'


def gh(*args, secret=None):
    result = subprocess.run(['gh', *args, '--repo', REPO], input=secret,
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('GitHub setup failed')


def main():
    print('Register at https://api.tradera.com/register and create an approved application.')
    print('This verifies read-only search access, stores two GitHub secrets, and enables Tradera.')
    print('The existing cron job will then search both marketplaces. No bids or purchases are made.')
    try:
        app_id = getpass.getpass('Tradera AppId (hidden input): ').strip()
        app_key = getpass.getpass('Tradera AppKey (hidden input): ').strip()
        client = tradera.Client(0, app_id, app_key)
        response = client.call('Search', {'query': 'ps5', 'categoryId': 0, 'pageNumber': 1, 'orderBy': 'PriceAscending'})
        if tradera.number(response, 'TotalNumberOfPages') is None:
            raise RuntimeError('Search schema not confirmed')
        gh('secret', 'set', 'TRADERA_APP_ID', secret=app_id)
        gh('secret', 'set', 'TRADERA_APP_KEY', secret=app_key)
        gh('variable', 'set', 'TRADERA_ENABLED', '--body', 'true')
        print('Verified and enabled. Check the next PS5 Search run for Tradera source status.')
        return 0
    except KeyboardInterrupt:
        print('\nCancelled. Check the repository variable if setup was interrupted during activation.')
    except Exception:
        # Never echo raw CLI or API errors: credentials may be present.
        print('Setup failed. Check developer approval, app credentials, and gh auth status. '
              'Some secrets may already have been saved; it is safe to rerun.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
