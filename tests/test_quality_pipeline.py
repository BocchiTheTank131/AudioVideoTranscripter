from copy import deepcopy
import json
from dataclasses import replace
import pytest
from test_optional_isolation import setup_pipeline, FakeEngine
from transcription import pipeline
from transcription.whisper_cpp import WhisperCppBackend
from hardware.detection import BackendInfo
from utils.process import Cancellation
from utils.types import Options, Segment, Cancelled
from transcription.quality import obvious_hallucination


def test_quality_requires_audio_evidence_before_auto_cleanup():
    assert not obvious_hallucination(Segment(0, 30, 'hello ' * 40, avg_logprob=-.1, no_speech_prob=.01))
    assert obvious_hallucination(Segment(0, 30, 'คือ' * 40, no_speech_prob=.9))


def test_backend_preserves_native_confidence_metadata(monkeypatch, tmp_path):
    model = tmp_path / 'model.bin'
    model.write_bytes(b'fake')
    backend = WhisperCppBackend(BackendInfo('cpu', executable='bridge', available=True), Cancellation())
    backend.load_model(model, Options(vad=False))
    def stream(args, cancel, read):
        read(json.dumps(dict(event='segment', start=0, end=2, text='Recognized speech', confidence=.7, avg_logprob=-.5,
                             no_speech_probability=.15, tokens=[dict(id=2, text=' speech', probability=.7)])))
        read(json.dumps(dict(event='done', language='en', duration=2)))
    monkeypatch.setattr('transcription.whisper_cpp.run_stream', stream)
    result = backend.transcribe('audio.wav', lambda e: None)
    cue = result['segments'][0]
    assert cue.avg_logprob == -.5 and cue.no_speech_prob == .15 and cue.confidence == .7
    assert cue.tokens and not cue.words and cue.compression_ratio is not None


def test_auto_cleanup_retries_once_and_retains_raw(monkeypatch, tmp_path):
    paths, settings, manager, backends = setup_pipeline(tmp_path, monkeypatch)
    class RetryEngine(FakeEngine):
        calls = 0
        def transcribe(self, *args):
            self.calls += 1
            text = 'hello ' * 40 if self.calls == 1 else 'Clear recognized speech.'
            return dict(language='en', duration=10, segments=[Segment(0, 5, text, no_speech_prob=.95)])
    monkeypatch.setattr(pipeline, 'WhisperCppBackend', RetryEngine)
    monkeypatch.setattr(pipeline, 'run_stream', lambda *args: None)
    result = pipeline.transcribe('test.mp4', Options(vad=False, auto_clean=True), paths, settings, manager, backends, Cancellation(), lambda e: None)
    assert result.segments[0].text == 'Clear recognized speech.'
    assert result.raw_segments[0].text.startswith('hello hello')
    assert result.cleanup['retried_original_ids'] == [0] and not result.cleanup['removed_original_ids']
    assert not manager.busy('base') and not list(paths.cache.iterdir())


def test_vad_unavailable_keeps_transcription(monkeypatch, tmp_path):
    paths, settings, manager, backends = setup_pipeline(tmp_path, monkeypatch)
    result = pipeline.transcribe('test.mp4', Options(vad=True), paths, settings, manager, backends, Cancellation(), lambda e: None)
    assert result.segments and 'VAD model is unavailable' in result.warnings[0]


def test_cleanup_cancellation_releases_model_and_temp(monkeypatch, tmp_path):
    paths, settings, manager, backends = setup_pipeline(tmp_path, monkeypatch)
    token = Cancellation()
    def event(value):
        if value.get('stage') == 'Reviewing pathological repetition':
            token.cancel()
    with pytest.raises(Cancelled):
        pipeline.transcribe('test.mp4', Options(vad=False, auto_clean=True), paths, settings, manager, backends, token, event)
    assert not manager.busy('base') and not list(paths.cache.iterdir())
