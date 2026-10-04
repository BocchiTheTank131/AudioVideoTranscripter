import json
import sys
import threading
import time
from dataclasses import replace
import pytest
from hardware.detection import BackendInfo, parse_probe, choose, sensible_threads
from media.ffmpeg import parse_metadata
from diarization.alignment import align_speakers
from keywords.extraction import frequency_keywords
from transcription.whisper_cpp import tokens_to_words
from translation.tasks import validate_task
from utils.process import Cancellation, run_stream
from utils.types import Segment, AppError, Cancelled


def test_hardware_priority_and_unavailable_fallback():
    backends = {name: BackendInfo(name, available=name in ('cpu', 'vulkan', 'cuda')) for name in ('cpu', 'vulkan', 'cuda', 'hip')}
    assert choose(backends).name == 'cuda'
    backends['cuda'].available = False
    assert choose(backends).name == 'vulkan'
    backends['vulkan'].available = False
    assert choose(backends).name == 'cpu'
    with pytest.raises(AppError, match='unavailable'):
        choose(backends, 'hip')


def test_probe_cannot_fake_gpu_from_cpu():
    assert not parse_probe('vulkan', 'runtime.exe', {'protocol': 1, 'backend': 'vulkan', 'devices': [{'gpu': False}]}).available
    assert not parse_probe('cuda', 'runtime.exe', {'protocol': 1, 'backend': 'cpu', 'devices': [{'gpu': True}]}).available
    assert parse_probe('cpu', 'runtime.exe', {'protocol': 1, 'backend': 'cpu', 'devices': [{'gpu': False}]}).available
    assert sensible_threads() >= 1


def test_multiple_audio_tracks():
    info = parse_metadata({'format': {'duration': '123.4', 'size': '1000', 'format_name': 'mov,mp4'}, 'streams': [
        {'index': 0, 'codec_type': 'video'},
        {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '48000', 'channels': 2, 'tags': {'language': 'en'}},
        {'index': 2, 'codec_type': 'audio', 'codec_name': 'opus', 'sample_rate': '48000', 'channels': 1}]}, 'file.mp4')
    assert info.duration == 123.4
    assert [s.index for s in info.streams] == [1, 2]
    assert info.streams[0].rate == 48000 and info.streams[0].channels == 2
    with pytest.raises(AppError, match='no audio'):
        parse_metadata({'streams': [{'codec_type': 'video'}]}, 'silent.mp4')


def test_diarization_uses_overlap_and_leaves_unknown():
    segments = [Segment(0, 3, 'A'), Segment(3, 7, 'B'), Segment(10, 11, 'C')]
    align_speakers(segments, [{'start': 0, 'end': 2, 'speaker': 'Speaker 1'}, {'start': 2, 'end': 7, 'speaker': 'Speaker 2'}])
    assert [s.speaker for s in segments] == ['Speaker 1', 'Speaker 2', None]


def test_overlapping_diarization_turns():
    segments = [Segment(3, 4, 'text')]
    align_speakers(segments, [{'start': 0, 'end': 10, 'speaker': 'A'}, {'start': 2, 'end': 3.5, 'speaker': 'B'}])
    assert segments[0].speaker == 'A'


def test_word_grouping_preserves_token_timing():
    words = tokens_to_words([{'text': ' tran', 'start': 0, 'end': 1}, {'text': 'script', 'start': 1, 'end': 2},
                             {'text': ' works', 'start': 2, 'end': 3}])
    assert words[0]['text'] == 'transcript'
    assert words[0]['start'] == 0 and words[0]['end'] == 2
    assert len(words[0]['tokens']) == 2


def test_keywords_and_translation_guardrails():
    output = frequency_keywords('Whisper recognition Whisper recognition and the computer', 3, 1, 2)
    assert 'whisper recognition' in output
    assert all('the' not in word.split() for word in output)
    validate_task('base', 'translate')
    for model in ('base.en', 'turbo'):
        with pytest.raises(AppError):
            validate_task(model, 'translate')


def test_unicode_word_timing_preserves_split_utf8_tokens():
    text = 'ภาษาไทย'
    encoded = text.encode('utf-8')
    words = tokens_to_words([{'bytes_hex': (b' ' + encoded[:2]).hex(), 'start': 0, 'end': .3},
                             {'bytes_hex': encoded[2:].hex(), 'start': .3, 'end': 1}])
    assert words[0]['text'] == text
    assert words[0]['end'] == 1
    assert words[0]['tokens'][0]['bytes_hex']


def test_running_process_cancellation_is_prompt():
    cancel = Cancellation()
    timer = threading.Timer(.4, cancel.cancel)
    timer.start()
    began = time.monotonic()
    with pytest.raises(Cancelled):
        run_stream([sys.executable, '-c', 'import time; time.sleep(60)'], cancel)
    timer.join()
    assert time.monotonic() - began < 5
    assert not cancel._processes


def test_cancellation_before_spawn():
    cancel = Cancellation()
    cancel.cancel()
    with pytest.raises(Cancelled):
        run_stream(['does-not-exist'], cancel)


def test_child_failure_is_reported():
    with pytest.raises(AppError, match='exit 7'):
        run_stream([sys.executable, '-c', 'import sys; print("test diagnostic", file=sys.stderr); sys.exit(7)'], Cancellation())
