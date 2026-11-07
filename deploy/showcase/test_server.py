import contextlib
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import tarfile

from server import create_server, load_site
from control import install


class ShowcaseOriginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.content = {'index.html': b'<h1>Original showcase</h1>', '404.html': b'Not found',
                        'robots.txt': b'User-agent: *\nDisallow: /\n',
                        'assets/main.js': b'console.log("public only")',
                        '_headers': Path(__file__).resolve().parents[2].joinpath('frontend/showcase/_headers').read_bytes()}
        for name, data in self.content.items():
            path = self.root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(data)
        self.manifest = [{'path': n, 'sha256': hashlib.sha256(d).hexdigest()} for n, d in self.content.items()]
        self.server = create_server(self.root, self.manifest, 'test-release', port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.logs = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.logs)
        self.redirect.__enter__()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.redirect.__exit__(None, None, None)
        self.temporary.cleanup()

    def request(self, path, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        status, values, data = response.status, dict(response.headers), response.read()
        connection.close()
        return status, values, data

    def test_static_bytes_headers_head_and_etag(self):
        status, headers, data = self.request('/')
        self.assertEqual((status, data), (200, self.content['index.html']))
        self.assertEqual(headers['X-Showcase-Origin'], 'mac-mini')
        self.assertEqual(headers['X-Showcase-Release'], 'test-release')
        self.assertIn("connect-src 'none'", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIn('noindex', headers['X-Robots-Tag'])
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(self.request('/', 'HEAD')[2], b'')
        self.assertEqual(self.request('/', headers={'If-None-Match': headers['ETag']})[0], 304)
        self.assertIn('immutable', self.request('/assets/main.js')[1]['Cache-Control'])
        self.assertEqual(self.request('/index.html')[2], data)

    def test_private_and_spa_unknown_paths_never_fall_back_to_application(self):
        (self.root / 'private.sqlite').write_bytes(b'never serve me')
        for path in ['/api/tasks', '/api/generation', '/.runtime/', '/.env.local', '/evidence/',
                     '/private.sqlite', '/_headers', '/assets/', '/unknown-route']:
            self.assertEqual(self.request(path)[0], 404, path)
        for path in ['/../private.sqlite', '/%2e%2e/private.sqlite', '/assets/../../private.sqlite', '/%5cetc/passwd', '/%00', '//index.html']:
            self.assertEqual(self.request(path)[0], 400, path)

    def test_all_write_methods_rejected_and_no_source_changes(self):
        before = {p: (self.root / p).read_bytes() for p in self.content}
        for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT']:
            status, headers, _ = self.request('/api/tasks', method)
            self.assertEqual(status, 405)
            self.assertEqual(headers['Allow'], 'GET, HEAD')
        self.assertEqual(before, {p: (self.root / p).read_bytes() for p in self.content})

    def test_host_https_and_safe_request_correlation(self):
        self.assertEqual(self.request('/', headers={'Host': 'unexpected.example'})[0], 421)
        status, headers, _ = self.request('/?probe=public', headers={
            'Host': 'studio-demo.enterprise-ai-workspace.com', 'X-Forwarded-Proto': 'http'})
        self.assertEqual(status, 308)
        self.assertEqual(headers['Location'], 'https://studio-demo.enterprise-ai-workspace.com/?probe=public')
        _, headers, _ = self.request('/?secret=not-for-logs', headers={'Authorization': 'must-not-log'})
        self.assertNotIn('not-for-logs', self.logs.getvalue())
        self.assertNotIn('must-not-log', self.logs.getvalue())
        rows = [json.loads(line) for line in self.logs.getvalue().splitlines()]
        self.assertIn(headers['X-Showcase-Request-ID'], [r['request_id'] for r in rows])

    def test_startup_fails_closed_for_changed_bytes_and_symlinks(self):
        asset = self.root / 'assets/main.js'
        asset.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            load_site(self.root, self.manifest)
        asset.unlink()
        asset.symlink_to(self.root / 'index.html')
        with self.assertRaises(ValueError):
            load_site(self.root, self.manifest)


class PackageSafetyTests(unittest.TestCase):
    def test_refuses_symlink_archive_and_wrong_transfer_hash_without_writing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                link = tarfile.TarInfo('site/index.html')
                link.type = tarfile.SYMTYPE
                link.linkname = '/etc/passwd'
                bundle.addfile(link)
            target = Path(directory) / 'must-not-exist'
            for digest in ['invalid', hashlib.sha256(archive.read_bytes()).hexdigest()]:
                with self.assertRaises(ValueError):
                    install(target, archive, digest)
                self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
