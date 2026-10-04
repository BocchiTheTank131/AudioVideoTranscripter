from copy import deepcopy
from utils.types import Segment, Transcript
from transcription.cleanup import export_snapshot
from exporters.formats import render


def test_deleted_cue_is_not_restored_by_original_export():
    raw = [Segment(0, 2, 'First', source_ids=[0]), Segment(2, 4, 'Deleted', source_ids=[1])]
    transcript = Transcript('test', 'base', 'en', 4, [deepcopy(raw[0])], raw_segments=raw)
    assert 'Deleted' not in render(export_snapshot(transcript, 'original'), 'txt')


def test_retry_split_cues_overlay_original_text_without_loss():
    raw = [Segment(0, 4, 'Original', source_ids=[0])]
    edited = [Segment(0, 2, 'New first', source_ids=[0]), Segment(2, 4, 'New second', source_ids=[0])]
    transcript = Transcript('test', 'base', 'en', 4, edited, raw_segments=raw)
    original = export_snapshot(transcript, 'original')
    assert original.segments[0].text == 'New first New second'
    assert original.segments[0].start == 0 and original.segments[0].end == 4


def test_subtitles_skip_empty_edited_cues_and_keep_numbering_valid():
    transcript = Transcript('test', 'base', 'en', 4, [Segment(0, 2, ''), Segment(2, 4, 'Text')])
    assert render(transcript, 'srt').startswith('1\n00:00:02,000')
    assert render(transcript, 'vtt').startswith('WEBVTT\n\n1\n00:00:02.000')
