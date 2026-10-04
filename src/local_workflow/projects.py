import json
import math
from dataclasses import fields, asdict
from pathlib import Path
from utils.types import Transcript, Segment, AppError

FORMAT_VERSION = 1


def segment_from_dict(data):
    segment = Segment(**{f.name: data[f.name] for f in fields(Segment) if f.name in data})
    if not math.isfinite(segment.start + segment.end) or segment.start < 0 or segment.end < segment.start:
        raise AppError('Project contains invalid cue timing.')
    if not isinstance(segment.text, str):
        raise AppError('Project contains invalid transcript text.')
    return segment


def transcript_from_dict(data):
    known = {f.name: data[f.name] for f in fields(Transcript) if f.name in data}
    known['segments'] = [segment_from_dict(s) for s in data.get('segments', [])]
    known['raw_segments'] = [segment_from_dict(s) for s in data.get('raw_segments', [])]
    return Transcript(**known)


def save_project(path, transcript, settings=None, export_settings=None):
    path = Path(path)
    data = dict(format='LocalTranscriber', version=FORMAT_VERSION, transcript=asdict(transcript),
                settings=settings or {}, export_settings=export_settings or {})
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), 'utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_project(path):
    try:
        path = Path(path)
        if path.stat().st_size > 128 * 1024**2:
            raise AppError('This project exceeds the supported 128 MB project size.')
        data = json.loads(path.read_text('utf-8'))
        if data.get('format') != 'LocalTranscriber' or data.get('version') != FORMAT_VERSION:
            raise AppError('Unsupported Local Transcriber project format.')
        return transcript_from_dict(data['transcript']), data.get('settings', {}), data.get('export_settings', {})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AppError('Could not open the project. It may be damaged or unreadable.') from exc
