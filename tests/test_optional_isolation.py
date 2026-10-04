from pathlib import Path
from types import SimpleNamespace
import pytest
from transcription import pipeline
from transcription.models import ModelManager
from utils.paths import Paths
from utils.process import Cancellation
from utils.types import Options, Segment, AppError, Cancelled
from hardware.detection import BackendInfo


class FakeEngine:
    unloaded = False
    def __init__(self, *args): pass
    def load_model(self, *args): pass
    def unload(self): self.unloaded = True
    def transcribe(self, audio, event):
        return {'language': 'en', 'duration': 10, 'segments': [Segment(0, 10, 'Local speech recognition local speech recognition')]}


def setup_pipeline(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    manager = ModelManager(paths.models)
    monkeypatch.setattr(manager, 'ensure', lambda *args: paths.models / 'fake.bin')
    monkeypatch.setattr(pipeline, 'WhisperCppBackend', FakeEngine)
    monkeypatch.setattr(pipeline, 'inspect', lambda *args: SimpleNamespace(path='test.mp4'))
    monkeypatch.setattr(pipeline, 'normalize', lambda info, stream, audio, *args: Path(audio).write_bytes(b'audio'))
    settings = SimpleNamespace(temp_dir='', keep_temp=False)
    backends = {'cpu': BackendInfo('cpu', available=True)}
    return paths, settings, manager, backends


def test_missing_optional_models_preserve_core_transcript(tmp_path, monkeypatch):
    paths, settings, manager, backends = setup_pipeline(tmp_path, monkeypatch)
    options = Options(diarization=True, keywords=True, keyword_method='ai')
    result = pipeline.transcribe('test.mp4', options, paths, settings, manager, backends, Cancellation(), lambda e: None)
    assert result.segments[0].text.startswith('Local speech')
    assert len(result.warnings) == 2
    assert result.keyword_method.startswith('Offline')
    assert result.keywords
    assert list(paths.cache.iterdir()) == []
    assert not manager.busy('base')


def test_optional_worker_failure_does_not_hide_transcript(tmp_path, monkeypatch):
    paths, settings, manager, backends = setup_pipeline(tmp_path, monkeypatch)
    model = tmp_path / 'community'
    model.mkdir()
    def fail(*args): raise AppError('Optional requirements missing')
    monkeypatch.setattr(pipeline, 'run_json_worker', fail)
    result = pipeline.transcribe('test.mp4', Options(diarization=True, diarization_path=str(model)), paths, settings, manager, backends, Cancellation(), lambda e: None)
    assert result.segments
    assert 'requirements missing' in result.warnings[0]
    assert result.segments[0].speaker is None
    assert not list(paths.cache.iterdir())


def test_pipeline_failure_cleans_temp_and_model_lease(tmp_path, monkeypatch):
    paths, settings, manager, backends = setup_pipeline(tmp_path, monkeypatch)
    class CrashedEngine(FakeEngine):
        def transcribe(self, *args): raise AppError('Runtime crashed')
    monkeypatch.setattr(pipeline, 'WhisperCppBackend', CrashedEngine)
    with pytest.raises(AppError, match='crashed'):
        pipeline.transcribe('test.mp4', Options(), paths, settings, manager, backends, Cancellation(), lambda e: None)
    assert not list(paths.cache.iterdir())
    assert not manager.busy('base')
