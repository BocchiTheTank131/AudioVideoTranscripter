"""Explicit installer for an isolated, application-managed optional environment."""
import hashlib
import io
import os
import logging
from pathlib import Path
import zipfile
import requests
from .types import AppError, Cancelled
from .process import run_stream

PACKAGES = {'diarization': ['pyannote.audio>=4,<5', 'huggingface_hub>=0.34,<2'],
            'keywords': ['keybert>=0.9,<1', 'sentence-transformers>=3,<6', 'huggingface_hub>=0.34,<2']}


def install(kind, paths, cancel, progress):
    if os.name != 'nt':
        raise AppError('Managed optional installation currently supports Windows x64. Use the manual environment field on this platform.')
    uv = paths.runtime / 'uv' / 'uv.exe'
    if not uv.exists():
        progress(dict(stage='Downloading optional environment installer', fraction=None))
        response = requests.get('https://api.github.com/repos/astral-sh/uv/releases/latest', timeout=(10, 10))
        response.raise_for_status()
        asset = next((a for a in response.json()['assets'] if a['name'] == 'uv-x86_64-pc-windows-msvc.zip'), None)
        if not asset or not asset.get('digest', '').startswith('sha256:'):
            raise AppError('The optional installer has no verifiable download digest. Use manual setup instead.')
        buffer = io.BytesIO()
        with requests.get(asset['browser_download_url'], stream=True, timeout=(10, 5)) as response:
            response.raise_for_status()
            for data in response.iter_content(65536):
                cancel.check()
                buffer.write(data)
                progress(dict(stage='Downloading optional installer', fraction=min(1, buffer.tell() / asset['size'])))
        cancel.check()
        if hashlib.sha256(buffer.getvalue()).hexdigest() != asset['digest'].split(':')[1]:
            raise AppError('Optional installer integrity verification failed.')
        with zipfile.ZipFile(buffer) as archive:
            name = next(n for n in archive.namelist() if Path(n).name == 'uv.exe')
            uv.parent.mkdir(parents=True, exist_ok=True)
            uv.write_bytes(archive.read(name))
    environment = paths.runtime / 'optional-env'
    python = environment / 'Scripts/python.exe'
    env = os.environ.copy()
    env['UV_PYTHON_INSTALL_DIR'] = str(paths.runtime / 'python')
    env['UV_CACHE_DIR'] = str(paths.cache / 'uv')
    progress(dict(stage='Preparing isolated Python 3.12 runtime', fraction=None))
    def line(value):
        progress(dict(stage=value[-250:], fraction=None))
    if not python.exists():
        installed = paths.runtime / 'python'
        candidates = list(installed.glob('cpython-3.12.*-windows-x86_64-none/python.exe'))
        if not candidates:
            try:
                run_stream([uv, 'python', 'install', '3.12', '--no-progress', '--no-bin', '--no-registry'], cancel, line, env)
            except Cancelled:
                raise
            except AppError:
                candidates = list(installed.glob('cpython-3.12.*-windows-x86_64-none/python.exe'))
                if not candidates:
                    raise
                logging.getLogger(__name__).warning('Python downloaded but uv version-link setup failed; verifying its explicit interpreter', exc_info=True)
                progress(dict(stage='Verifying downloaded Python after version-link setup error', fraction=None))
        candidates = list(installed.glob('cpython-3.12.*-windows-x86_64-none/python.exe'))
        if not candidates:
            raise AppError('Managed Python installation did not produce an interpreter. Use manual setup.')
        interpreter = candidates[-1]
        run_stream([interpreter, '-c', 'import sys; assert sys.version_info[:2] == (3,12)'], cancel, line, env)
        run_stream([uv, 'venv', '--python', interpreter, environment], cancel, line, env)
    progress(dict(stage=f'Installing {kind} dependencies; downloads may be several GB', fraction=None))
    run_stream([uv, 'pip', 'install', '--python', python, '--no-progress', *PACKAGES[kind]], cancel, line, env)
    # Import verification does not instantiate a pipeline or download AI weights.
    module = 'pyannote.audio' if kind == 'diarization' else 'keybert'
    run_stream([python, '-c', f'import importlib; importlib.import_module({module!r})'], cancel, line, env)
    return str(python)
