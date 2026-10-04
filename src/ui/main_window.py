import json
import logging
import os
from dataclasses import replace
from pathlib import Path
import time
import psutil
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                              QComboBox, QCheckBox, QSpinBox, QSplitter, QScrollArea, QFrame,
                              QProgressBar, QFileDialog, QMessageBox, QDialog, QDialogButtonBox,
                              QPlainTextEdit, QApplication)
from PySide6.QtWidgets import QSizePolicy
from exporters.formats import export_file, FORMATS
from hardware.detection import detect, choose, LABELS, sensible_threads
from media.ffmpeg import inspect, EXTENSIONS
from settings.store import Settings
from transcription.models import ModelManager, CATALOG
from transcription.pipeline import transcribe
from utils.paths import resource_root
from utils.types import Transcript, Options, AppError
from .settings_dialog import SettingsDialog
from .theme import apply_theme
from .transcript import TranscriptView
from .widgets import DropArea, label, button, card, duration, size
from .workers import Job, MonitorThread

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, paths):
        super().__init__()
        self.paths = paths
        self.settings = Settings.load(paths.settings)
        self.models = ModelManager(paths.models)
        self.backends = {}
        self.media = None
        self.job = None
        self.result = None
        self.unsaved = False
        self.started_at = 0
        self.closing = False
        self.last_options = None
        self.setWindowTitle('Local Transcriber')
        self.resize(1270, 900)
        self.setMinimumSize(920, 700)
        icon = resource_root() / 'assets' / 'app.svg'
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        apply_theme(QApplication.instance(), self.settings.theme)
        self.build_ui()
        self.apply_defaults()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.monitor = MonitorThread(self)
        self.monitor.sample.connect(self.resource_sample)
        self.monitor.start()
        self.statusBar().showMessage('Private by design · No accounts needed for transcription · No telemetry')
        QTimer.singleShot(0, self.refresh_hardware)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=lambda: self.viewer.find_text.setFocus())
        QShortcut(QKeySequence('Ctrl+O'), self, activated=self.browse)
        QShortcut(QKeySequence('Ctrl+S'), self, activated=self.export)

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.setSpacing(16)
        header = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(4)
        title.addWidget(label('Local Transcriber', 'title'))
        title.addWidget(label('Turn recordings into words. Keep everything on your device.', 'muted'))
        header.addLayout(title)
        header.addStretch()
        header.addWidget(label('●  LOCAL PROCESSING', 'badge'))
        self.settings_button = button('Settings', self.open_settings)
        header.addWidget(self.settings_button)
        outer.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)
        self.drop = DropArea()
        self.drop.browse.connect(self.browse)
        self.drop.file_dropped.connect(self.import_file)
        left_layout.addWidget(self.drop)
        self.metadata = label('MP4, MKV, MOV, WAV, MP3, FLAC and more', 'muted')
        self.metadata.setWordWrap(True)
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left_layout.addWidget(self.metadata)
        settings_card, settings_layout = card()
        settings_layout.addWidget(label('Transcription', 'section'))
        form = QFormLayout()
        form.setSpacing(11)
        self.stream = QComboBox()
        self.stream.setToolTip('Choose which audio track to transcribe')
        form.addRow('Audio track', self.stream)
        self.model = QComboBox()
        self.model.setAccessibleName('Whisper model')
        self.refresh_models()
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        self.download_button = button('Download', self.download_selected)
        model_row.addWidget(self.download_button)
        form.addRow('Model', model_row)
        self.model_info = label('', 'muted')
        self.model_info.setWordWrap(True)
        form.addRow(self.model_info)
        self.language = QComboBox()
        self.language.addItem('Auto Detect', 'auto')
        languages = json.loads((Path(__file__).parents[1] / 'transcription/languages.json').read_text('utf-8'))
        for code, name in sorted(languages.items(), key=lambda item: item[1]):
            self.language.addItem(name, code)
        form.addRow('Language', self.language)
        self.task = QComboBox()
        self.task.addItem('Transcribe in original language', 'transcribe')
        self.task.addItem('Translate speech to English', 'translate')
        form.addRow('Task', self.task)
        self.acceleration = QComboBox()
        self.acceleration.addItem('Auto · detecting runtimes…', 'auto')
        form.addRow('Acceleration', self.acceleration)
        for combo in (self.stream, self.model, self.language, self.task, self.acceleration):
            combo.setMinimumContentsLength(7)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.setMinimumWidth(0)
        settings_layout.addLayout(form)
        self.device = label('Detecting local hardware…', 'muted')
        self.device.setWordWrap(True)
        settings_layout.addWidget(self.device)
        self.words = QCheckBox('Word timestamps')
        self.words.setToolTip('Approximate token-based word timing. Adds processing cost; available in JSON.')
        self.suppress = QCheckBox('Suppress non-speech tokens')
        self.suppress.setChecked(True)
        self.suppress.setToolTip('Whisper suppresses non-speech tokens. Punctuation is produced by the model. No arbitrary filler deletion.')
        self.diarization = QCheckBox('Speaker diarization · optional')
        self.keywords = QCheckBox('Extract keywords')
        settings_layout.addWidget(self.words)
        settings_layout.addWidget(self.suppress)
        settings_layout.addWidget(self.diarization)
        self.speaker_mode = QComboBox()
        self.speaker_mode.addItems(['Automatic speakers', 'Exact speaker count', 'Speaker count range'])
        speaker_row = QHBoxLayout()
        speaker_row.addWidget(self.speaker_mode, 1)
        self.speaker_min = QSpinBox()
        self.speaker_min.setRange(1, 50)
        self.speaker_min.setValue(2)
        self.speaker_max = QSpinBox()
        self.speaker_max.setRange(1, 50)
        self.speaker_max.setValue(6)
        self.speaker_min.setAccessibleName('Exact or minimum speaker count')
        self.speaker_max.setAccessibleName('Maximum speaker count')
        speaker_row.addWidget(self.speaker_min)
        speaker_row.addWidget(self.speaker_max)
        settings_layout.addLayout(speaker_row)
        settings_layout.addWidget(self.keywords)
        left_layout.addWidget(settings_card)
        self.start_button = button('Start Transcription', self.start, primary=True)
        left_layout.addWidget(self.start_button)
        left_layout.addStretch()
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        scroll.setMinimumWidth(340)
        splitter.addWidget(scroll)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(14)
        progress_card, progress_layout = card()
        progress_top = QHBoxLayout()
        self.stage = label('Ready when you are', 'section')
        self.percent = label('—', 'section')
        progress_top.addWidget(self.stage)
        progress_top.addStretch()
        progress_top.addWidget(self.percent)
        progress_layout.addLayout(progress_top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        self.progress.setToolTip('Overall pipeline progress. Stage percentages are based on actual work; unknown stages are indeterminate.')
        progress_layout.addWidget(self.progress)
        self.detail = label('Import a recording to begin', 'muted')
        self.detail.setWordWrap(True)
        progress_layout.addWidget(self.detail)
        progress_footer = QHBoxLayout()
        self.elapsed = label('Elapsed 00:00', 'muted')
        progress_footer.addWidget(self.elapsed)
        progress_footer.addStretch()
        self.pause_button = button('Pause')
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip('Pause/resume is not safe in this whisper.cpp runtime. Cancel and restart instead.')
        self.cancel_button = button('Cancel', self.cancel)
        self.cancel_button.setEnabled(False)
        progress_footer.addWidget(self.pause_button)
        progress_footer.addWidget(self.cancel_button)
        progress_layout.addLayout(progress_footer)
        right_layout.addWidget(progress_card)
        monitor_card, monitor_layout = card()
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, title in [('cpu', 'CPU'), ('ram', 'RAM'), ('gpu', 'GPU'), ('vram', 'VRAM')]:
            group = QVBoxLayout()
            group.setSpacing(4)
            group.addWidget(label(title, 'muted'))
            value = label('N/A', 'metric')
            self.metrics[key] = value
            group.addWidget(value)
            metrics.addLayout(group, 1)
        monitor_layout.addLayout(metrics)
        self.monitor_detail = label('Actual system usage · GPU readings are best-effort', 'muted')
        monitor_layout.addWidget(self.monitor_detail)
        right_layout.addWidget(monitor_card)
        viewer_card, viewer_layout = card()
        self.viewer = TranscriptView()
        self.viewer.changed.connect(self.edited)
        viewer_layout.addWidget(self.viewer, 1)
        self.keyword_panel = QFrame()
        keyword_layout = QVBoxLayout(self.keyword_panel)
        keyword_layout.setContentsMargins(0, 4, 0, 4)
        keyword_layout.addWidget(label('Keywords', 'section'))
        self.keyword_text = label('')
        self.keyword_text.setWordWrap(True)
        self.keyword_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.keyword_method = label('', 'muted')
        keyword_layout.addWidget(self.keyword_text)
        keyword_layout.addWidget(self.keyword_method)
        self.keyword_panel.hide()
        viewer_layout.addWidget(self.keyword_panel)
        export_row = QHBoxLayout()
        self.export_timestamps = QCheckBox('Timestamps')
        self.export_timestamps.setChecked(self.settings.timestamp_export)
        self.export_speakers = QCheckBox('Speaker labels')
        self.export_speakers.setChecked(self.settings.speaker_export)
        export_row.addWidget(self.export_timestamps)
        export_row.addWidget(self.export_speakers)
        export_row.addStretch()
        self.export_all_button = button('Export all', lambda: self.export(all_formats=True))
        self.export_button = button('Export…', self.export, primary=True)
        self.export_button.setEnabled(False)
        self.export_all_button.setEnabled(False)
        export_row.addWidget(self.export_all_button)
        export_row.addWidget(self.export_button)
        viewer_layout.addLayout(export_row)
        right_layout.addWidget(viewer_card, 1)
        splitter.addWidget(right)
        splitter.setSizes([375, 820])
        outer.addWidget(splitter, 1)
        self.model.currentIndexChanged.connect(self.update_model_info)
        self.acceleration.currentIndexChanged.connect(self.update_device)
        self.diarization.toggled.connect(self.diarization_changed)
        self.speaker_mode.currentIndexChanged.connect(self.speaker_controls)
        self.speaker_controls()

    def refresh_models(self):
        previous = self.model.currentData() if hasattr(self, 'model') else 'base'
        self.model.blockSignals(True)
        self.model.clear()
        for name in CATALOG:
            installed = self.models.installed(name)
            self.model.addItem(name.replace('.en', ' · English only').title() + (' · Installed' if installed else ' · Download'), name)
        self.model.setCurrentIndex(max(0, self.model.findData(previous or 'base')))
        self.model.blockSignals(False)
        if hasattr(self, 'model_info'):
            self.update_model_info()

    def update_model_info(self):
        name = self.model.currentData() or 'base'
        info = CATALOG[name]
        note = 'Fast · lower accuracy' if name.startswith('tiny') else 'Balanced · recommended default' if name.startswith('base') else 'Higher accuracy · more memory' if name != 'turbo' else 'Fast large model · transcription only'
        self.model_info.setText(f"{note}\n{size(info['size'])} download · ~{info['memory_mb'] / 1024:.1f} GB working memory")
        self.download_button.setText('Verify' if self.models.installed(name) else 'Download')

    def apply_defaults(self):
        model_index = self.model.findData(self.settings.model)
        self.model.setCurrentIndex(model_index if model_index >= 0 else self.model.findData('base'))
        self.language.setCurrentIndex(max(0, self.language.findData(self.settings.language)))
        self.words.setChecked(self.settings.words)
        if self.settings.optional_python:
            os.environ['LOCAL_TRANSCRIBER_OPTIONAL_PYTHON'] = self.settings.optional_python
        else:
            os.environ.pop('LOCAL_TRANSCRIBER_OPTIONAL_PYTHON', None)
        self.update_model_info()

    def launch_job(self, action, result, kind):
        if self.job:
            return False
        self.job_kind = kind
        self.started_at = time.monotonic()
        self.job = Job(action, self)
        self.job.event.connect(self.handle_event)
        self.job.result.connect(result)
        self.job.error.connect(self.failed)
        self.job.cancelled.connect(self.cancelled)
        self.job.finished.connect(self.job_finished)
        self.set_busy(True)
        self.job.start()
        return True

    def set_busy(self, busy):
        self.drop.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        for widget in (self.model, self.language, self.task, self.acceleration, self.stream, self.words,
                       self.suppress, self.diarization, self.keywords, self.speaker_mode, self.speaker_min, self.speaker_max, self.download_button):
            widget.setEnabled(not busy)
        self.start_button.setEnabled(not busy and self.media is not None and bool(self.backends))
        self.cancel_button.setEnabled(busy)
        self.export_button.setEnabled(not busy and self.result is not None)
        self.export_all_button.setEnabled(not busy and self.result is not None)
        self.viewer.set_editable(not busy and self.result is not None)
        if not busy:
            self.speaker_controls()

    def refresh_hardware(self):
        self.stage.setText('Detecting hardware')
        self.launch_job(lambda cancel, event: detect(self.paths), self.hardware_ready, 'hardware')

    def hardware_ready(self, backends):
        self.backends = backends
        self.acceleration.blockSignals(True)
        self.acceleration.clear()
        self.acceleration.addItem('Auto · recommended', 'auto')
        for name in ('cuda', 'vulkan', 'hip', 'cpu'):
            info = backends[name]
            self.acceleration.addItem(LABELS[name] + ('' if info.available else ' · unavailable'), name)
            item = self.acceleration.model().item(self.acceleration.count() - 1)
            if not info.available:
                item.setEnabled(False)
                item.setToolTip(info.reason)
        selected = self.settings.acceleration
        if selected != 'auto' and (not backends.get(selected) or not backends[selected].available):
            selected = 'auto'
        self.acceleration.setCurrentIndex(max(0, self.acceleration.findData(selected)))
        self.acceleration.blockSignals(False)
        self.update_device()
        self.stage.setText('Ready when you are')

    def update_device(self):
        if not self.backends:
            return
        try:
            backend = choose(self.backends, self.acceleration.currentData() or 'auto')
            data = backend.device
            memory = data.get('memory_total', 0)
            self.device.setText(f"{data.get('name', 'Detected device')}\nBackend: {LABELS[backend.name]}" +
                                (f" · VRAM: {size(memory)}" if backend.name != 'cpu' and memory else
                                 f" · Threads: {self.settings.threads or sensible_threads()}" if backend.name == 'cpu' else ''))
            self.active_backend = backend
        except AppError as exc:
            self.device.setText(str(exc))
            self.active_backend = None

    def browse(self):
        if self.job:
            return
        file, _ = QFileDialog.getOpenFileName(self, 'Choose a recording', self.settings.last_input_dir,
                                            'Video and audio (' + ' '.join('*.' + extension for extension in EXTENSIONS) + ');;All files (*)')
        if file:
            self.import_file(file)

    def import_file(self, path):
        if self.job:
            return
        self.stage.setText('Inspecting media')
        self.detail.setText(Path(path).name)
        self.launch_job(lambda cancel, event: inspect(path, cancel), self.media_ready, 'inspect')

    def media_ready(self, info):
        self.media = info
        self.drop.title.setText(Path(info.path).name)
        self.drop.title.setToolTip(info.path)
        self.metadata.setText(f"{Path(info.path).suffix.lstrip('.').upper() or info.format} · {size(info.size)} · {duration(info.duration) if info.duration else 'Duration determined during decoding'}")
        self.metadata.setToolTip('Detected container: ' + info.format)
        self.stream.clear()
        for stream in info.streams:
            self.stream.addItem(f"Track {stream.index} · {stream.codec.upper()} · {stream.rate:,} Hz · {stream.channels} ch" +
                                (f' · {stream.language}' if stream.language else '') + (f' · {stream.title}' if stream.title else ''), stream.index)
        if self.settings.remember_folder:
            self.settings.last_input_dir = str(Path(info.path).parent)
            self.save_settings()
        self.stage.setText('Ready to transcribe')
        self.detail.setText('Selected audio track will be decoded locally to 16 kHz mono.')
        self.progress.setValue(0)
        self.percent.setText('—')

    def options(self):
        speaker_mode = self.speaker_mode.currentIndex()
        return Options(model=self.model.currentData(), language=self.language.currentData(), task=self.task.currentData(),
                       acceleration=self.acceleration.currentData(), threads=self.settings.threads,
                       beam_size=self.settings.beam_size, temperature=self.settings.temperature, words=self.words.isChecked(),
                       suppress_non_speech=self.suppress.isChecked(), stream=self.stream.currentData(),
                       diarization=self.diarization.isChecked(), speaker_count=self.speaker_min.value() if speaker_mode == 1 else 0,
                       min_speakers=self.speaker_min.value() if speaker_mode == 2 else 0,
                       max_speakers=self.speaker_max.value() if speaker_mode == 2 else 0,
                       diarization_path=self.settings.diarization_path, keywords=self.keywords.isChecked(),
                       keyword_method=self.settings.keyword_method, keyword_count=self.settings.keyword_count,
                       phrase_min=self.settings.phrase_min, phrase_max=self.settings.phrase_max, diversity=self.settings.diversity,
                       keyword_path=self.settings.keyword_path, keep_temp=self.settings.keep_temp)

    def start(self, checked=False, override=None):
        if self.job or not self.media:
            return
        if self.unsaved and QMessageBox.question(self, 'Start a new transcript', 'The current transcript has unexported changes. Replace it with a new transcription?') != QMessageBox.StandardButton.Yes:
            return
        options = override or self.options()
        try:
            from translation.tasks import validate_task
            validate_task(options.model, options.task)
            backend = choose(self.backends, options.acceleration)
            if options.min_speakers > options.max_speakers:
                raise AppError('Minimum speakers must not exceed maximum speakers.')
        except AppError as exc:
            QMessageBox.warning(self, 'Check transcription settings', str(exc))
            return
        free = backend.device.get('memory_free') if backend.name != 'cpu' else psutil.virtual_memory().available
        required = CATALOG[options.model]['memory_mb'] * 1024 ** 2
        if free and required > free:
            if QMessageBox.question(self, 'Available memory', f"This model may need approximately {size(required)} of memory.\nAvailable: {size(free)}.\nContinue anyway?") != QMessageBox.StandardButton.Yes:
                return
        self.last_options = options
        self.unsaved = False
        self.result = None
        self.viewer.set_transcript(Transcript(self.media.path, options.model, options.language, self.media.duration), editable=False)
        self.keyword_panel.hide()
        self.progress.setValue(0)
        self.percent.setText('—')
        self.stage.setText('Preparing transcription')
        self.detail.setText('Models are downloaded only when required; installed models are verified locally.')
        self.launch_job(lambda cancel, event: transcribe(self.media.path, options, self.paths, self.settings,
                                                        self.models, self.backends, cancel, event), self.completed, 'transcribe')

    def handle_event(self, event):
        kind = event.get('event')
        if kind == 'segment':
            self.viewer.append(event['segment'])
            return
        if kind == 'metadata':
            return
        if kind == 'device':
            capacity = event['device'].get('memory_total')
            suffix = f' · VRAM: {size(capacity)}' if event['backend'] != 'cpu' and capacity else f' · Threads: {self.last_options.threads or sensible_threads()}' if event['backend'] == 'cpu' and self.last_options else ''
            self.device.setText(event['device']['name'] + '\nBackend: ' + LABELS[event['backend']] + suffix)
            return
        stage = event.get('stage')
        if stage:
            self.stage.setText(stage + (' · translating to English' if self.last_options and self.last_options.task == 'translate' and stage == 'Transcribing' else ''))
        overall = event.get('overall')
        fraction = event.get('fraction')
        if overall is not None:
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(overall * 1000))
        elif fraction is None:
            self.progress.setRange(0, 0)
        self.percent.setText(f'{fraction:.0%}' if fraction is not None else '…')
        if event.get('downloaded') is not None:
            self.detail.setText(f"{size(event['downloaded'])} / {size(event['total'])} · {size(event['speed'])}/s · selected model only")
        elif kind == 'progress' and event.get('processed') is not None:
            speed = event.get('speed', 0)
            self.detail.setText(f"{duration(event['processed'])} / {duration(event['total'])} · {speed:.2f}× realtime" +
                                (f" · ETA {duration(event['eta'])}" if event.get('eta') is not None else ''))
        elif stage:
            self.detail.setText('Working locally · progress is indeterminate for this stage' if fraction is None else f'{fraction:.0%} of this stage complete')

    def completed(self, transcript):
        self.result = transcript
        self.unsaved = True
        self.viewer.set_transcript(transcript)
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        self.percent.setText('100%')
        self.stage.setText('Transcript ready')
        self.detail.setText(f'{duration(transcript.duration)} · {len(transcript.segments):,} cues · edit before exporting')
        if transcript.keywords:
            self.keyword_panel.show()
            self.keyword_text.setText('   ·   '.join(transcript.keywords))
            self.keyword_method.setText(transcript.keyword_method)
        if transcript.warnings:
            QMessageBox.warning(self, 'Transcript available', '\n\n'.join(transcript.warnings))

    def failed(self, message):
        self.stage.setText('Operation could not finish')
        self.detail.setText('Your installed models remain cached. See logs for technical details.')
        self.progress.setRange(0, 1000)
        self.retain_partial('Incomplete transcription · processing failed')
        dialog = QMessageBox(QMessageBox.Icon.Warning, 'Local Transcriber', message, parent=self)
        dialog.addButton('Close', QMessageBox.ButtonRole.RejectRole)
        retry = None
        if self.job_kind == 'transcribe' and self.last_options and self.last_options.acceleration != 'cpu' and self.backends.get('cpu') and self.backends['cpu'].available:
            retry = dialog.addButton('Retry on CPU', QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()
        if retry and dialog.clickedButton() == retry:
            options = replace(self.last_options, acceleration='cpu')
            self.unsaved = False
            QTimer.singleShot(100, lambda: self.retry_when_idle(options))

    def retry_when_idle(self, options):
        if self.job:
            QTimer.singleShot(100, lambda: self.retry_when_idle(options))
        else:
            self.start(override=options)

    def retain_partial(self, warning):
        if self.job_kind == 'transcribe' and self.viewer.transcript and self.viewer.transcript.segments:
            self.result = self.viewer.transcript
            self.result.warnings.append(warning)
            self.unsaved = True

    def cancelled(self):
        self.stage.setText('Cancelled')
        self.detail.setText('Child processes stopped. Temporary data removed unless debugging is enabled.')
        self.percent.setText('—')
        self.progress.setRange(0, 1000)
        self.retain_partial('Incomplete transcription · cancelled by user')

    def job_finished(self):
        job = self.job
        self.job = None
        job.deleteLater()
        self.set_busy(False)
        self.refresh_models()
        if self.closing:
            QTimer.singleShot(0, self.close)

    def cancel(self):
        if self.job:
            self.cancel_button.setEnabled(False)
            self.stage.setText('Cancelling…')
            self.job.cancel()

    def download_selected(self):
        if self.job:
            return
        name = self.model.currentData()

        def action(cancel, progress):
            with self.models.lease(name):
                return self.models.ensure(name, cancel, progress)
        self.stage.setText(f'Preparing {name}')
        self.launch_job(action, lambda path: (self.stage.setText('Model ready'), self.detail.setText(f'{name} installed and SHA-256 verified'),
                                              self.progress.setRange(0, 1000), self.progress.setValue(1000), self.percent.setText('100%')), 'download')

    def speaker_controls(self):
        enabled = self.diarization.isChecked() and self.job is None
        self.speaker_mode.setEnabled(enabled)
        self.speaker_mode.setVisible(self.diarization.isChecked())
        self.speaker_min.setVisible(self.diarization.isChecked() and self.speaker_mode.currentIndex() > 0)
        self.speaker_max.setVisible(self.diarization.isChecked() and self.speaker_mode.currentIndex() == 2)
        self.speaker_min.setEnabled(enabled)
        self.speaker_max.setEnabled(enabled)

    def diarization_changed(self, enabled):
        self.speaker_controls()
        if enabled and not Path(self.settings.diarization_path or '__missing__').is_dir():
            QMessageBox.information(self, 'Speaker diarization setup', 'Speaker diarization is optional. Configure a local Community-1 model and the pyannote.audio environment in Settings → Advanced features. Normal transcription works without it.')
            self.diarization.setChecked(False)
            self.open_settings(advanced=True)

    def resource_sample(self, data):
        self.metrics['cpu'].setText(f"{data['cpu']:.0f}%")
        self.metrics['ram'].setText(f"{data['ram_used'] / 1024**3:.1f} / {data['ram_total'] / 1024**3:.0f} GB")
        self.metrics['gpu'].setText(f"{data['gpu']:.0f}%" if data.get('gpu') is not None else 'N/A')
        total = data.get('vram_total')
        backend = getattr(self, 'active_backend', None)
        # Windows counters cover all adapters; avoid pairing a system allocation with a guessed total.
        self.metrics['vram'].setText((f"{data['vram_used'] / 1024**3:.1f}" + (f" / {total / 1024**3:.0f}" if total else '') + ' GB') if data.get('vram_used') is not None else 'N/A')
        scope = data.get('gpu_scope', 'GPU telemetry unavailable')
        self.metrics['gpu'].setToolTip(scope)
        self.metrics['vram'].setToolTip(scope)
        self.monitor_detail.setText(f"App RAM {size(data['app_ram'])}" + (f" · GPU {data['temperature']:.0f}°C" if data.get('temperature') is not None else '') + ' · system usage')

    def tick(self):
        if self.job and self.started_at:
            self.elapsed.setText('Elapsed ' + duration(time.monotonic() - self.started_at))

    def edited(self):
        self.unsaved = True

    def export(self, checked=False, all_formats=False):
        if self.job or self.result is None:
            return
        # End an active table-cell editor before snapshotting the user's text.
        self.export_button.setFocus()
        self.viewer.sync()
        snapshot = replace(self.result, segments=[replace(segment) for segment in self.result.segments], speaker_names=dict(self.result.speaker_names))
        stem = Path(self.result.source).stem
        base = self.settings.output_dir or str(Path(self.result.source).parent)
        if all_formats:
            directory = QFileDialog.getExistingDirectory(self, 'Export all formats', base)
            if not directory:
                return
            targets = [Path(directory) / (stem + '.' + fmt) for fmt in FORMATS]
        else:
            filters = ';;'.join(f'{fmt.upper()} (*.{fmt})' for fmt in FORMATS)
            file, selected_filter = QFileDialog.getSaveFileName(self, 'Export transcript', str(Path(base) / (stem + '.txt')), filters,
                                                              options=QFileDialog.Option.DontConfirmOverwrite)
            if not file:
                return
            target = Path(file)
            if not target.suffix:
                target = target.with_suffix('.' + selected_filter.split(' ', 1)[0].lower())
            targets = [target]
            directory = str(target.parent)
        collisions = [path.name for path in targets if path.exists()]
        approved_paths = {path for path in targets if path.exists()}
        overwrite = False
        if collisions:
            overwrite = QMessageBox.question(self, 'Replace existing exports', 'Replace these files?\n\n' + '\n'.join(collisions)) == QMessageBox.StandardButton.Yes
            if not overwrite:
                return
        timestamps, speakers = self.export_timestamps.isChecked(), self.export_speakers.isChecked()
        self.settings.output_dir = directory
        self.settings.timestamp_export, self.settings.speaker_export = timestamps, speakers
        self.save_settings()

        def action(cancel, event):
            written = []
            for path in targets:
                cancel.check()
                export_file(path, snapshot, timestamps, speakers, overwrite and path in approved_paths)
                written.append(str(path))
                event({'stage': 'Exporting', 'fraction': len(written) / len(targets), 'overall': len(written) / len(targets)})
            return written

        def done(written):
            self.unsaved = False
            self.stage.setText('Export complete')
            self.detail.setText(f"Saved {len(written)} file(s) to {directory}")
            self.statusBar().showMessage('Saved: ' + ', '.join(Path(path).name for path in written), 10000)
        self.launch_job(action, done, 'export')

    def open_settings(self, checked=False, advanced=False):
        if self.job:
            return
        dialog = SettingsDialog(self.settings, self.paths, self.models, self.backends, self)
        if advanced:
            dialog.tabs.setCurrentIndex(4)
        if dialog.exec():
            self.settings = dialog.settings
            self.save_settings()
            apply_theme(QApplication.instance(), self.settings.theme)
            self.apply_defaults()
            self.acceleration.setCurrentIndex(max(0, self.acceleration.findData(self.settings.acceleration)))
            self.update_device()
        self.refresh_models()

    def save_settings(self):
        try:
            self.settings.save(self.paths.settings)
        except OSError:
            log.exception('Cannot save settings')
            self.statusBar().showMessage('Settings could not be saved. Check application-data permissions.', 10000)

    def closeEvent(self, event):
        if self.job:
            if not self.closing and QMessageBox.question(self, 'Cancel and close', 'An operation is running. Cancel it and close once resources have been released?') != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.closing = True
            self.cancel()
            event.ignore()
            return
        if self.unsaved and not self.closing:
            if QMessageBox.question(self, 'Unexported transcript', 'The transcript has unexported changes. Close without exporting?') != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.save_settings()
        self.monitor.stop()
        if self.monitor.isRunning():
            event.ignore()
            self.closing = True
            QTimer.singleShot(100, self.close)
            return
        event.accept()
