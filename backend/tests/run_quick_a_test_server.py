"""Explicit local TEST_ONLY browser fixture server. Never imports user credentials or sends model traffic."""
import argparse
import os
from pathlib import Path

import uvicorn

from quick_a_adapters import UnderstandingSubstitute, ImageSubstitute, source_png
from aigc_workbench.app import create_app
from aigc_workbench.c01_model import OpenAIResponsesTransport
from aigc_workbench.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8033)
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    marker = directory / 'TEST_ONLY'
    if directory.exists() and not marker.is_file():
        parser.error('Refusing an existing unmarked directory; choose a fresh isolated TEST_ONLY data directory.')
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    marker.write_text('TEST_ONLY / NO REAL MODEL CALLS / NOT FOR OPERATIONS\n')
    for name, color in [('product.png', 'red'), ('reference.png', 'blue')]:
        path = directory / name
        if not path.exists(): path.write_bytes(source_png(color))
    os.environ['OPENAI_API_KEY'] = 'TEST_ONLY_NOT_A_REAL_CREDENTIAL'
    os.environ.pop('OPENAI_BASE_URL', None)
    os.environ['AIGC_ENABLE_C06_REAL'] = '1'; os.environ['AIGC_ENABLE_C07_REAL'] = '1'
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_ONLY: real provider transport is forbidden')
    OpenAIResponsesTransport._request = forbidden
    settings = Settings(directory, enable_c01_testing=True, enable_c01_real=True, enable_workspace_generation=True,
                        allowed_origins=(f'http://127.0.0.1:{args.port + 1}',))
    app = create_app(settings, generation_transport=ImageSubstitute(delay=2), understanding_transport=UnderstandingSubstitute(delay=2))
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__': main()
