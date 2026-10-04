"""Exercise bounded native chunking and cancellation on a >5-minute WAV."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from utils.paths import Paths
from settings.store import Settings
from transcription.models import ModelManager
from transcription.pipeline import transcribe
from utils.process import Cancellation
from utils.types import Options, Cancelled
from hardware.detection import detect
import requests

def blocked(*a, **kw): raise AssertionError('Unexpected network request')
requests.get = blocked
paths = Paths()
manager = ModelManager(paths.models)
backends = detect(paths)
cancel = Cancellation()
progress = []
began = time.monotonic()
options = Options(acceleration='vulkan', stream=0)

def events(event):
    if event.get('processed') is not None:
        progress.append(event['processed'])

result = transcribe('verification/long.wav', options, paths, Settings(), manager, backends, cancel, events)
assert result.duration > 300
assert any(s.start > 300 for s in result.segments)
assert progress == sorted(progress)
assert abs(progress[-1] - result.duration) < .001
assert not list(paths.cache.glob('job-*'))
assert not manager.busy('base')
report = dict(duration=result.duration, segments=len(result.segments), inference_pipeline_seconds=time.monotonic() - began,
              progress_callbacks=len(progress), monotone_progress=True, last_segment_end=result.segments[-1].end)
cancel = Cancellation()
began = time.monotonic()

def cancel_event(event):
    if event.get('stage') == 'Transcribing':
        cancel.cancel()

try:
    transcribe('verification/long.wav', options, paths, Settings(), manager, backends, cancel, cancel_event)
    raise AssertionError('Cancellation did not interrupt transcription')
except Cancelled:
    report['cancel_pipeline_seconds'] = time.monotonic() - began
assert not list(paths.cache.glob('job-*'))
assert not manager.busy('base')
Path('verification/long-report.json').write_text(json.dumps(report, indent=2), 'utf-8')
print(json.dumps(report))
