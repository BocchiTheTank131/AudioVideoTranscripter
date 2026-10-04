import os
import sys
from pathlib import Path
from platformdirs import user_data_dir


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))


class Paths:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get('LOCAL_TRANSCRIBER_HOME') or user_data_dir('LocalTranscriber', appauthor=False))
        self.models = self.root / 'models'
        self.cache = self.root / 'cache'
        self.logs = self.root / 'logs'
        self.backends = self.root / 'backends'
        for path in (self.root, self.models, self.cache, self.logs, self.backends):
            path.mkdir(parents=True, exist_ok=True)
        self.settings = self.root / 'settings.json'


def backend_roots(paths: Paths):
    roots = [paths.backends, resource_root() / 'backends']
    if getattr(sys, 'frozen', False):
        roots.append(Path(sys.executable).parent / 'backends')
    configured = os.environ.get('LOCAL_TRANSCRIBER_BACKENDS')
    if configured:
        roots.insert(0, Path(configured))
    return roots
