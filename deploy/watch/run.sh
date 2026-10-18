#!/bin/sh
set -eu
cd /opt/hamilton-watch
state=/var/lib/hamilton-watch
python=/opt/hamilton-watch/.venv/bin/python
test -s "$state/watch-state.json"
rm -f "$state/chrono-browser.json" "$state/ebay-browser.json"
# Browser capture never receives notification or GitHub credentials.
env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID -u GITHUB_TOKEN \
  WATCH_BROWSER_BACKEND=auto xvfb-run -a "$python" watch_chrono_browser.py --output "$state/chrono-browser.json" || true
env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID -u GITHUB_TOKEN \
  xvfb-run -a "$python" watch_ebay_browser.py --output "$state/ebay-browser.json" || true
exec "$python" watch_monitor.py --local-state "$state/watch-state.json" \
  --chrono-snapshot "$state/chrono-browser.json" --ebay-snapshot "$state/ebay-browser.json" \
  --output "$state/output" "$@"
