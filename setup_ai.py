"""Store an AI Studio free-tier key as a GitHub secret without writing it locally."""
import getpass
import subprocess
import sys
import urllib.request
import json

REPO = 'DieserLaurenz/ps5-blocket'


def main():
    print('Erstelle einen API-Key unter https://aistudio.google.com/api-keys')
    print('Für kostenlosen Betrieb: ein Free-Tier-Projekt OHNE aktivierte Abrechnung verwenden.')
    print('Das Programm aktiviert keine Abrechnung. Bei ausgeschöpftem Kontingent läuft die Suche ohne KI weiter.')
    try:
        key = getpass.getpass('Gemini API-Key (unsichtbare Eingabe): ').strip()
        request = urllib.request.Request(
            'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite',
            headers={'x-goog-api-key': key})
        with urllib.request.urlopen(request, timeout=30) as response:
            json.load(response)
        result = subprocess.run(['gh', 'secret', 'set', 'GEMINI_API_KEY', '--repo', REPO],
                                input=key, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('secret setup failed')
        print('API-Key geprüft und als GitHub-Secret gespeichert. Du kannst dieses Fenster schließen.')
        return 0
    except KeyboardInterrupt:
        print('\nAbgebrochen.')
    except Exception:
        # No raw API response or exception: these can contain credentials.
        print('Einrichtung fehlgeschlagen. Key, API-Zugriff und gh auth status prüfen.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
