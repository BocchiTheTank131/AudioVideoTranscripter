import json
import os
from pathlib import Path
import shutil
import subprocess
from dataclasses import dataclass
from utils.paths import resource_root
from utils.process import CREATE_FLAGS, run_stream
from utils.types import AppError

EXTENSIONS = ('mp4', 'mkv', 'mov', 'avi', 'webm', 'm4v', 'mpeg', 'mpg', 'wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus', 'wma')


def find_tool(name):
    configured = os.environ.get(name.upper() + '_PATH')
    suffix = '.exe' if os.name == 'nt' else ''
    for path in [Path(configured) if configured else None, resource_root() / 'tools' / (name + suffix)]:
        if path and path.is_file():
            return str(path)
    found = shutil.which(name)
    if found:
        return found
    raise AppError(f'{name} is missing. Install FFmpeg (including ffprobe) and add its bin folder to PATH, or place both in the application tools folder.')


@dataclass
class AudioStream:
    index: int
    codec: str
    rate: int
    channels: int
    language: str = ''
    title: str = ''


@dataclass
class MediaInfo:
    path: str
    format: str
    size: int
    duration: float
    streams: list[AudioStream]


def parse_metadata(data, path):
    streams = []
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'audio':
            tags = stream.get('tags', {})
            streams.append(AudioStream(int(stream['index']), stream.get('codec_name', 'unknown'),
                                      int(stream.get('sample_rate', 0)), int(stream.get('channels', 0)),
                                      tags.get('language', ''), tags.get('title', '')))
    if not streams:
        raise AppError('This file has no audio track. Choose a video or audio file containing speech.')
    fmt = data.get('format', {})
    duration = float(fmt.get('duration') or max((float(s.get('duration', 0) or 0) for s in data.get('streams', [])), default=0))
    return MediaInfo(str(path), fmt.get('format_name', Path(path).suffix.lstrip('.')), int(fmt.get('size', 0)), duration, streams)


def inspect(path, cancel):
    path = Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise AppError('The input file is missing or empty. Choose a readable media file.')
    lines = []
    run_stream([find_tool('ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path], cancel, lines.append)
    try:
        return parse_metadata(json.loads('\n'.join(lines)), path)
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError('Could not read media metadata. The file may be corrupted or unsupported.') from exc


def normalize(info, stream, output, cancel, progress):
    if stream not in {s.index for s in info.streams}:
        raise AppError('The selected audio track no longer exists. Import the file again.')

    def on_line(line):
        if line.startswith('out_time_us='):
            try:
                seconds = max(0, int(line.split('=', 1)[1]) / 1_000_000)
            except ValueError:
                return
            progress({'stage': 'Preparing audio', 'fraction': min(seconds / info.duration, 1) if info.duration else None})
    run_stream([find_tool('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error', '-y', '-i', info.path,
                '-map', f'0:{stream}', '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                '-rf64', 'auto', '-progress', 'pipe:1', output], cancel, on_line)
    if not Path(output).is_file() or Path(output).stat().st_size <= 80:
        raise AppError('The selected audio track is empty.')
