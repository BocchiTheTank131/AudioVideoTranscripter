"""Explicit, cancellable downloads. Media and transcripts never enter a request."""
import fnmatch
import hashlib
import logging
from pathlib import Path
import time
import requests
from .types import AppError

REPOS = {'diarization': 'pyannote/speaker-diarization-community-1',
         'keywords': 'sentence-transformers/all-MiniLM-L6-v2'}
ESTIMATES = {'diarization': '~1 GB (repository size is checked before downloading)',
             'keywords': '~90 MB (small English embedding model)'}


def download_optional(kind, destination, token, cancel, progress):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    repo = REPOS[kind]
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    try:
        cancel.check()
        response = requests.get(f'https://huggingface.co/api/models/{repo}?blobs=true', headers=headers, timeout=(10, 5))
        response.raise_for_status()
        info = response.json()
        revision = info['sha']
        files = info['siblings']
        if kind == 'keywords':
            files = [f for f in files if not f['rfilename'].startswith(('onnx/', 'openvino/', 'tf_', 'rust_', 'flax_'))
                     and not f['rfilename'].endswith(('.h5', '.msgpack', '.bin'))]
        files = [f for f in files if f['rfilename'] != '.gitattributes']
        total = sum(f.get('size', f.get('lfs', {}).get('size', 0)) for f in files)
        done = 0
        start = time.monotonic()
        for file in files:
            cancel.check()
            name = file['rfilename']
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()):
                raise AppError('Invalid path in the model repository.')
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + '.part')
            expected = file.get('size', file.get('lfs', {}).get('size', 0))
            digest = hashlib.sha256() if file.get('lfs') else hashlib.sha1()
            if not file.get('lfs'):
                digest.update(f'blob {expected}\0'.encode())
            received = 0
            try:
                with requests.get(f'https://huggingface.co/{repo}/resolve/{revision}/{name}', headers=headers,
                                  stream=True, timeout=(10, 5)) as reply:
                    reply.raise_for_status()
                    with partial.open('wb') as output:
                        for block in reply.iter_content(256 * 1024):
                            cancel.check()
                            if block:
                                output.write(block)
                                digest.update(block)
                                received += len(block)
                                progress({'stage': f'Downloading {kind}', 'fraction': (done + received) / total if total else None,
                                          'downloaded': done + received, 'total': total,
                                          'speed': (done + received) / max(.01, time.monotonic() - start)})
                checksum = file.get('lfs', {}).get('sha256') or file.get('blobId')
                if (expected and received != expected) or (checksum and digest.hexdigest() != checksum):
                    raise AppError(f'Download verification failed for {name}. Please retry.')
                partial.replace(path)
                done += received
            finally:
                partial.unlink(missing_ok=True)
        return str(root)
    except requests.RequestException as exc:
        # Do not log request headers or tokens.
        raise AppError('Model download failed. Check your connection and, for Community-1, accept the Hugging Face model conditions and enter a read token.') from exc
