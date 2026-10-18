#!/bin/sh
set -eu
cd /opt/hamilton-watch
state=/var/lib/hamilton-watch
python=/opt/hamilton-watch/.venv/bin/python
test -s "$state/watch-state.json"
rm -f "$state/chrono-browser.json" "$state/ebay-browser.json" "$state/discovery.json"
# Browser capture never receives notification or GitHub credentials.
timeout --kill-after=15s 180s env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID -u GITHUB_TOKEN \
  WATCH_BROWSER_BACKEND=auto xvfb-run -a "$python" watch_chrono_browser.py --output "$state/chrono-browser.json" || true
timeout --kill-after=15s 360s env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID -u GITHUB_TOKEN \
  xvfb-run -a "$python" watch_ebay_browser.py --profile-dir "$state/ebay-profile" \
  --output "$state/ebay-browser.json" || true
timeout --kill-after=15s 150s env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID -u GITHUB_TOKEN \
  xvfb-run -a "$python" watch_discovery.py --output "$state/discovery.json" || true
exec "$python" watch_monitor.py --local-state "$state/watch-state.json" \
  --chrono-snapshot "$state/chrono-browser.json" --ebay-snapshot "$state/ebay-browser.json" \
  --discovery-snapshot "$state/discovery.json" \
  --output "$state/output" "$@"
