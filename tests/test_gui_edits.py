import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from ui.transcript import TranscriptView
from exporters.formats import render
from utils.types import Segment, Transcript

app = QApplication.instance() or QApplication([])


def make_view():
    view = TranscriptView()
    view.set_transcript(Transcript('test.mp4', 'base', 'en', 10, [Segment(0, 4, 'First sentence'), Segment(4, 10, 'Second sentence')]))
    return view


def test_table_edits_export_across_modes():
    view = make_view()
    view.table.item(0, 2).setText('Edited sentence')
    view.mode.setCurrentIndex(1)
    assert view.editor.toPlainText().startswith('Edited sentence')
    assert 'Edited sentence' in render(view.transcript, 'srt')
    view.deleteLater()


def test_plain_split_retains_timing_and_text():
    view = make_view()
    view.mode.setCurrentIndex(1)
    cursor = view.editor.textCursor()
    cursor.setPosition(5)
    cursor.insertText('\nAdded line ')
    view.sync()
    assert 'Added line' in view.transcript.segments[0].text
    assert view.transcript.segments[0].start == 0 and view.transcript.segments[0].end == 4
    assert view.transcript.segments[-1].text == 'Second sentence'
    view.deleteLater()


def test_plain_join_extends_original_cue_interval():
    view = make_view()
    view.mode.setCurrentIndex(1)
    cursor = view.editor.textCursor()
    cursor.setPosition(len('First sentence'))
    cursor.deleteChar()
    view.sync()
    assert len(view.transcript.segments) == 1
    assert view.transcript.segments[0].end == 10
    assert 'Second sentence' in render(view.transcript, 'vtt')
    view.deleteLater()


def test_speaker_rename_applies_to_exports():
    view = make_view()
    view.transcript.segments[0].speaker = 'Speaker 1'
    view.transcript.speaker_names['Speaker 1'] = 'Alice'
    assert 'Alice:' in render(view.transcript, 'txt')
    assert 'Alice' in render(view.transcript, 'json')
    view.deleteLater()
