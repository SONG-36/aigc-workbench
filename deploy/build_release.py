"""Build an allowlisted release from committed source; never copy a developer working tree."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATHS = ('backend/src', 'backend/requirements-runtime.lock', 'backend/pyproject.toml', 'deploy',
         'docs/business/01_BUSINESS_MODES.md', 'docs/business/02_INTENT_AND_CONTEXT_RULES.md',
         'docs/business/04_IMAGE_CREATION_WORK_METHOD.md',
         'docs/business/architecture/T03_CONTEXT_ENGINEERING.md', 'docs/business/architecture/T05_AI_CAPABILITIES.md',
         'frontend/src', 'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html',
         'frontend/tsconfig.json', 'frontend/vite.config.ts')


def build(destination):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT):
        raise ValueError('Commit deployment changes before building.')
    if destination.exists(): raise ValueError('Refusing to overwrite an existing deployment bundle.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='release-', dir=destination.parent) as temporary:
        staging = Path(temporary)
        archive = staging / 'source.tar'
        subprocess.run(['git', 'archive', '--format=tar', '-o', str(archive), commit, '--', *PATHS], cwd=ROOT, check=True)
        source = staging / 'source'; source.mkdir()
        with tarfile.open(archive) as stream: stream.extractall(source, filter='data')
        # Build the exported commit, without local .env files or runtime files. Dependencies stay local.
        (source / 'frontend/node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith('VITE_') and k != 'OPENAI_API_KEY'}
        subprocess.run(['npm', 'run', 'build'], cwd=source / 'frontend', env=env, check=True)
        public = source / 'frontend/dist'
        manifest = {'format': 'aigc-workbench-release-v1', 'commit': commit,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'build_node': subprocess.check_output(['node', '--version'], text=True).strip(),
            'files': {}}
        members = []
        for prefix in ('backend', 'deploy', 'docs', 'frontend/dist'):
            for path in sorted((source / prefix).rglob('*')):
                if path.is_file():
                    relative = path.relative_to(source).as_posix()
                    if any(part in {'.env.local', '.runtime', '.venv', 'node_modules', '.DS_Store'} for part in path.relative_to(source).parts):
                        raise ValueError('Forbidden deployment member.')
                    manifest['files'][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
                    members.append(path)
        for name in ('package.json', 'package-lock.json'):
            path = source / 'frontend' / name
            manifest['files'][path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            members.append(path)
        if not (public / 'index.html').is_file(): raise ValueError('Production frontend build missing.')
        (source / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n')
        with tarfile.open(destination, 'x:gz') as output:
            for path in [*members, source / 'release.json']:
                output.add(path, arcname=path.relative_to(source), recursive=False)
    return commit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    commit = build(args.output.resolve())
    print(json.dumps({'commit': commit, 'bundle': str(args.output),
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))
