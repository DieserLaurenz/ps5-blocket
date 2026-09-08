"""Store an AI Studio free-tier key as a GitHub secret without writing it locally."""
import getpass
import subprocess
import sys
import urllib.request
import json
from evaluator import MODEL

REPO = 'DieserLaurenz/ps5-blocket'


def main():
    print('Create an API key at https://aistudio.google.com/api-keys')
    print('For free operation: use a free-tier project WITHOUT billing enabled.')
    print('This program does not enable billing. If quota runs out, the search continues without AI.')
    try:
        key = getpass.getpass('Gemini API key (hidden input): ').strip()
        request = urllib.request.Request(
            'https://generativelanguage.googleapis.com/v1beta/models/' + MODEL,
            headers={'x-goog-api-key': key})
        with urllib.request.urlopen(request, timeout=30) as response:
            json.load(response)
        result = subprocess.run(['gh', 'secret', 'set', 'GEMINI_API_KEY', '--repo', REPO],
                                input=key, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('secret setup failed')
        print('API key verified and saved as a GitHub secret. You can close this window.')
        return 0
    except KeyboardInterrupt:
        print('\nCancelled.')
    except Exception:
        # No raw API response or exception: these can contain credentials.
        print('Setup failed. Check your key, API access and gh auth status.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
