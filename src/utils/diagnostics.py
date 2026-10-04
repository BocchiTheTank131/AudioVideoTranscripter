import platform
import sys
import subprocess
from pathlib import Path
import psutil
from .version import VERSION, WHISPER_VERSION
from .process import CREATE_FLAGS
from media.ffmpeg import find_tool
from .types import AppError


def diagnostics(paths, backends, include_ffmpeg=True):
    text = [f'Local Transcriber {VERSION}', f'OS: {platform.platform()}', f'Python/runtime: {sys.version.split()[0]}',
            f'whisper.cpp: {WHISPER_VERSION}', f'RAM: {psutil.virtual_memory().total / 1024**3:.1f} GB']
    if include_ffmpeg:
        try:
            output = subprocess.run([find_tool('ffmpeg'), '-version'], capture_output=True, text=True, timeout=5, creationflags=CREATE_FLAGS)
            text.append(output.stdout.splitlines()[0])
        except (OSError, RuntimeError, AppError, IndexError, subprocess.TimeoutExpired):
            text.append('FFmpeg: unavailable')
    else:
        text.append('FFmpeg version: refresh with Copy diagnostics')
    for name, backend in backends.items():
        text.append(f'{name}: ' + ('available' if backend.available else 'unavailable'))
        for device in backend.devices:
            text.append(f"  {device.get('name', 'Unknown')} · memory {device.get('memory_total', 0) / 1024**3:.1f} GB")
    for label, directory in [('Models', paths.models), ('Temp', paths.cache), ('Logs', paths.logs)]:
        path = str(directory).replace(str(paths.root), '%APPDATA%/LocalTranscriber').replace(str(Path.home()), '%USERPROFILE%')
        text.append(f'{label}: {path}')
    return '\n'.join(text)
