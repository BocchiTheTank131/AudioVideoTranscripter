from collections import defaultdict
from dataclasses import replace
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextBlockUserData, QTextCursor, QTextDocument, QColor, QBrush, QKeyEvent
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox,
                              QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
                              QStackedWidget, QLineEdit, QMessageBox, QInputDialog, QDialog,
                              QDialogButtonBox, QFormLayout)
from exporters.formats import timestamp, plain, speaker_name
from utils.types import Segment
from transcription.quality import confidence_label
from local_workflow.search import matches
from .widgets import label, button


class CueData(QTextBlockUserData):
    def __init__(self, index):
        super().__init__()
        self.index = index


def collect_plain_edits(document, segments):
    """Qt retains cue IDs while typing; new blocks inherit surrounding cue timing."""
    if not segments:
        return []
    blocks = []
    block = document.begin()
    while block.isValid():
        tag = block.userData()
        blocks.append((tag.index if isinstance(tag, CueData) else None, block.text().replace('\u2028', '\n')))
        block = block.next()
    grouped = defaultdict(list)
    current = next((index for index, _ in blocks if index is not None), 0)
    for index, text in blocks:
        if index is not None:
            current = min(len(segments) - 1, index)
        grouped[current].append(text)
    indices = sorted(grouped)
    result = []
    for pos, index in enumerate(indices):
        text = '\n'.join(grouped[index]).strip()
        if not text:
            continue
        original = segments[index]
        next_index = indices[pos + 1] if pos + 1 < len(indices) else len(segments)
        stop = segments[max(index, next_index - 1)].end
        result.append(replace(original, end=stop, text=text,
                              words=original.words if text == original.text else [], confidence=original.confidence if text == original.text else None,
                              avg_logprob=original.avg_logprob if text == original.text else None,
                              tokens=original.tokens if text == original.text else [],
                              source_ids=[identifier for s in segments[index:next_index] for identifier in s.source_ids]))
    return result


class TranscriptView(QWidget):
    changed = Signal()
    seek_requested = Signal(float)

    def __init__(self):
        super().__init__()
        self.transcript = None
        self.dirty = False
        self.rendering = False
        self.current_mode = 0
        self.search_index = -1
        self.search_matches = []
        self.follow_suspended = False
        self.live = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(label('Transcript', 'section'))
        top.addStretch()
        self.mode = QComboBox()
        self.mode.addItems(['Timestamped', 'Plain text'])
        top.addWidget(self.mode)
        self.wrap = QCheckBox('Wrap')
        self.wrap.setChecked(True)
        top.addWidget(self.wrap)
        self.follow = QCheckBox('Follow live transcription')
        self.follow.setChecked(True)
        top.addWidget(self.follow)
        self.show_confidence = QCheckBox('Show confidence')
        top.addWidget(self.show_confidence)
        layout.addLayout(top)
        self.stack = QStackedWidget()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Time', 'Speaker', 'Text · double-click to edit', 'Quality'])
        self.table.setColumnHidden(3, True)
        self.table.setColumnWidth(3, 145)
        self.table.setColumnWidth(0, 108)
        self.table.setColumnWidth(1, 105)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText('Your transcript will appear here as speech is recognized.\n\nAll processing happens locally.')
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.editor)
        layout.addWidget(self.stack, 1)
        search = QHBoxLayout()
        self.find_text = QLineEdit()
        self.find_text.setPlaceholderText('Find in transcript…')
        self.find_text.setClearButtonEnabled(True)
        self.replace_text = QLineEdit()
        self.replace_text.setPlaceholderText('Replace with…')
        search.addWidget(self.find_text)
        search.addWidget(button('Previous', lambda: self.find(backward=True)))
        search.addWidget(button('Next', self.find))
        self.match_count = label('0 matches', 'muted')
        search.addWidget(self.match_count)
        search.addWidget(self.replace_text)
        search.addWidget(button('Replace all', self.replace_all))
        layout.addLayout(search)
        footer = QHBoxLayout()
        self.copy_button = button('Copy all', self.copy)
        self.rename_button = button('Rename speakers', self.rename)
        footer.addWidget(self.copy_button)
        footer.addWidget(self.rename_button)
        footer.addStretch()
        self.info = label('No transcript yet', 'muted')
        footer.addWidget(self.info)
        layout.addLayout(footer)
        self.table.itemChanged.connect(self.mark_dirty)
        self.editor.textChanged.connect(self.mark_dirty)
        self.mode.currentIndexChanged.connect(self.switch_mode)
        self.wrap.toggled.connect(self.set_wrap)
        self.find_text.returnPressed.connect(self.find)
        self.find_text.textChanged.connect(self.update_search)
        self.find_text.installEventFilter(self)
        self.show_confidence.toggled.connect(lambda shown: self.table.setColumnHidden(3, not shown))
        self.table.cellClicked.connect(lambda row, col: self.seek_requested.emit(self.transcript.segments[row].start) if col == 0 and self.transcript else None)
        for widget in (self.table, self.editor):
            widget.verticalScrollBar().valueChanged.connect(lambda value, w=widget: self.scroll_changed(w))
        self.follow.toggled.connect(lambda enabled: setattr(self, 'follow_suspended', False))
        self.table.cellDoubleClicked.connect(self.edit_time)
        self.set_editable(False)

    def set_editable(self, editable):
        self.editor.setReadOnly(not editable)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed if editable else QTableWidget.EditTrigger.NoEditTriggers)
        self.rename_button.setEnabled(editable and self.transcript is not None and any(s.speaker for s in self.transcript.segments))
        self.replace_text.setEnabled(editable)
        self.editable = editable

    def mark_dirty(self, *args):
        if not self.rendering and self.editable:
            self.dirty = True
            self.changed.emit()

    def set_transcript(self, transcript, editable=True):
        self.transcript = transcript
        self.live = not editable
        self.follow_suspended = False
        self.dirty = False
        self.set_editable(editable)
        self.render()
        self.table.scrollToTop()
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)

    def render(self):
        self.rendering = True
        try:
            self.table.setRowCount(0)
            self.editor.clear()
            if not self.transcript:
                return
            segments = self.transcript.segments
            self.table.setRowCount(len(segments))
            for i, segment in enumerate(segments):
                time_item = QTableWidgetItem(timestamp(segment.start))
                time_item.setToolTip(timestamp(segment.start) + ' → ' + timestamp(segment.end) + '\nClick to seek source; double-click to edit cue timing')
                time_item.setFlags(time_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, 0, time_item)
                speaker_item = QTableWidgetItem(speaker_name(self.transcript, segment))
                speaker_item.setFlags(speaker_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, 1, speaker_item)
                self.table.setItem(i, 2, QTableWidgetItem(segment.text))
                self.decorate_row(i, segment)
            self.table.resizeRowsToContents()
            self.editor.setPlainText('\n'.join(s.text.replace('\n', '\u2028') for s in segments))
            block = self.editor.document().begin()
            for i in range(len(segments)):
                block.setUserData(CueData(i))
                block = block.next()
            suspicious = sum(bool(s.quality_flags) for s in segments)
            self.info.setText(f'{len(segments):,} cues · {self.transcript.language.upper()}' + (f' · ⚠ {suspicious} to review' if suspicious else ''))
            self.rename_button.setEnabled(self.editable and any(s.speaker for s in segments))
        finally:
            self.rendering = False
        self.update_search()

    def sync(self):
        if not self.transcript or not self.dirty:
            return
        if self.current_mode == 0:
            for i, segment in enumerate(self.transcript.segments):
                item = self.table.item(i, 2)
                if item and item.text() != segment.text:
                    segment.text = item.text()
                    segment.words = []
                    segment.confidence = None
                    segment.avg_logprob = None
                    segment.tokens = []
                    from transcription.quality import flag
                    flag(segment)
        else:
            self.transcript.segments = collect_plain_edits(self.editor.document(), self.transcript.segments)
        self.dirty = False

    def switch_mode(self, index):
        self.sync()
        self.current_mode = index
        self.stack.setCurrentIndex(index)
        self.render()

    def append(self, segment):
        if not self.transcript:
            return
        self.transcript.segments.append(segment)
        self.rendering = True
        table_position = self.table.verticalScrollBar().value()
        editor_position = self.editor.verticalScrollBar().value()
        follow = self.follow.isChecked() and not self.follow_suspended
        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(timestamp(segment.start)))
            self.table.setItem(row, 1, QTableWidgetItem(segment.speaker or ''))
            self.table.setItem(row, 2, QTableWidgetItem(segment.text))
            self.decorate_row(row, segment)
            self.table.resizeRowToContents(row)
            if follow:
                self.table.scrollToBottom()
            self.editor.append(segment.text.replace('\n', '\u2028'))
            self.editor.document().lastBlock().setUserData(CueData(row))
            if follow:
                self.editor.verticalScrollBar().setValue(self.editor.verticalScrollBar().maximum())
            else:
                self.table.verticalScrollBar().setValue(table_position)
                self.editor.verticalScrollBar().setValue(editor_position)
            self.info.setText(f'{row + 1:,} cues · live')
        finally:
            self.rendering = False

    def scroll_changed(self, widget):
        if not self.rendering and self.live:
            bar = widget.verticalScrollBar()
            self.follow_suspended = bar.maximum() - bar.value() > 4

    def decorate_row(self, row, segment):
        warning = bool(segment.quality_flags)
        self.table.item(row, 0).setText(('⚠ ' if warning else '') + timestamp(segment.start))
        item = QTableWidgetItem(confidence_label(segment))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        details = '\n'.join(segment.quality_flags)
        for key in ('avg_logprob', 'no_speech_prob', 'compression_ratio'):
            value = getattr(segment, key)
            if value is not None:
                details += f'\n{key}: {value:.3f}'
        self.table.item(row, 2).setToolTip(details.strip())
        item.setToolTip(details.strip() + '\nToken probability is not a calibrated accuracy score.')
        self.table.setItem(row, 3, item)

    def set_wrap(self, enabled):
        self.editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth if enabled else QTextEdit.LineWrapMode.NoWrap)
        self.table.setWordWrap(enabled)
        self.table.resizeRowsToContents()

    def copy(self):
        self.sync()
        if self.transcript:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(plain(self.transcript, self.current_mode == 0, True))

    def update_search(self):
        texts = [self.table.item(i, 2).text() for i in range(self.table.rowCount())] if self.current_mode == 0 else [self.editor.toPlainText()]
        self.search_matches = matches(texts, self.find_text.text())
        self.search_index = -1
        self.match_count.setText(f'{len(self.search_matches)} matches')
        for row in range(self.table.rowCount()):
            self.table.item(row, 2).setBackground(QBrush())
        if self.current_mode == 0:
            for row, _, _ in self.search_matches:
                self.table.item(row, 2).setBackground(QColor(145, 115, 30, 85))
        selections = []
        for _, start, end in self.search_matches if self.current_mode == 1 else []:
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(self.editor.document())
            text = texts[0]
            cursor.setPosition(len(text[:start].encode('utf-16-le')) // 2)
            cursor.setPosition(len(text[:end].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selection.format.setBackground(QColor(145, 115, 30, 110))
            selections.append(selection)
        self.editor.setExtraSelections(selections)

    def find(self, checked=False, backward=False):
        if not self.search_matches:
            self.update_search()
        if not self.search_matches:
            return
        self.search_index = (self.search_index + (-1 if backward else 1)) % len(self.search_matches)
        row, start, end = self.search_matches[self.search_index]
        self.match_count.setText(f'{self.search_index + 1} / {len(self.search_matches)} matches')
        if self.current_mode == 0:
            self.table.setCurrentCell(row, 2)
            self.table.scrollToItem(self.table.item(row, 2))
        else:
            text = self.editor.toPlainText()
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(len(text[:start].encode('utf-16-le')) // 2)
            cursor.setPosition(len(text[:end].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self.find_text and event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.find(backward=True)
            return True
        return super().eventFilter(obj, event)

    def replace_all(self):
        if not self.editable or not self.find_text.text() or not self.transcript:
            return
        self.sync()
        find, replacement = self.find_text.text(), self.replace_text.text()
        for segment in self.transcript.segments:
            text = segment.text.replace(find, replacement)
            if text != segment.text:
                segment.text = text
                segment.words = []
                segment.confidence = None
                segment.avg_logprob = None
                segment.tokens = []
        self.render()
        self.changed.emit()

    def rename(self):
        self.sync()
        if not self.transcript:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Rename speakers')
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        edits = {}
        for speaker in sorted({s.speaker for s in self.transcript.segments if s.speaker}):
            edit = QLineEdit(self.transcript.speaker_names.get(speaker, speaker))
            edits[speaker] = edit
            form.addRow(speaker, edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.transcript.speaker_names.update({speaker: edit.text().strip() or speaker for speaker, edit in edits.items()})
            self.render()
            self.changed.emit()

    def edit_time(self, row, column):
        if column != 0 or not self.editable or not self.transcript:
            return
        self.sync()
        segment = self.transcript.segments[row]
        text, ok = QInputDialog.getText(self, 'Cue timing', 'Start and end in seconds (e.g. 2.34, 7.12)', text=f'{segment.start:.3f}, {segment.end:.3f}')
        if ok:
            try:
                start, end = [float(value.strip()) for value in text.split(',')]
                import math
                if not math.isfinite(start + end) or start < 0 or end <= start or end > self.transcript.duration + .1:
                    raise ValueError
                segment.start, segment.end = start, end
                segment.words = []
                self.render()
                self.changed.emit()
            except ValueError:
                QMessageBox.warning(self, 'Invalid timing', 'Enter two finite seconds values with 0 ≤ start < end ≤ recording duration.')
