"""Single-origin production UI with explicit LAN peer/Host restrictions; no user authentication."""
from ipaddress import ip_address, ip_network
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def pilot_boundary(settings):
    if not settings.pilot_hosts or not settings.pilot_networks:
        raise ValueError('Pilot requires explicit allowed Hosts and trusted client networks.')
    networks = tuple(ip_network(value.strip(), strict=True) for value in settings.pilot_networks)
    if any(n.prefixlen == 0 or not (n.is_private or n.is_loopback) for n in networks):
        raise ValueError('Pilot networks must be explicit internal or loopback networks.')
    if any('*' in h or '/' in h or ':' in h.replace('::1', '') for h in settings.pilot_hosts):
        raise ValueError('Pilot Hosts must be explicit hostnames or IP addresses, without ports or wildcards.')
    dist = settings.frontend_dist
    if dist is None or dist.is_symlink() or not (dist / 'index.html').is_file() or not (dist / 'assets').is_dir():
        raise ValueError('Pilot requires an existing React production build.')
    if settings.runtime_root is None or not settings.data_dir.is_relative_to(settings.runtime_root):
        raise ValueError('Pilot runtime root must contain the data directory.')
    if (settings.runtime_root.is_symlink() or settings.data_dir.is_symlink() or settings.database_path.is_symlink()
            or not settings.database_path.is_file() or not settings.data_dir.resolve().is_relative_to(settings.runtime_root.resolve())):
        raise ValueError('Pilot needs an existing restored database in its private runtime; never create an empty substitute.')
    if dist.resolve().is_relative_to(settings.runtime_root.resolve()) or settings.runtime_root.resolve().is_relative_to(dist.resolve()):
        raise ValueError('Public frontend files and private runtime data must be separate.')
    return networks


def peer_allowed(host, networks):
    try:
        address = ip_address(host)
        if getattr(address, 'ipv4_mapped', None):
            address = address.ipv4_mapped
        return any(address in network for network in networks)
    except ValueError:
        return False


def attach_frontend(app, dist: Path):
    # Only Vite's built assets and the explicit client routes are public. No repository/data mount.
    app.mount('/assets', StaticFiles(directory=dist / 'assets', follow_symlink=False), name='frontend-assets')

    @app.get('/', include_in_schema=False)
    @app.get('/workspace/{task_id}', include_in_schema=False)
    def frontend(task_id: str | None = None):
        if task_id is not None and task_id != 'new':
            try:
                UUID(task_id)
            except ValueError:
                raise HTTPException(404)
        return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
