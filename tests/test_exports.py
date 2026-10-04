import csv
import io
import json
import pytest
from exporters.formats import timestamp, render, export_file
from utils.types import Segment, Transcript


@pytest.fixture
def transcript():
    return Transcript('meeting.mp4', 'base', 'en', 12, [Segment(2.34, 7.12, 'Hello, everyone.', 'Speaker 1'),
                                                     Segment(7.12, 11.84, 'Line one\n\nLine two', 'Speaker 2')],
                      keywords=['speech recognition'], speaker_names={'Speaker 1': 'Alice'})


@pytest.mark.parametrize('value,expected', [(0, '00:00:00.000'), (2.34, '00:00:02.340'),
                                          (59.9996, '00:01:00.000'), (360000.123, '100:00:00.123'), (-1, '00:00:00.000')])
def test_timestamp(value, expected):
    assert timestamp(value) == expected


def test_txt_options(transcript):
    assert render(transcript, 'txt', False, False).startswith('Hello, everyone.')
    assert '[00:00:02.340]' in render(transcript, 'txt', True, False)
    assert 'Alice:\nHello' in render(transcript, 'txt', False, True)
    assert '[00:00:02.340] Alice:' in render(transcript, 'txt', True, True)


def test_srt_cues(transcript):
    output = render(transcript, 'srt')
    assert output.startswith('1\n00:00:02,340 --> 00:00:07,120\nAlice: Hello, everyone.\n\n2\n')
    assert 'Line one\n\nLine two' not in output
    assert len(output.strip().split('\n\n')) == 2


def test_vtt(transcript):
    output = render(transcript, 'vtt')
    assert output.startswith('WEBVTT\n\n1\n00:00:02.340 --> 00:00:07.120')
    assert ',' not in output.splitlines()[3]


def test_json_preserves_structure_and_edits(transcript):
    transcript.segments[0].text = 'Edited transcript ภาษาไทย'
    transcript.segments[0].words = [{'text': 'Edited', 'start': 2.34, 'end': 2.9}]
    data = json.loads(render(transcript, 'json'))
    assert data['segments'][0]['text'].endswith('ภาษาไทย')
    assert data['segments'][0]['speaker'] == 'Alice'
    assert data['segments'][0]['words'][0]['start'] == 2.34
    assert data['keywords'] == ['speech recognition']


@pytest.mark.parametrize('format,delimiter', [('csv', ','), ('tsv', '\t')])
def test_tabular_roundtrip(transcript, format, delimiter):
    rows = list(csv.DictReader(io.StringIO(render(transcript, format)), delimiter=delimiter))
    assert rows[0]['speaker'] == 'Alice'
    assert rows[0]['text'] == 'Hello, everyone.'
    assert rows[1]['text'] == 'Line one\n\nLine two'


def test_never_overwrite_without_permission(tmp_path, transcript):
    target = tmp_path / 'result.txt'
    target.write_text('Keep me', 'utf-8')
    with pytest.raises(FileExistsError):
        export_file(target, transcript)
    assert target.read_text() == 'Keep me'
    export_file(target, transcript, overwrite=True)
    assert 'Hello' in target.read_text()


@pytest.mark.parametrize('format', ['txt', 'srt', 'vtt', 'json', 'csv', 'md', 'tsv', 'lrc'])
def test_all_formats_keep_edited_text(transcript, format):
    transcript.segments[0].text = 'Edited sentence'
    assert 'Edited sentence' in render(transcript, format)


def test_lrc_long_recording(transcript):
    transcript.segments[0].start = 3602.34
    assert '[60:02.34]' in render(transcript, 'lrc')
