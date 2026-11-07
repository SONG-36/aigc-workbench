"""Read-only, manifest-bound Showcase origin. No application or database imports."""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import uuid
import threading


def load_site(root, manifest):
    root = Path(root).resolve(strict=True)
    files = {}
    for item in manifest:
        name = item['path']
        parts = PurePosixPath(name).parts
        if not parts or name.startswith('/') or any(p in ('.', '..') for p in parts) or '\\' in name:
            raise ValueError('Invalid manifest path')
        path = root
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise ValueError('Symlink in release')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Release hash mismatch: ' + name)
        if name in files:
            raise ValueError('Duplicate manifest path')
        files[name] = data
    for name in ('index.html', '404.html', '_headers', 'robots.txt'):
        if name not in files:
            raise ValueError('Missing required public file: ' + name)
    return files


def parse_headers(data):
    rules = []
    for line in data.decode('utf8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        if line.startswith('/'):
            rules.append((line.strip(), {}))
        else:
            if not rules or not line.startswith('  ') or ':' not in line:
                raise ValueError('Unsupported header rule')
            name, value = line.strip().split(':', 1)
            if name.lower() in ('content-length', 'connection', 'transfer-encoding', 'server'):
                raise ValueError('Reserved header')
            rules[-1][1][name] = value.strip()
    return rules


def create_server(root, manifest, release, host='127.0.0.1', port=8040):
    files = load_site(root, manifest)
    rules = parse_headers(files['_headers'])
    allowed_hosts = {'127.0.0.1', 'localhost', 'studio-demo.enterprise-ai-workspace.com'}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'ShowcaseStatic/1.0'
        sys_version = ''
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass  # Never log request headers, query strings or arbitrary server errors.

        def respond(self, status, path, body=b'', extra=None, head=False):
            request_id = str(uuid.uuid4())
            headers = {}
            for pattern, values in rules:
                if fnmatch.fnmatchcase(path, pattern):
                    headers.update(values)
            if status not in (200, 304):
                headers['Cache-Control'] = 'no-store'
            # Keep Cloudflare's automatic analytics/HTML rewriting out of this origin.
            headers['Cache-Control'] = headers.get('Cache-Control', 'public, max-age=0, must-revalidate') + ', no-transform'
            headers.update(extra or {})
            headers.update({'X-Showcase-Origin': 'mac-mini', 'X-Showcase-Release': release,
                            'X-Showcase-Request-ID': request_id, 'Connection': 'close'})
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.close_connection = True
            if not head and body:
                self.wfile.write(body)
            print(json.dumps({'request_id': request_id, 'release': release, 'method': self.command,
                              'path': path, 'status': status}), flush=True)

        def serve(self, head=False):
            try:
                if self.requestline.split()[1].startswith('//'):
                    raise ValueError('Ambiguous path')
                path = unquote(urlsplit(self.path).path, errors='strict')
                if not self.path.startswith('/') or any(c in path for c in ('\\', '\x00', '\r', '\n')):
                    raise ValueError('Bad path')
                if any(p in ('.', '..') for p in path.split('/')):
                    raise ValueError('Traversal')
            except (ValueError, UnicodeError):
                return self.respond(400, '/', head=head)
            hostname = self.headers.get('Host', '').split(':')[0].lower()
            if hostname not in allowed_hosts:
                return self.respond(421, path, head=head)
            if hostname == 'studio-demo.enterprise-ai-workspace.com' and self.headers.get('X-Forwarded-Proto') == 'http':
                return self.respond(308, path, extra={'Location': 'https://' + hostname + self.path}, head=head)
            name = 'index.html' if path in ('/', '/index.html') else path.lstrip('/')
            if name not in files or name == '_headers' or path.startswith('//'):
                return self.respond(404, path, files['404.html'], {'Content-Type': 'text/html; charset=utf-8'}, head)
            body = files[name]
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            content_type = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript',
                            '.json': 'application/json', '.txt': 'text/plain', '.svg': 'image/svg+xml',
                            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                            '.webp': 'image/webp'}.get(PurePosixPath(name).suffix, 'application/octet-stream')
            if content_type.startswith('text/') or name.endswith(('.js', '.json')):
                content_type += '; charset=utf-8'
            headers = {'Content-Type': content_type, 'ETag': etag}
            if self.headers.get('If-None-Match') == etag:
                return self.respond(304, path, extra=headers, head=True)
            self.respond(200, path, body, headers, head)

        def do_GET(self):
            self.serve()

        def do_HEAD(self):
            self.serve(head=True)

        def reject_write(self):
            self.respond(405, '/', extra={'Allow': 'GET, HEAD'})

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = reject_write

    class StaticServer(ThreadingHTTPServer):
        slots = threading.BoundedSemaphore(32)
        request_queue_size = 64

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(10)
            return connection, address

        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                request.close()
                return
            try:
                super().process_request(request, address)
            except Exception:
                self.slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()

    server = StaticServer((host, port), Handler)
    server.daemon_threads = True
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--release-file', required=True)
    parser.add_argument('--port', type=int, default=8040)
    args = parser.parse_args()
    release = json.loads(Path(args.release_file).read_text())['release_id']
    manifest = json.loads(Path(args.manifest).read_text())
    create_server(args.root, manifest, release, port=args.port).serve_forever()
