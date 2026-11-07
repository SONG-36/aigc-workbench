"""Manage only the independent macOS Showcase static origin."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.request

LABEL = 'com.song36.aigc-showcase'


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def target():
    return 'gui/{}/{}'.format(os.getuid(), LABEL)


def loaded():
    return run('launchctl', 'print', target(), check=False).returncode == 0


def start(root, restart=False):
    plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    if loaded():
        run('launchctl', 'kickstart', *(['-k'] if restart else []), target())
    else:
        run('launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(plist))


def healthy(expected):
    for _ in range(40):
        try:
            local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with local.open('http://127.0.0.1:8040/', timeout=2) as r:
                if r.status == 200 and r.headers.get('X-Showcase-Release') == expected:
                    return
        except OSError:
            pass
        time.sleep(.25)
    raise RuntimeError('Showcase did not become ready on 127.0.0.1:8040')


def activate(root, release):
    if not re.fullmatch(r'[a-f0-9]{40}-\d{8}T\d{6}Z', release):
        raise ValueError('Invalid release identity')
    directory = root / 'releases' / release
    if not (directory / 'release.json').is_file():
        raise ValueError('Release is not installed')
    current = root / 'current'
    if current.exists() and not current.is_symlink():
        raise ValueError('Refusing to replace a non-symlink current')
    previous = os.readlink(current) if current.is_symlink() else None
    temporary = root / ('.current-' + str(os.getpid()))
    temporary.symlink_to(Path('releases') / release)
    temporary.replace(current)
    try:
        start(root, restart=True)
        healthy(release)
    except Exception:
        if previous:
            temporary.symlink_to(previous)
            temporary.replace(current)
            start(root, restart=True)
        raise
    print(json.dumps({'active_release': release, 'previous_release': previous}))


def install(root, archive, expected_hash):
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError('Transferred archive hash mismatch')
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(not m.isfile() for m in members):
            raise ValueError('Only unique regular files are permitted')
        metadata = json.load(bundle.extractfile('release.json'))
        manifest = json.load(bundle.extractfile('manifest.json'))
        allowed = {'server.py', 'control.py', 'release.json', 'manifest.json'} | {'site/' + f['path'] for f in manifest}
        if set(names) != allowed or any(n.startswith('/') or '..' in Path(n).parts or '\\' in n for n in names):
            raise ValueError('Unexpected archive members')
        release = metadata['release_id']
        if not re.fullmatch(r'[a-f0-9]{40}-\d{8}T\d{6}Z', release):
            raise ValueError('Invalid release identity')
        if sum(m.size for m in members) > 100 * 1024 * 1024:
            raise ValueError('Archive exceeds static release size limit')
        root.mkdir(parents=True, exist_ok=True)
        (root / 'releases').mkdir(exist_ok=True)
        (root / 'logs').mkdir(exist_ok=True)
        destination = root / 'releases' / release
        if destination.exists():
            raise ValueError('Release already exists; refusing overwrite')
        staging = Path(tempfile.mkdtemp(prefix='.incoming-', dir=root / 'releases'))
        for item in members:
            file = staging / item.name
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open('xb') as stream:
                stream.write(bundle.extractfile(item).read())
            file.chmod(0o444)
        for f in manifest:
            if hashlib.sha256((staging / 'site' / f['path']).read_bytes()).hexdigest() != f['sha256']:
                raise ValueError('Static content hash mismatch')
        staging.rename(destination)
    agent = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    definition = {'Label': LABEL, 'ProgramArguments': ['/usr/bin/python3', '-u', str(root / 'current/server.py'),
        '--root', str(root / 'current/site'), '--manifest', str(root / 'current/manifest.json'),
        '--release-file', str(root / 'current/release.json')], 'WorkingDirectory': str(root),
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 5,
        'StandardOutPath': str(root / 'logs/access.log'), 'StandardErrorPath': str(root / 'logs/error.log')}
    encoded = plistlib.dumps(definition)
    if agent.exists() and agent.read_bytes() != encoded:
        raise ValueError('Existing launchd definition differs; refusing overwrite')
    if not agent.exists():
        agent.parent.mkdir(parents=True, exist_ok=True)
        with agent.open('xb') as f:
            f.write(encoded)
    activate(root, release)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.home() / 'apps/aigc-showcase')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('install')
    command.add_argument('archive', type=Path)
    command.add_argument('--sha256', required=True)
    command = commands.add_parser('activate')
    command.add_argument('release')
    for name in ('start', 'stop', 'restart', 'status'):
        commands.add_parser(name)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == 'install':
        install(root, args.archive, args.sha256)
    elif args.command == 'activate':
        activate(root, args.release)
    elif args.command == 'stop':
        if loaded():
            run('launchctl', 'bootout', target())
    elif args.command in ('start', 'restart'):
        start(root, restart=args.command == 'restart')
        healthy(json.loads((root / 'current/release.json').read_text())['release_id'])
    else:
        print(json.dumps({'label': LABEL, 'loaded': loaded(), 'current': os.readlink(root / 'current')
                          if (root / 'current').is_symlink() else None}))
