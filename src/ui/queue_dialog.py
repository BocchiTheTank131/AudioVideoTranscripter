from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QFileDialog
from PySide6.QtCore import QTimer
from pathlib import Path
from .widgets import button, label


class QueueDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Batch transcription')
        self.resize(940, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(label('One job at a time. Results save as local .ltproj, TXT, SRT and VTT files in a new batch folder.', 'muted'))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Filename', 'Model', 'Language', 'Status', 'Progress', 'Realtime'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.controls = {}
        for text, callback in [('Add files', self.add), ('Start / resume queue', window.resume_queue), ('Pause queue', self.pause), ('Cancel current', window.cancel), ('Remove', self.remove), ('Retry failed', self.retry), ('Clear completed', self.clear)]:
            control = button(text, callback)
            self.controls[text] = control
            row.addWidget(control)
        self.controls['Pause queue'].setToolTip('Finish the current job, then wait. This does not pause Whisper inference.')
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def add(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Add recordings', self.window.settings.last_input_dir, 'Media files (*.*)')
        self.window.add_queue_files(files)

    def pause(self):
        self.window.queue.paused = True
        self.refresh()

    def selected(self):
        row = self.table.currentRow()
        return self.window.queue.items[row].id if 0 <= row < len(self.window.queue.items) else ''

    def remove(self):
        self.window.queue.remove(self.selected())
        self.refresh()

    def retry(self):
        for item in self.window.queue.items:
            if item.status in ('Failed', 'Cancelled'):
                self.window.queue.retry(item.id)
        self.refresh()

    def clear(self):
        self.window.queue.clear_completed()
        self.refresh()

    def refresh(self):
        queue = self.window.queue
        self.table.setRowCount(len(queue.items))
        for row, item in enumerate(queue.items):
            values = [Path(item.source).name, item.options.model, item.options.language, item.status,
                      f'{item.progress:.0%}' if item.progress is not None else '—', f'{item.speed:.2f}×' if item.speed is not None else 'N/A']
            for col, value in enumerate(values):
                widget = QTableWidgetItem(value)
                widget.setToolTip(item.error or item.output)
                self.table.setItem(row, col, widget)
        self.table.resizeColumnsToContents()
        self.controls['Cancel current'].setEnabled(queue.active is not None and self.window.job is not None)
        self.controls['Pause queue'].setEnabled(not queue.paused)
        self.controls['Start / resume queue'].setEnabled(self.window.job is None and any(item.status == 'Waiting' for item in queue.items))
        self.controls['Retry failed'].setEnabled(any(item.status in ('Failed', 'Cancelled') for item in queue.items))
        self.controls['Clear completed'].setEnabled(any(item.status == 'Completed' for item in queue.items))
