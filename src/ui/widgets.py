from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QPushButton
from media.ffmpeg import EXTENSIONS


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback=None, primary=False):
    widget = QPushButton(text)
    if callback:
        widget.clicked.connect(callback)
    if primary:
        widget.setObjectName('primary')
    return widget


def card():
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    return frame, layout


class DropArea(QFrame):
    file_dropped = Signal(str)
    browse = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('drop')
        self.setAcceptDrops(True)
        self.setMinimumHeight(145)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 18)
        self.title = label('Drop a video or audio file here', 'section')
        self.title.setWordWrap(True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = label('Audio stays on this device', 'muted')
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addWidget(subtitle)
        self.browse_button = button('Browse…', self.browse.emit)
        layout.addWidget(self.browse_button, alignment=Qt.AlignmentFlag.AlignCenter)

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.isEnabled():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.file_dropped.emit(paths[0])
                event.acceptProposedAction()


def duration(seconds):
    value = max(0, round(seconds or 0))
    hours, value = divmod(value, 3600)
    minutes, seconds = divmod(value, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


def size(value):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.1f} {unit}'
        value /= 1024
