"""Package the verified static artifact and its isolated origin controls only."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import io


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    subprocess.run(['node', 'frontend/scripts/verify-showcase.mjs'], cwd=repo, check=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True).strip():
        raise SystemExit('Commit the deployment sources before packaging')
    release = commit + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    source = repo / 'frontend/dist-showcase'
    entries = {str(f.relative_to(source)): f.read_bytes() for f in source.rglob('*') if f.is_file()}
    manifest = [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for name, data in sorted(entries.items())]
    artifact = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    files = {'site/' + name: data for name, data in entries.items()}
    files.update({name: (Path(__file__).parent / name).read_bytes() for name in ('server.py', 'control.py')})
    files['manifest.json'] = json.dumps(manifest, indent=2).encode()
    files['release.json'] = json.dumps({'release_id': release, 'source_commit': commit,
                                      'artifact_sha256': artifact}, indent=2).encode()
    with args.output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as bundle:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o444
            bundle.addfile(info, io.BytesIO(data))
    print(json.dumps({'release_id': release, 'archive': str(args.output),
                      'archive_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      'artifact_sha256': artifact, 'public_files': len(entries)}))
