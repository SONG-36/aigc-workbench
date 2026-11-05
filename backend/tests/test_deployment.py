"""Deployment defaults, server-side AI disablement, bundle safety and release rollback."""
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import tarfile
from uuid import uuid4

import pytest

from test_creation_decision import source
from test_c06 import generation
from test_c07_review import revision
from test_pilot import pilot, client_for
from test_workspace_generation import route
from aigc_workbench.pilot_server import settings_from_file

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy' / (name + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def private_config(pilot, tmp_path, **extra):
    settings, *_ = pilot
    config = {'bind_host': '127.0.0.1', 'port': 8030, 'hosts': ['127.0.0.1', 'localhost'],
              'networks': ['127.0.0.1/32'], 'runtime_root': str(settings.runtime_root),
              'data_dir': str(settings.data_dir), 'frontend_dist': str(settings.frontend_dist), **extra}
    path = tmp_path / 'private-pilot.json'
    path.write_text(json.dumps(config)); path.chmod(0o600)
    return path


def test_disabled_server_overrides_inherited_flags_and_never_loads_credentials_or_calls_models(pilot, tmp_path, monkeypatch):
    _, g, service, v1 = pilot
    for name in ('AIGC_ENABLE_C01_REAL', 'AIGC_ENABLE_C06_REAL', 'AIGC_ENABLE_C07_REAL'):
        monkeypatch.setenv(name, '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'TEST_ONLY_INHERITED_NOT_A_REAL_KEY')
    def forbidden(*args, **kwargs): pytest.fail('Disabled deployment must not read credentials or call a provider')
    monkeypatch.setattr('aigc_workbench.generation_application.load_openai_credentials', forbidden)
    monkeypatch.setattr('aigc_workbench.c01_model.OpenAIResponsesTransport._request', forbidden)
    settings, host, port = settings_from_file(private_config(pilot, tmp_path))
    assert not settings.enable_c01_real and not settings.enable_workspace_generation
    assert host == '127.0.0.1' and port == 8030
    path = route(g)
    with client_for(settings, peer='127.0.0.1', host='http://127.0.0.1:18030') as client:
        for _ in range(2):
            result = client.get(path + '/workspace').json()
            assert not result['readiness']['can_generate'] and '模型服务暂不可用' in result['readiness']['reason']
            assert not result['can_request_revision']
        assert client.post(path + '/generations', json={'operation_id': str(uuid4()), 'quote_token': 'a' * 64}).status_code == 503
        assert client.post(path + '/versions/' + v1['image_version_id'] + '/revision-quote', json={'revision_text': 'not sent'}).status_code == 409
        assert client.post(path + '/revisions', json={'operation_id': str(uuid4()), 'snapshot_id': g[1]['id'],
                                                     'quote_token': 'a' * 64}).status_code == 409
        assert client.get('/backend/.env.local').status_code == 404
    assert not (tmp_path / 'openai.env').exists()


def test_real_ai_requires_explicit_boolean_and_private_key_path(pilot, tmp_path):
    with pytest.raises(ValueError): settings_from_file(private_config(pilot, tmp_path, enable_real_ai='true'))
    with pytest.raises(KeyError): settings_from_file(private_config(pilot, tmp_path, enable_real_ai=True))
    configured, *_ = settings_from_file(private_config(pilot, tmp_path, enable_real_ai=True, key_file=str(tmp_path / 'absent.env')))
    assert configured.enable_c01_real  # Preparation only; this test never starts or invokes a provider.


def test_runtime_lock_pins_are_a_subset_of_existing_locked_dependencies():
    def pins(path):
        return {line.lower().replace('_', '-').strip() for line in path.read_text().splitlines() if line and not line.startswith('#')}
    assert pins(ROOT / 'backend/requirements-runtime.lock') <= pins(ROOT / 'backend/requirements-dev.lock')


@pytest.mark.parametrize('name', ['../escape', '/tmp/escape', 'backend/.env.local', 'backend/.venv/file', 'node_modules/file', '.runtime/db'])
def test_installer_rejects_private_or_escaping_bundle_members(tmp_path, name):
    archive = tmp_path / 'bad.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        item = tarfile.TarInfo(name); item.size = 1; stream.addfile(item, BytesIO(b'x'))
    with pytest.raises(ValueError): module('install_release').verified_extract(archive, tmp_path / 'release')


def test_verified_release_checks_every_hash_and_never_overwrites(tmp_path):
    commit = 'a' * 40; archive = tmp_path / 'release.tar.gz'; content = b'production index'
    manifest = json.dumps({'commit': commit, 'files': {'frontend/dist/index.html': hashlib.sha256(content).hexdigest()}}).encode()
    with tarfile.open(archive, 'w:gz') as stream:
        for name, data in [('release.json', manifest), ('frontend/dist/index.html', content)]:
            info = tarfile.TarInfo(name); info.size = len(data); stream.addfile(info, BytesIO(data))
    installer = module('install_release'); target = tmp_path / commit
    assert installer.verified_extract(archive, target)['commit'] == commit
    with pytest.raises(ValueError): installer.verified_extract(archive, target)


def test_release_switch_and_rollback_preserve_shared_data_and_reject_directories(tmp_path):
    controller = module('pilotctl'); root = tmp_path / 'pilot'; root.mkdir()
    data = root / 'shared/runtime'; data.mkdir(parents=True)
    (data / 'unchanged.txt').write_text('persisted business data')
    for commit in ('a' * 40, 'b' * 40):
        release = root / 'releases' / commit
        (release / 'backend/.venv/bin').mkdir(parents=True); (release / 'backend/.venv/bin/python').write_text('fixture')
        (release / 'frontend/dist').mkdir(parents=True); (release / 'frontend/dist/index.html').write_text('fixture')
        (release / 'release.json').write_text(json.dumps({'commit': commit}))
    for commit in ('a' * 40, 'b' * 40, 'a' * 40):
        controller.activate(root, commit)
        assert (root / 'current').resolve().name == commit
        assert (data / 'unchanged.txt').read_text() == 'persisted business data'
    (root / 'current').unlink(); (root / 'current').mkdir()
    with pytest.raises(ValueError): controller.activate(root, 'b' * 40)
