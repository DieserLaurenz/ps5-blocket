#!/bin/sh
set -eu
id hamilton >/dev/null 2>&1 || useradd --system --home-dir /var/lib/hamilton-watch --shell /usr/sbin/nologin hamilton
install -d -o hamilton -g hamilton -m 700 /var/lib/hamilton-watch
python3 -m venv /opt/hamilton-watch/.venv
/opt/hamilton-watch/.venv/bin/pip install -r /opt/hamilton-watch/deploy/watch/requirements.txt
apt-get install -y xvfb libdbus-glib-1-2
/opt/hamilton-watch/.venv/bin/python -c 'from camoufox.fpgen_model import ensure_fpgen_model; ensure_fpgen_model()'
runuser -u hamilton -- env HOME=/var/lib/hamilton-watch /opt/hamilton-watch/.venv/bin/python -m camoufox fetch official/156.0.1-beta.36
install -m 644 /opt/hamilton-watch/deploy/watch/hamilton-watch.service /etc/systemd/system/
install -m 644 /opt/hamilton-watch/deploy/watch/hamilton-watch.timer /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/hamilton-watch.service /etc/systemd/system/hamilton-watch.timer
# Provision the existing Telegram configuration and migrated state before enabling.
