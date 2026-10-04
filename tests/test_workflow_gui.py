import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from utils.types import Segment, Transcript
from ui.transcript import TranscriptView
from transcription.quality import analyse

app = QApplication.instance() or QApplication([])


def test_search_next_previous_count_and_highlights():
    view = TranscriptView()
    view.set_transcript(Transcript('test', 'base', 'en', 10, [Segment(0, 5, 'Hello hello'), Segment(5, 10, 'hello again')]))
    view.find_text.setText('hello')
    view.find()
    assert view.match_count.text() == '1 / 3 matches'
    view.find()
    assert view.match_count.text() == '2 / 3 matches'
    view.find(backward=True)
    assert view.match_count.text() == '1 / 3 matches'
    view.mode.setCurrentIndex(1)
    assert len(view.editor.extraSelections()) == 3
    view.deleteLater()


def test_quality_marked_without_confidence_fabrication_and_click_seeks():
    view = TranscriptView()
    cue = analyse([Segment(5, 10, 'คือ' * 40)])[0]
    view.set_transcript(Transcript('test', 'base', 'th', 10, [cue]))
    assert '⚠' in view.table.item(0, 0).text()
    assert view.table.item(0, 3).text() == 'N/A'
    assert view.table.isColumnHidden(3)
    seeks = []
    view.seek_requested.connect(seeks.append)
    view.table.cellClicked.emit(0, 0)
    assert seeks == [5]
    view.deleteLater()


def test_manual_scroll_suspends_live_follow_and_bottom_resumes():
    view = TranscriptView()
    view.resize(700, 300)
    view.show()
    view.set_transcript(Transcript('test', 'base', 'en', 100), editable=False)
    for i in range(30):
        view.append(Segment(i, i + 1, f'Cue {i}'))
    app.processEvents()
    bar = view.table.verticalScrollBar()
    bar.setValue(0)
    assert view.follow_suspended
    view.append(Segment(30, 31, 'Newest cue'))
    assert bar.value() == 0
    bar.setValue(bar.maximum())
    assert not view.follow_suspended
    view.append(Segment(31, 32, 'Final cue'))
    assert bar.value() == bar.maximum()
    view.close()
    view.deleteLater()
