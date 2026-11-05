"""Run one private LAN Pilot from an owner-only JSON configuration. This command never installs or deploys."""
import argparse
import json
import logging
import os
from pathlib import Path
import stat
from ipaddress import ip_address

from .app import create_app
from .config import Settings


def settings_from_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384:
            raise ValueError('Pilot config must be a private owner-only 0600 regular file.')
        config = json.load(stream)
    allowed = {'bind_host', 'port', 'hosts', 'networks', 'runtime_root', 'data_dir', 'archived_runtime_root', 'frontend_dist', 'key_file', 'enable_real_ai'}
    if set(config) - allowed or not config.get('bind_host') or type(config.get('port')) is not int:
        raise ValueError('Pilot config fields are invalid.')
    enabled = config.get('enable_real_ai', False)
    if type(enabled) is not bool:
        raise ValueError('enable_real_ai must be an explicit boolean.')
    for key in ('runtime_root', 'data_dir', 'frontend_dist') + (('key_file',) if enabled else ()):
        if not Path(config[key]).is_absolute():
            raise ValueError('Pilot paths must be explicit absolute server paths.')
    if config['bind_host'] not in config['hosts'] or not 1024 <= config['port'] <= 65535:
        raise ValueError('Pilot bind address must be an explicitly allowed Host with an unprivileged port.')
    address = ip_address(config['bind_host'])
    if address.is_unspecified or address.is_multicast or not (address.is_private or address.is_loopback):
        raise ValueError('Pilot must bind one explicit internal interface, not all interfaces or a public address.')
    if enabled:
        os.environ['AIGC_PRIVATE_KEY_FILE'] = config['key_file']
    else:
        os.environ.pop('AIGC_PRIVATE_KEY_FILE', None)
    # These are server execution settings, not an operator permission or a fee approval.
    os.environ['AIGC_ENABLE_C06_REAL'] = '1' if enabled else '0'
    os.environ['AIGC_ENABLE_C07_REAL'] = '1' if enabled else '0'
    os.environ['AIGC_ENABLE_C01_REAL'] = '1' if enabled else '0'
    settings = Settings(Path(config['data_dir']), allowed_origins=(), environment='pilot', enable_c01_testing=False,
        enable_c01_real=enabled, enable_workspace_generation=enabled, pilot_hosts=tuple(config['hosts']),
        pilot_networks=tuple(config['networks']), frontend_dist=Path(config['frontend_dist']),
        runtime_root=Path(config['runtime_root']), archived_runtime_root=Path(config['archived_runtime_root']) if config.get('archived_runtime_root') else None,
        model_service_disabled_reason=None if enabled else '模型服务暂不可用：本部署已从服务端禁用真实 AI 调用；素材保存与查询仍可使用。')
    return settings, config['bind_host'], config['port']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        settings, host, port = settings_from_file(args.config)
        app = create_app(settings)
    except (OSError, KeyError, TypeError, ValueError):
        logging.error('Pilot configuration rejected; private config, paths, production build and LAN scope required.')
        return 1
    import uvicorn
    uvicorn.run(app, host=host, port=port, workers=1, proxy_headers=False, timeout_graceful_shutdown=320)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
