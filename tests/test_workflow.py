import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import pytest
from utils.types import Segment, Transcript, Options, AppError
from transcription.quality import analyse, repetitive, confidence_label
from transcription.cleanup import merge_segments, export_snapshot
from transcription.vad import intervals
from local_workflow.projects import save_project, load_project
from local_workflow.history import History, realtime_speed
from local_workflow.queue import Queue
from local_workflow.search import matches
from hardware.recommendations import preset_values, recommend
from hardware.detection import BackendInfo
from media.languages import metadata_language
from exporters.formats import render


@pytest.mark.parametrize('text', ['คือ' * 40, 'hello ' * 40, 'Thank you for watching. ' * 20])
def test_pathological_repetition(text):
    assert repetitive(text)
    cue = analyse([Segment(0, 30, text)])[0]
    assert 'Pathological repetition' in cue.quality_flags


@pytest.mark.parametrize('text', ['No, no, no! Wait a moment.', 'Hello. Hello. Hello.', 'This is a normal explanation of repeated actions.'])
def test_legitimate_repetitions_retained(text):
    assert not repetitive(text)
    cues = analyse([Segment(0, 5, text)])
    assert cues[0].text == text


def test_low_confidence_and_non_speech_flags_do_not_remove_text():
    cue = Segment(0, 5, 'Suspicious but preserved', avg_logprob=-1.8, no_speech_prob=.9)
    analyse([cue])
    assert len(cue.quality_flags) == 2
    assert confidence_label(cue) == 'N/A'


def test_consecutive_segments_flag_after_three_not_two():
    cues = [Segment(i, i + 1, 'An identical sentence.') for i in range(4)]
    analyse(cues)
    assert not cues[1].quality_flags
    assert 'Consecutive near-identical segments' in cues[2].quality_flags


def test_vad_handles_silence_short_noise_and_padded_speech():
    assert intervals([0.0] * 100) == []
    assert intervals([0.0] * 20 + [1.0] * 2 + [0.0] * 20) == []
    spans = intervals([0.0] * 20 + [1.0] * 20 + [0.0] * 20)
    assert len(spans) == 1
    assert spans[0][0] == pytest.approx(.49)
    assert spans[0][1] == pytest.approx(1.43)


def test_merge_preserves_boundaries_speakers_and_raw_data():
    raw = [Segment(0, 1, 'Hello', source_ids=[0]), Segment(1.2, 3, 'world', source_ids=[1]), Segment(3.2, 4, 'Other', speaker='Speaker 2', source_ids=[2])]
    merged = merge_segments(raw)
    assert len(merged) == 2 and merged[0].start == 0 and merged[0].end == 3
    assert merged[0].source_ids == [0, 1]
    assert raw[0].text == 'Hello'
    assert len(merge_segments(raw, .1)) == 3


def test_original_segmentation_overlay_preserves_manual_edits_all_exports():
    raw = [Segment(0, 1, 'Hello', source_ids=[0]), Segment(1.2, 3, 'world', source_ids=[1])]
    transcript = Transcript('sample.mp4', 'base', 'en', 3, merge_segments(raw), raw_segments=deepcopy(raw))
    transcript.segments[0].text = 'Edited Hello world'
    original = export_snapshot(transcript, 'original')
    assert len(original.segments) == 2
    assert original.segments[0].text.startswith('Edited')
    for format in ('txt', 'srt', 'vtt', 'json', 'csv', 'md', 'tsv', 'lrc'):
        assert 'Edited' in render(original, format)
    assert transcript.raw_segments[0].text == 'Hello'


def test_project_roundtrip_preserves_metadata_edits_names_and_raw(tmp_path):
    raw = Segment(0, 2, 'original', 'Speaker 1', confidence=.8, avg_logprob=-.2, no_speech_prob=.1, source_ids=[0])
    transcript = Transcript('moved.mp4', 'base', 'th', 2, [deepcopy(raw)], raw_segments=[raw], speaker_names={'Speaker 1': 'Alice'}, keywords=['local'])
    transcript.segments[0].text = 'Edited Thai'
    path = tmp_path / 'test.ltproj'
    save_project(path, transcript, asdict(Options()), {'segmentation': 'original'})
    loaded, settings, exports = load_project(path)
    assert asdict(loaded) == asdict(transcript)
    assert loaded.source == 'moved.mp4' and exports['segmentation'] == 'original'
    assert 'Alice' in render(loaded, 'srt')
    assert not path.with_name(path.name + '.tmp').exists()


def test_project_rejects_bad_format_and_nonfinite_time(tmp_path):
    path = tmp_path / 'bad.ltproj'
    path.write_text('{"format":"other","version":1}', 'utf-8')
    with pytest.raises(AppError):
        load_project(path)
    transcript = Transcript('test', 'base', 'en', 2, [Segment(0, float('inf'), 'bad')])
    with pytest.raises(ValueError):
        save_project(path, transcript)


def test_history_metadata_only_realtime_and_persistence(tmp_path):
    path = tmp_path / 'history.sqlite3'
    history = History(path)
    history.add(filename='sample.mp4', model='base', status='completed', media_seconds=1200, processing_seconds=100)
    history.add(filename='cancelled.mp4', model='base', status='cancelled', media_seconds=1200, processing_seconds=2)
    rows = History(path).rows()
    assert rows[0]['realtime_speed'] is None and rows[1]['realtime_speed'] == 12
    assert 'transcript' not in rows[1]
    output = tmp_path / 'benchmarks.csv'
    history.export(output)
    assert 'sample.mp4' in output.read_text('utf-8')
    history.clear()
    assert History(path).rows() == []
    assert realtime_speed(100, 0) is None


def test_queue_serial_pause_cancel_retry_and_independent_options():
    queue = Queue()
    options = Options()
    first = queue.add('first.mp4', options)
    second = queue.add('second.mp4', options)
    options.model = 'turbo'
    assert first.options.model == 'base' and queue.next() is None
    queue.paused = False
    assert queue.next() is first and queue.next() is None
    queue.remove(first.id)
    assert first in queue.items
    queue.finish('Cancelled')
    queue.retry(first.id)
    assert queue.next() is first
    queue.finish('Completed')
    assert queue.next() is second
    queue.finish('Failed', 'broken media')
    queue.clear_completed()
    assert queue.items == [second]


def test_language_metadata_never_controls_detected_speech():
    assert metadata_language('eng', {'en': 'english', 'th': 'thai'}) == 'English'
    transcript = Transcript('test', 'turbo', 'th', 3)
    assert transcript.language == 'th'


def test_hardware_presets_advisory_cpu_small_gpu_large_gpu():
    cpu = BackendInfo('cpu', available=True, devices=[{'gpu': False}])
    gpu = BackendInfo('vulkan', available=True, devices=[{'gpu': True, 'memory_free': 16 * 1024**3}])
    assert recommend(cpu, 32 * 1024**3)['balanced'] == 'base'
    assert preset_values('balanced', gpu, 32 * 1024**3)['model'] == 'turbo'
    assert preset_values('accurate', gpu, 32 * 1024**3)['model'] == 'medium'
    assert preset_values('custom', gpu, 32 * 1024**3) == {}


def test_search_unicode_literal_multiple_matches():
    assert len(matches(['คือ คือ คือ', 'Other คือ'], 'คือ')) == 4
    assert matches(['a.b aXb'], 'a.b') == [(0, 0, 3)]
    assert matches(['Hello HELLO'], 'hello') == [(0, 0, 5), (0, 6, 11)]


def test_subtitle_preview_uses_edits_and_excludes_gaps():
    from ui.media_player import subtitle_at
    cue = Segment(2, 4, 'Edited subtitle')
    assert subtitle_at([cue], 3) == 'Edited subtitle'
    assert subtitle_at([cue], 4) == ''
