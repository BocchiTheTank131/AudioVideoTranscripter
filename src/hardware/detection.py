import json
import logging
import os
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import psutil
from utils.paths import backend_roots
from utils.process import CREATE_FLAGS
from utils.types import AppError

LABELS = {'auto': 'Auto', 'cuda': 'NVIDIA CUDA', 'vulkan': 'Vulkan', 'hip': 'AMD HIP / ROCm', 'cpu': 'CPU'}
log = logging.getLogger(__name__)


def sensible_threads():
    physical = psutil.cpu_count(logical=False) or os.cpu_count() or 4
    return max(1, min(physical, max(1, (os.cpu_count() or 2) - 1), 16))


@dataclass
class BackendInfo:
    name: str
    executable: str = ''
    available: bool = False
    devices: list[dict] = field(default_factory=list)
    reason: str = 'Runtime is not installed'

    @property
    def device(self):
        candidates = [d for d in self.devices if d.get('gpu') == (self.name != 'cpu')]
        return candidates[0] if candidates else {}


def parse_probe(name, executable, data):
    if data.get('protocol') != 1 or data.get('backend') != name:
        return BackendInfo(name, str(executable), reason='Incompatible runtime protocol or backend identity')
    devices = data.get('devices', [])
    compatible = any(d.get('gpu') == (name != 'cpu') for d in devices)
    return BackendInfo(name, str(executable), compatible, devices,
                       '' if compatible else 'No compatible device detected by this runtime')


def detect(paths):
    result = {}
    exe = 'local-whisper.exe' if os.name == 'nt' else 'local-whisper'
    for name in ('cuda', 'vulkan', 'hip', 'cpu'):
        info = BackendInfo(name)
        for root in backend_roots(paths):
            file = root / name / exe
            if not file.is_file():
                continue
            try:
                proc = subprocess.run([str(file), '--probe'], capture_output=True, text=True, encoding='utf-8',
                                      errors='replace', timeout=20, creationflags=CREATE_FLAGS)
                if proc.returncode:
                    info = BackendInfo(name, str(file), reason='Runtime initialization failed; check driver and DLL dependencies')
                    log.warning('Probe failed: %s: %s', file, proc.stderr[-2000:])
                    continue
                info = parse_probe(name, file, json.loads(proc.stdout))
                if info.available:
                    break
            except (OSError, subprocess.TimeoutExpired, ValueError):
                log.exception('Backend probe failed: %s', file)
                info = BackendInfo(name, str(file), reason='Runtime did not respond correctly; check installation')
        result[name] = info
    return result


def choose(backends, requested='auto'):
    if requested != 'auto':
        info = backends.get(requested)
        if info and info.available:
            return info
        raise AppError(f'{LABELS.get(requested, requested)} is unavailable. Select Auto or CPU, or install a compatible runtime.')
    # CUDA requires a CUDA runtime that really enumerates a usable GPU.
    for name in ('cuda', 'vulkan', 'hip', 'cpu'):
        if backends.get(name) and backends[name].available:
            return backends[name]
    raise AppError('No Whisper runtime is installed. Run scripts/build_backend.py cpu once, or install the packaged CPU/Vulkan runtime. See README → Backend setup.')
