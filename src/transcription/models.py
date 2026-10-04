import hashlib
import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import requests
from utils.types import AppError

CATALOG = json.loads((Path(__file__).parent / 'catalog.json').read_text('utf-8'))
log = logging.getLogger(__name__)


class ModelManager:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._busy = set()
        self._lock = threading.Lock()

    def path(self, name):
        if name not in CATALOG:
            raise AppError('Unknown Whisper model. Select a model from the list.')
        return self.directory / CATALOG[name]['file']

    def installed(self, name):
        path = self.path(name)
        return path.is_file() and path.stat().st_size == CATALOG[name]['size']

    def busy(self, name):
        with self._lock:
            return name in self._busy

    @contextmanager
    def lease(self, name):
        with self._lock:
            if name in self._busy:
                raise AppError('This model is already in use. Wait for the current operation.')
            self._busy.add(name)
        try:
            yield
        finally:
            with self._lock:
                self._busy.discard(name)

    def delete(self, name):
        with self._lock:
            if name in self._busy:
                raise AppError('This model is currently in use and cannot be deleted.')
            self.path(name).unlink(missing_ok=True)
            self.path(name).with_suffix('.bin.part').unlink(missing_ok=True)

    def verify(self, name, cancel, progress=None):
        path = self.path(name)
        if not self.installed(name):
            return False
        digest = hashlib.sha256()
        size = path.stat().st_size
        done = 0
        with path.open('rb') as stream:
            while block := stream.read(4 * 1024 * 1024):
                cancel.check()
                digest.update(block)
                done += len(block)
                if progress:
                    progress({'stage': 'Verifying model', 'fraction': done / size})
        return digest.hexdigest() == CATALOG[name]['sha256']

    def ensure(self, name, cancel, progress):
        if self.verify(name, cancel, progress):
            return self.path(name)
        # A full hash is checked on every use, including offline use.
        info = CATALOG[name]
        target = self.path(name)
        partial = target.with_suffix('.bin.part')
        start = time.monotonic()
        digest = hashlib.sha256()
        done = 0
        try:
            cancel.check()
            with requests.get(info['url'], stream=True, timeout=(10, 5)) as response:
                response.raise_for_status()
                total = info['size']
                with partial.open('wb') as output:
                    for block in response.iter_content(256 * 1024):
                        cancel.check()
                        if not block:
                            continue
                        output.write(block)
                        digest.update(block)
                        done += len(block)
                        progress({'stage': f'Downloading {name}', 'fraction': min(done / total, 1),
                                  'downloaded': done, 'total': total, 'speed': done / max(time.monotonic() - start, .01)})
            cancel.check()
            if done != info['size'] or digest.hexdigest() != info['sha256']:
                raise AppError('Model download failed integrity verification. Please try the download again.')
            partial.replace(target)
            return target
        except requests.RequestException as exc:
            log.exception('Model download failed')
            raise AppError('Could not download the selected model. Check your connection. Installed verified models work offline.') from exc
        except OSError as exc:
            raise AppError(f'Cannot save model in {self.directory}. Check free disk space and write permissions.') from exc
        finally:
            partial.unlink(missing_ok=True)
