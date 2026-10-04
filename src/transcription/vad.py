import hashlib
import time
from pathlib import Path
import requests
from utils.types import AppError

NAME = 'ggml-silero-v6.2.0.bin'
SIZE = 885098
SHA256 = '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'
URL = 'https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/' + NAME


def valid(path):
    path = Path(path)
    return path.is_file() and path.stat().st_size == SIZE and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256


def download(directory, cancel, progress):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / NAME
    if valid(target):
        return str(target)
    part = target.with_suffix('.part')
    done, began = 0, time.monotonic()
    try:
        with requests.get(URL, stream=True, timeout=(10, 5)) as response:
            response.raise_for_status()
            with part.open('wb') as stream:
                for data in response.iter_content(65536):
                    cancel.check()
                    stream.write(data)
                    done += len(data)
                    progress(dict(stage='Downloading Silero VAD', fraction=min(1, done / SIZE), downloaded=done,
                                  total=SIZE, speed=done / max(.01, time.monotonic() - began)))
        cancel.check()
        if not valid(part):
            raise AppError('VAD model download failed integrity verification.')
        part.replace(target)
        return str(target)
    except requests.RequestException as exc:
        raise AppError('VAD download failed. Normal transcription is still available without VAD.') from exc
    finally:
        part.unlink(missing_ok=True)


def intervals(probabilities, frame_seconds=.032, threshold=.5, minimum=.25, silence=.3, padding=.15):
    """Reference segmentation for testing VAD hysteresis; native Silero runs inference."""
    spans, start, last = [], None, None
    for index, probability in enumerate(probabilities):
        time_at = index * frame_seconds
        if probability >= threshold:
            if start is None:
                start = time_at
            last = time_at + frame_seconds
        elif start is not None and time_at - last >= silence:
            if last - start >= minimum:
                spans.append((max(0, start - padding), min(len(probabilities) * frame_seconds, last + padding)))
            start = last = None
    if start is not None and last - start >= minimum:
        spans.append((max(0, start - padding), min(len(probabilities) * frame_seconds, last + padding)))
    return spans
