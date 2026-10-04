"""Optional local Qt Multimedia player, isolated from the transcription pipeline."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl, QSizeF
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QSlider, QCheckBox, QSpinBox,
                              QGraphicsView, QGraphicsScene, QGraphicsTextItem)
from .widgets import button, label, duration


def subtitle_at(segments, seconds, max_lines=2):
    active = [s.text for s in segments if s.start <= seconds < s.end]
    return '\n'.join(' '.join(active).splitlines()[:max_lines])


class MediaPreview(QDialog):
    def __init__(self, settings, transcript_provider, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
        self.setWindowTitle('Local media preview')
        self.resize(820, 570)
        self.settings, self.transcript_provider = settings, transcript_provider
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(.7)
        layout = QVBoxLayout(self)
        self.scene = QGraphicsScene(self)
        self.video = QGraphicsVideoItem()
        self.video.setSize(QSizeF(960, 540))
        self.scene.addItem(self.video)
        self.player.setVideoOutput(self.video)
        self.subtitle = QGraphicsTextItem()
        self.subtitle.setTextWidth(880)
        self.subtitle.setDefaultTextColor(QColor('white'))
        self.subtitle.setZValue(5)
        self.scene.addItem(self.subtitle)
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(4)
        shadow.setOffset(1, 2)
        self.subtitle.setGraphicsEffect(shadow)
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet('background: #121418; border: 0;')
        layout.addWidget(self.view, 1)
        self.error = label('', 'muted')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.seek_bar = QSlider(Qt.Orientation.Horizontal)
        self.seek_bar.setRange(0, 0)
        self.seek_bar.sliderReleased.connect(lambda: self.player.setPosition(self.seek_bar.value()))
        layout.addWidget(self.seek_bar)
        row = QHBoxLayout()
        row.addWidget(button('Play', self.player.play))
        row.addWidget(button('Pause', self.player.pause))
        row.addWidget(button('Stop', self.player.stop))
        self.position = label('00:00 / 00:00', 'muted')
        row.addWidget(self.position)
        row.addStretch()
        row.addWidget(label('Volume'))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(70)
        volume.setMaximumWidth(100)
        volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        row.addWidget(volume)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.enabled = QCheckBox('Preview edited subtitles')
        self.enabled.setChecked(True)
        row.addWidget(self.enabled)
        for title, key, limits in [('Font', 'subtitle_font', (10, 48)), ('Bottom margin', 'subtitle_margin', (0, 150)), ('Max lines', 'subtitle_lines', (1, 6))]:
            row.addWidget(label(title))
            spin = QSpinBox()
            spin.setRange(*limits)
            spin.setValue(getattr(settings, key))
            spin.valueChanged.connect(lambda v, k=key: setattr(settings, k, v))
            row.addWidget(spin)
        layout.addLayout(row)
        self.player.durationChanged.connect(lambda ms: self.seek_bar.setMaximum(ms))
        self.player.positionChanged.connect(self.update_position)
        self.player.errorOccurred.connect(lambda *args: self.error.setText('Playback is unavailable for this file. ' + self.player.errorString() + ' Transcription remains available.'))
        self.pending_seek = None
        self.player.mediaStatusChanged.connect(self.media_status)

    def open_media(self, path, seconds=0, audio_track=0):
        if not Path(path).is_file():
            self.error.setText('Source media is missing. Locate it again from Open media or Open Project.')
            return
        url = QUrl.fromLocalFile(str(Path(path).resolve()))
        self.pending_audio_track = audio_track
        if self.player.source() != url:
            self.player.stop()
            self.pending_seek = round(seconds * 1000)
            self.player.setSource(url)
        else:
            self.player.setPosition(round(seconds * 1000))
            if 0 <= audio_track < len(self.player.audioTracks()):
                self.player.setActiveAudioTrack(audio_track)
        self.show()
        self.raise_()

    def media_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self.pending_seek is not None:
            if 0 <= self.pending_audio_track < len(self.player.audioTracks()):
                self.player.setActiveAudioTrack(self.pending_audio_track)
            self.player.setPosition(self.pending_seek)
            self.pending_seek = None

    def toggle(self):
        from PySide6.QtMultimedia import QMediaPlayer
        self.player.pause() if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState else self.player.play()

    def update_position(self, ms):
        if not self.seek_bar.isSliderDown():
            self.seek_bar.setValue(ms)
        self.position.setText(f'{duration(ms / 1000)} / {duration(self.player.duration() / 1000)}')
        transcript = self.transcript_provider()
        self.subtitle.setFont(QFont('Segoe UI', self.settings.subtitle_font))
        self.subtitle.setPlainText(subtitle_at(transcript.segments, ms / 1000, self.settings.subtitle_lines) if transcript and self.enabled.isChecked() else '')
        self.subtitle.setPos(40, max(0, 540 - self.settings.subtitle_margin - self.subtitle.boundingRect().height()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view.fitInView(self.video.boundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def closeEvent(self, event):
        self.player.stop()
        self.player.setSource(QUrl())
        self.pending_seek = None
        super().closeEvent(event)
