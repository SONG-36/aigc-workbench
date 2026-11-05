"""Owner-only controls for this LaunchAgent; no sudo, other-service changes or AI calls."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import time
from uuid import uuid4

LABEL = 'com.song36.aigc-workbench-pilot'


def loaded():
    return subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def activate(root, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit): raise ValueError('Use an exact commit SHA.')
    release = root / 'releases' / commit
    if (release.is_symlink() or not (release / 'backend/.venv/bin/python').is_file()
            or not (release / 'frontend/dist/index.html').is_file()
            or json.loads((release / 'release.json').read_text())['commit'] != commit):
        raise ValueError('Install and verify the release before activation.')
    current = root / 'current'
    if current.exists() and not current.is_symlink(): raise ValueError('Refusing to replace an existing directory.')
    temporary = root / ('.current-' + uuid4().hex)
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, current)


def wait_stopped(root):
    lock = root / 'shared/runtime/f01/c06-dispatch.lock'
    deadline = time.monotonic() + 10
    while lock.exists():
        with lock.open('r') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline: raise ValueError('Still stopping; do not restart or retry execution.')
        time.sleep(.2)


def install_agent(root):
    target = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    template = root / 'current/deploy' / (LABEL + '.plist')
    payload = plistlib.loads(template.read_bytes())
    payload['ProgramArguments'][0] = str(root / 'current/backend/.venv/bin/python')
    payload['ProgramArguments'][-1] = str(root / 'shared/pilot.json')
    payload['WorkingDirectory'] = str(root / 'current')
    for key, name in [('StandardOutPath', 'stdout.log'), ('StandardErrorPath', 'stderr.log')]:
        payload[key] = str(root / 'shared/logs' / name)
    content = plistlib.dumps(payload)
    if target.exists():
        if target.read_bytes() != content: raise ValueError('Existing LaunchAgent differs; not overwritten.')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream: stream.write(content)
        target.chmod(0o600)
    return target


def backup(root, name):
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', name): raise ValueError('Invalid backup name.')
    if loaded(): raise ValueError('Stop this service before backup; no automatic service stop.')
    destination = root / 'backups' / name
    subprocess.run([str(root / 'current/backend/.venv/bin/python'), '-m', 'aigc_workbench.pilot_backup', 'create',
        '--runtime-root', str(root / 'shared/runtime'), '--data-dir', str(root / 'shared/runtime/f01'),
        '--destination', str(destination)], check=True)
    metadata = {}
    for original, name in [(root / 'shared/pilot.json', 'pilot-config.json'),
                           (root / 'current/release.json', 'release-version.json')]:
        target = destination / name
        shutil.copyfile(original, target); target.chmod(0o600)
        metadata[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / 'deployment-files.json').write_text(json.dumps(metadata, indent=2))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'apps/aigc-workbench-pilot')
    parser.add_argument('command', choices=['activate', 'install-agent', 'start', 'stop', 'restart', 'status', 'backup'])
    parser.add_argument('value', nargs='?')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.root.absolute()
    domain = f'gui/{os.getuid()}'
    if args.command == 'activate':
        if loaded(): raise ValueError('Stop this service before switching releases.')
        activate(root, args.value or '')
    elif args.command == 'install-agent':
        print(install_agent(root))
    elif args.command == 'status':
        return subprocess.run(['launchctl', 'print', domain + '/' + LABEL]).returncode
    elif args.command == 'backup':
        print(backup(root, args.value or ''))
    else:
        if args.command in {'stop', 'restart'} and loaded():
            subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], check=True)
            wait_stopped(root)
        if args.command in {'start', 'restart'} and not loaded():
            subprocess.run(['launchctl', 'bootstrap', domain, str(install_agent(root))], check=True)
    return 0


if __name__ == '__main__': raise SystemExit(main())
