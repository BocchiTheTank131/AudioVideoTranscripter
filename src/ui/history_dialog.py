from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox
from local_workflow.history import COLUMNS
from .widgets import button, label, duration, size


class HistoryDialog(QDialog):
    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle('Local transcription history')
        self.resize(1100, 480)
        layout = QVBoxLayout(self)
        note = label('Local benchmarks · filenames and metrics only; no transcript content.\nRAM includes the app and child processes. VRAM is sampled system/adapter usage and may include other apps.', 'muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([s.replace('_', ' ').title() for s in COLUMNS])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        row.addWidget(button('Export benchmark CSV', self.export))
        row.addWidget(button('Clear history', self.clear))
        row.addStretch()
        row.addWidget(button('Close', self.accept))
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        rows = self.history.rows()
        self.table.setRowCount(len(rows))
        for row, value in enumerate(rows):
            for col, key in enumerate(COLUMNS):
                item = value.get(key)
                if key in ('peak_ram_bytes', 'peak_vram_bytes'):
                    text = size(item) if item is not None else 'N/A'
                elif key == 'realtime_speed':
                    text = f'{item:.2f}×' if item is not None else 'N/A'
                elif key.endswith('_seconds'):
                    text = duration(item)
                else:
                    text = str(item or '')
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def clear(self):
        if QMessageBox.question(self, 'Clear local history', 'Delete all local benchmark metadata?') == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self.refresh()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export benchmarks', 'benchmarks.csv', 'CSV (*.csv)')
        if path:
            try:
                self.history.export(path)
            except OSError as exc:
                QMessageBox.warning(self, 'Export failed', str(exc))
