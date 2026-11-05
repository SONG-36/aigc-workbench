"""Install only a new, verified release and initialize this app's own empty store explicitly."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile


def verified_extract(bundle, release):
    if release.exists() or release.is_symlink(): raise ValueError('Release already exists; never overwrite it.')
    with tarfile.open(bundle, 'r:gz') as archive:
        members = archive.getmembers()
        for item in members:
            path = Path(item.name)
            if (path.is_absolute() or '..' in path.parts or not item.isfile()
                    or any(p in {'.venv', 'node_modules', '.runtime', '.DS_Store'} or p.startswith('.env') for p in path.parts)):
                raise ValueError('Unsafe deployment bundle member.')
        release.mkdir(mode=0o700, parents=True)
        archive.extractall(release, filter='data')
    manifest = json.loads((release / 'release.json').read_text())
    actual = {p.relative_to(release).as_posix() for p in release.rglob('*') if p.is_file()} - {'release.json'}
    if actual != set(manifest['files']): raise ValueError('Deployment file list mismatch.')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((release / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Deployment file hash mismatch.')
    if release.name != manifest['commit']: raise ValueError('Release name must match committed version.')
    return manifest


def install(root, bundle, commit, python, wheels=None):
    os.umask(0o077)
    root = root.absolute()
    if (not re.fullmatch('[0-9a-f]{40}', commit) or root.is_symlink() or root.resolve() != root
            or not root.is_relative_to(Path.home() / 'apps')):
        raise ValueError('Use this user apps directory, without a root symlink.')
    for name in ('releases', 'shared', 'shared/runtime/f01', 'shared/logs', 'backups'):
        (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    release = root / 'releases' / commit
    manifest = verified_extract(bundle, release)
    environment = release / 'backend/.venv'
    subprocess.run([str(python), '-m', 'venv', str(environment)], check=True)
    interpreter = environment / 'bin/python'
    command = [str(interpreter), '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(release / 'backend/requirements-runtime.lock')]
    command += ['--no-index', '--find-links', str(wheels)] if wheels else ['--index-url', 'https://pypi.org/simple']
    subprocess.run(command, check=True)
    # A release-local source path avoids unpinned build tools and retains the existing REPOSITORY_ROOT contract.
    site = Path(subprocess.check_output([str(interpreter), '-c', 'import sysconfig;print(sysconfig.get_paths()["purelib"])'], text=True).strip())
    (site / 'aigc_workbench_release.pth').write_text(str(release / 'backend/src') + '\n')
    subprocess.run([str(interpreter), '-m', 'pip', 'check'], check=True)
    data = root / 'shared/runtime/f01'
    for directory in ('image_versions', 'c01/snapshot_images'):
        (data / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (data / 'workbench.sqlite3').exists():
        subprocess.run([str(interpreter), '-c',
            'from pathlib import Path;from aigc_workbench.persistence import SQLiteTaskStore;'
            'from aigc_workbench.file_storage import LocalImageFiles;'
            'p=Path(__import__("sys").argv[1]);SQLiteTaskStore(p/"workbench.sqlite3").initialize();LocalImageFiles(p).initialize()', str(data)], check=True)
    config_path = root / 'shared/pilot.json'
    if not config_path.exists():
        config = json.loads((release / 'deploy/pilot.example.json').read_text())
        config.update(runtime_root=str(root / 'shared/runtime'), data_dir=str(data),
                      frontend_dist=str(root / 'current/frontend/dist'))
        with config_path.open('x') as stream: json.dump(config, stream, indent=2)
        config_path.chmod(0o600)
    else:
        if json.loads(config_path.read_text()).get('enable_real_ai') is not False:
            raise ValueError('This deployment requires real AI disabled.')
    (root / 'shared/installed-release.json').write_text(json.dumps({'commit': manifest['commit'], 'real_ai': False}, indent=2))
    # Does not switch current or start launchd. Those are explicit pilotctl operations.
    return str(release)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True); parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--commit', required=True); parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--wheels', type=Path)
    args = parser.parse_args()
    print(install(args.root, args.bundle, args.commit, args.python, args.wheels))
