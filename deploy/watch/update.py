"""Update the existing VPS monitor from a reviewed, pinned repository archive.

Run as root from the extracted release. Does not provision access or touch secrets,
browser profiles, the notification history, or other monitors.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import time

SOURCE = Path(__file__).resolve().parents[2]
TARGET = Path('/opt/hamilton-watch')
FILES = ('watch_sources.py', 'watch_monitor.py', 'watch_discovery.py', 'watch-config.json',
         'WATCH.md', 'deploy/watch/run.sh', 'deploy/watch/hamilton-watch.service')
UNIT = Path('/etc/systemd/system/hamilton-watch.service')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def install(src, target):
    temp = target.with_name(target.name + '.updating')
    shutil.copyfile(src, temp)
    temp.chmod(0o644)
    os.replace(temp, target)


def main():
    if os.geteuid() != 0 or SOURCE == TARGET:
        raise SystemExit('Run as root from a separate extracted release directory.')
    if not (TARGET / '.venv/bin/python').is_file() or not UNIT.is_file():
        raise SystemExit('Existing monitor installation required.')
    for relative in FILES:
        if not (SOURCE / relative).is_file():
            raise SystemExit('Incomplete release.')
    run('systemd-analyze', 'verify', str(SOURCE / 'deploy/watch/hamilton-watch.service'))
    run('systemctl', 'stop', 'hamilton-watch.timer')
    backup = Path('/root') / ('hamilton-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    previous = {}
    changed = False
    try:
        deadline = time.monotonic() + 900
        while True:
            state = subprocess.check_output(['systemctl', 'show', '-p', 'ActiveState', '--value', 'hamilton-watch.service'], text=True).strip()
            if state not in ('activating', 'active', 'deactivating', 'reloading'):
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Previous monitor run still active; no files changed.')
            time.sleep(2)
        for relative in FILES:
            old = TARGET / relative
            previous[relative] = old.exists()
            if old.exists():
                dest = backup / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, dest)
        shutil.copy2(UNIT, backup / 'installed.service')
        changed = True
        for relative in FILES:
            install(SOURCE / relative, TARGET / relative)
        install(SOURCE / 'deploy/watch/hamilton-watch.service', UNIT)
        run('systemctl', 'daemon-reload')
        run('systemd-analyze', 'verify', str(UNIT))
        print('Updated existing Hamilton monitor. History and credentials preserved.', flush=True)
        print('Rollback files:', backup, flush=True)
    except BaseException:
        if changed:
            for relative in FILES:
                if previous[relative]:
                    install(backup / relative, TARGET / relative)
                elif (TARGET / relative).exists():
                    (TARGET / relative).unlink()
            install(backup / 'installed.service', UNIT)
            run('systemctl', 'daemon-reload')
        raise
    finally:
        run('systemctl', 'start', 'hamilton-watch.timer')


if __name__ == '__main__':
    main()
