"""Server-local, stopped-worker backup/restore. No HTTP endpoint, credentials, migration or model calls."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
from uuid import UUID

from .application import TaskService
from .c01_context import C01Error
from .c01_model_store import C01ModelStore
from .c01_persistence import C01EvidenceStore
from .c06_candidates import CandidateStore
from .file_storage import LocalImageFiles
from .image_review import ReviewStore
from .persistence import SQLiteTaskStore


def digest(path):
    h = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def relative_path(value):
    p = PurePosixPath(value)
    if p.is_absolute() or not p.parts or any(part in {'.', '..'} for part in p.parts) or '\\' in value:
        raise ValueError('Invalid archive-relative path.')
    return Path(*p.parts)


def inventory(data_dir):
    """Check every persisted business identity, image, snapshot, review and model record without mutation."""
    database = SQLiteTaskStore(data_dir / 'workbench.sqlite3')
    with database.connection() as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database integrity check failed.')
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        table_hashes = {}
        for table in tables:
            # Table names come from SQLite, not a path or HTTP request.
            rows = [json.dumps(tuple(r), ensure_ascii=False, separators=(',', ':')) for r in db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]
            table_hashes[table] = {'rows': len(rows), 'sha256': sha256('\n'.join(sorted(rows)).encode()).hexdigest()}
        assets = [dict(r) for r in db.execute('SELECT id,task_id FROM assets')]
        snapshots = [r['id'] for r in db.execute('SELECT id FROM c01_snapshots')]
        models = [r['id'] for r in db.execute('SELECT id FROM c01_model_runs')]
        versions = [dict(r) for r in db.execute('SELECT id,task_id,slot_id FROM image_versions')]
        slots = [dict(r) for r in db.execute('SELECT id,task_id FROM image_slots')]
        test_runs = [r['id'] for r in db.execute('SELECT id FROM c01_runs')]
        schema = db.execute('PRAGMA user_version').fetchone()[0]
    tasks = TaskService(database, LocalImageFiles(data_dir))
    for asset in assets:
        tasks.read_asset(UUID(asset['task_id']), UUID(asset['id']))
    evidence = C01EvidenceStore(database, data_dir)
    for identity in snapshots:
        evidence.read_snapshot_image(evidence.load_snapshot(UUID(identity)))
    for identity in test_runs:
        evidence.inspect_run(UUID(identity))
    for identity in models:
        C01ModelStore(database).inspect(UUID(identity))
    candidates = CandidateStore(database, data_dir)
    for v in versions:
        candidates.read_for_slot(UUID(v['task_id']), UUID(v['slot_id']), UUID(v['id']))
    # An integrity inspection may contain explicitly labelled synthetic reviews; it never approves them.
    reviews = ReviewStore(database, candidates, test_mode=True)
    for slot in slots:
        reviews.list(UUID(slot['task_id']), UUID(slot['id']))
        current = reviews.current(UUID(slot['task_id']), UUID(slot['id']))
        if current:
            reviews.delivery(UUID(slot['task_id']), UUID(slot['id']), UUID(current['id']))
    return {'schema': schema, 'tables': table_hashes, 'assets_checked': len(assets),
            'snapshots_checked': len(snapshots), 'model_runs_checked': len(models), 'versions_checked': len(versions)}


@contextmanager
def stopped_worker(data_dir):
    # The application always takes this lock before it can dispatch. Never stop/kill it from the backup tool.
    fd = os.open(data_dir / 'c06-dispatch.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Stop the service before backup/restore inspection; a worker still owns the runtime.') from None
        yield


def copy_file(source, target):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with source.open('rb') as original, os.fdopen(fd, 'wb') as output:
        shutil.copyfileobj(original, output)
        output.flush(); os.fsync(output.fileno())
    if stat.S_IMODE(source.stat().st_mode) == 0o400:
        target.chmod(0o400)


def create(runtime_root, data_dir, destination):
    if runtime_root.is_symlink() or data_dir.is_symlink():
        raise ValueError('Runtime/data symlinks are not supported.')
    root, data_dir, destination = runtime_root.resolve(), data_dir.resolve(), destination.absolute()
    relative_data = data_dir.relative_to(root)
    if destination.resolve().is_relative_to(root) or destination.exists():
        raise ValueError('Backup destination must be new and outside the runtime root.')
    if not (data_dir / 'workbench.sqlite3').is_file():
        raise ValueError('Existing business database required; never initialize a replacement.')
    with stopped_worker(data_dir), sqlite3.connect(data_dir / 'workbench.sqlite3') as frozen:
        frozen.execute('BEGIN IMMEDIATE')  # Freeze DB writers while copying all immutable files and the DB.
        destination.mkdir(mode=0o700, parents=True)
        files_dir = destination / 'runtime'; files_dir.mkdir(mode=0o700)
        excluded = []
        db_relative = relative_data / 'workbench.sqlite3'
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ValueError('Symlinks are not permitted in a runtime archive.')
            if path.is_dir():
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('Only regular runtime files can be backed up.')
            rel = path.relative_to(root)
            if (path.name.startswith('.env') or path.suffix in {'.env', '.pem', '.key', '.lock'}
                    or path.name.endswith(('-wal', '-shm', '-journal')) or path.name == '.DS_Store'):
                excluded.append(str(rel)); continue
            if rel != db_relative:
                copy_file(path, files_dir / rel)
        db_copy = files_dir / db_relative
        db_copy.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect((data_dir / 'workbench.sqlite3').as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(db_copy) as target:
                source.backup(target)
        db_copy.chmod(0o600)
        state = inventory(files_dir / relative_data)
        manifest = {'format': 'aigc-pilot-runtime-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                    'source_runtime_root': str(root), 'data_relative': str(relative_data), 'excluded': excluded,
                    'inventory': state, 'files': {str(p.relative_to(files_dir)): {'size': p.stat().st_size, 'sha256': digest(p)}
                                                 for p in sorted(files_dir.rglob('*')) if p.is_file()}}
        content = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        fd = os.open(destination / 'manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(content); output.flush(); os.fsync(output.fileno())
        frozen.rollback()
    return verify(destination)


def verify(archive):
    if archive.is_symlink() or any(p.is_symlink() for p in archive.rglob('*')):
        raise ValueError('Archive symlinks are forbidden.')
    manifest = json.loads((archive / 'manifest.json').read_text())
    if manifest['format'] != 'aigc-pilot-runtime-v1':
        raise ValueError('Unsupported backup format.')
    files_dir = archive / 'runtime'
    expected = set(manifest['files'])
    actual = {str(p.relative_to(files_dir)) for p in files_dir.rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('Backup file list mismatch.')
    for name, identity in manifest['files'].items():
        path = files_dir / relative_path(name)
        if path.stat().st_size != identity['size'] or digest(path) != identity['sha256']:
            raise ValueError('Backup file hash mismatch.')
    state = inventory(files_dir / relative_path(manifest['data_relative']))
    if state != manifest['inventory']:
        raise ValueError('Backup database/relationship inventory mismatch.')
    return manifest


def restore(archive, destination):
    manifest = verify(archive)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(archive.resolve()):
        raise ValueError('Restore requires a new, absent directory; never overwrite a running or existing store.')
    destination.mkdir(mode=0o700, parents=True)
    for name in manifest['files']:
        relative = relative_path(name)
        copy_file(archive / 'runtime' / relative, destination / relative)
    state = inventory(destination / relative_path(manifest['data_relative']))
    if state != manifest['inventory']:
        raise ValueError('Restored identity inventory mismatch.')
    return {'runtime_root': str(destination), 'data_dir': str(destination / manifest['data_relative']),
            'archived_runtime_root': manifest['source_runtime_root'], 'inventory': state}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create_cmd = commands.add_parser('create')
    create_cmd.add_argument('--runtime-root', type=Path, required=True)
    create_cmd.add_argument('--data-dir', type=Path, required=True)
    create_cmd.add_argument('--destination', type=Path, required=True)
    for name in ('verify', 'restore'):
        command = commands.add_parser(name); command.add_argument('--archive', type=Path, required=True)
        if name == 'restore': command.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'create': result = create(args.runtime_root, args.data_dir, args.destination)
        elif args.command == 'restore': result = restore(args.archive, args.destination)
        else: result = verify(args.archive)
        print(json.dumps({'status': 'VERIFIED', 'inventory': result['inventory'],
                          **{key: result[key] for key in ('runtime_root', 'data_dir', 'archived_runtime_root') if key in result}}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error, C01Error):
        print(json.dumps({'status': 'FAILED', 'message': 'Backup/restore failed; existing runtime retained. Check service stop, paths and archive integrity.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
