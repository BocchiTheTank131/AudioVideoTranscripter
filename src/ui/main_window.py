import json
import logging
import os
from dataclasses import replace, asdict
from copy import deepcopy
from pathlib import Path
import time
import psutil
from PySide6.QtCore import Qt, QTimer, QUrl, QEvent
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                              QComboBox, QCheckBox, QSpinBox, QSplitter, QScrollArea, QFrame,
                              QProgressBar, QFileDialog, QMessageBox, QDialog, QDialogButtonBox,
                              QPlainTextEdit, QApplication)
from PySide6.QtWidgets import QLineEdit, QTextEdit
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
from local_workflow.history import History
from local_workflow.queue import Queue
from local_workflow.projects import save_project, load_project
from transcription.cleanup import export_snapshot
from hardware.recommendations import recommend, preset_values
from media.languages import metadata_language
from utils.version import VERSION

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
        self.project_path = None
        self.preview = None
        self.queue = Queue()
        self.history = History(paths.history)
        self.history_run = None
        self.batch_output = None
        self.preset_changing = False
        self.setWindowTitle('Local Transcriber ' + VERSION)
        self.resize(1270, 900)
        self.setMinimumSize(920, 700)
        icon = resource_root() / 'assets' / 'app.svg'
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        apply_theme(QApplication.instance(), self.settings.theme)
        self.build_ui()
        self.build_menus()
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
        QShortcut(QKeySequence('Ctrl+S'), self, activated=self.save_current_project)
        QShortcut(QKeySequence('Ctrl+Shift+S'), self, activated=lambda: self.save_current_project(save_as=True))
        QShortcut(QKeySequence('Ctrl+E'), self, activated=self.export)
        QShortcut(QKeySequence('Ctrl+H'), self, activated=lambda: self.viewer.replace_text.setFocus())
        QApplication.instance().installEventFilter(self)
        QShortcut(QKeySequence('Escape'), self, activated=lambda: self.preview.player.stop() if self.preview else None)
        QShortcut(QKeySequence('Ctrl+C'), self, activated=self.copy_selected)

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
        header.addWidget(button('Preview', self.show_preview))
        header.addWidget(button('Batch', self.show_queue))
        header.addWidget(button('History', self.show_history))
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
        self.drop.files_dropped.connect(self.add_queue_files)
        left_layout.addWidget(self.drop)
        self.metadata = label('MP4, MKV, MOV, WAV, MP3, FLAC and more', 'muted')
        self.metadata.setWordWrap(True)
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left_layout.addWidget(self.metadata)
        settings_card, settings_layout = card()
        settings_layout.addWidget(label('Transcription', 'section'))
        form = QFormLayout()
        form.setSpacing(11)
        self.preset = QComboBox()
        for key in ('custom', 'fast', 'balanced', 'accurate'):
            self.preset.addItem(key.title(), key)
        form.addRow('Preset', self.preset)
        self.stream = QComboBox()
        self.stream.setToolTip('Choose which audio track to transcribe')
        form.addRow('Audio track', self.stream)
        self.track_metadata = label('Metadata language: —', 'muted')
        form.addRow(self.track_metadata)
        self.stream.currentIndexChanged.connect(self.update_track_metadata)
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
        languages = json.loads((resource_root() / 'transcription/languages.json' if getattr(__import__('sys'), 'frozen', False) else Path(__file__).parents[1] / 'transcription/languages.json').read_text('utf-8'))
        self.languages = languages
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
        self.detected_language = label('Detected speech: —', 'muted')
        settings_layout.addWidget(self.detected_language)
        self.vad = QCheckBox('Voice activity detection')
        self.vad.setChecked(self.settings.vad)
        self.vad.setToolTip('Local Silero speech detection. Optional 0.9 MB model; no automatic startup download. Music may still trigger speech detection.')
        settings_layout.addWidget(self.vad)
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
        left_pane = QWidget()
        pane_layout = QVBoxLayout(left_pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.addWidget(scroll, 1)
        left_layout.removeWidget(self.start_button)
        pane_layout.addWidget(self.start_button)
        splitter.addWidget(left_pane)
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
        self.viewer.seek_requested.connect(self.show_preview)
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
        self.export_mode = QComboBox()
        self.export_mode.addItem('Cleaned cues', 'cleaned')
        self.export_mode.addItem('Original cues + edits', 'original')
        self.export_mode.setToolTip('Original cue boundaries with your edits applied. Raw recognized text remains in project files.')
        export_row.addWidget(self.export_mode)
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
        self.preset.currentIndexChanged.connect(self.preset_selected)
        self.model.activated.connect(lambda _: self.mark_custom())
        self.words.clicked.connect(self.mark_custom)
        self.vad.clicked.connect(self.mark_custom)

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
        self.vad.setChecked(self.settings.vad)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(max(0, self.preset.findData(self.settings.preset)))
        self.preset.blockSignals(False)
        self.viewer.follow.setChecked(self.settings.follow_live)
        self.viewer.show_confidence.setChecked(self.settings.show_confidence)
        self.export_mode.setCurrentIndex(max(0, self.export_mode.findData(self.settings.export_segmentation)))
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
        self.cancel_button.setEnabled(busy and getattr(self, 'job_kind', '') in ('transcribe', 'batch', 'export', 'download'))
        self.pause_button.setEnabled(False)
        self.preset.setEnabled(not busy)
        self.vad.setEnabled(not busy)
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
        if self.settings.preset != 'custom':
            self.preset_selected()
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
            recommended = recommend(backend, psutil.virtual_memory().available)['balanced']
            self.device.setToolTip(f'{recommended.title()} recommended for current available memory. Advisory only; model sizes depend on decoding settings.')
            for i in range(self.model.count()):
                name = self.model.itemData(i)
                self.model.setItemText(i, name.replace('.en', ' · English only').title() + (' · Installed' if self.models.installed(name) else ' · Download') + (' · Recommended' if name == recommended else ''))
        except AppError as exc:
            self.device.setText(str(exc))
            self.active_backend = None

    def browse(self):
        if self.job:
            return
        files, _ = QFileDialog.getOpenFileNames(self, 'Choose a recording', self.settings.last_input_dir,
                                            'Video and audio (' + ' '.join('*.' + extension for extension in EXTENSIONS) + ');;All files (*)')
        if len(files) > 1:
            self.add_queue_files(files)
        elif files:
            self.import_file(files[0])

    def import_file(self, path):
        if self.job:
            return
        self.stage.setText('Inspecting media')
        self.detail.setText(Path(path).name)
        self.launch_job(lambda cancel, event: inspect(path, cancel), self.media_ready, 'inspect')

    def media_ready(self, info):
        if self.result and self.project_path and not Path(self.result.source).is_file():
            self.result.source = info.path
            self.unsaved = True
        self.media = info
        self.drop.title.setText(Path(info.path).name)
        self.drop.title.setToolTip(info.path)
        self.metadata.setText(f"{Path(info.path).suffix.lstrip('.').upper() or info.format} · {size(info.size)} · {duration(info.duration) if info.duration else 'Duration determined during decoding'}")
        self.metadata.setToolTip('Detected container: ' + info.format)
        self.stream.clear()
        for stream in info.streams:
            self.stream.addItem(f"Track {stream.index} · {stream.codec.upper()} · {stream.rate:,} Hz · {stream.channels} ch" +
                                (f' · Metadata language: {metadata_language(stream.language, self.languages)}') + (f' · {stream.title}' if stream.title else ''), stream.index)
            self.stream.setItemData(self.stream.count() - 1, self.stream.itemText(self.stream.count() - 1), Qt.ItemDataRole.ToolTipRole)
        self.update_track_metadata()
        self.detected_language.setText('Detected speech: — (shown after Whisper detects speech)')
        if self.preview:
            self.preview.player.stop()
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
                       keyword_path=self.settings.keyword_path, keep_temp=self.settings.keep_temp,
                       vad=self.vad.isChecked(), vad_path=str(self.paths.models / 'vad' / 'ggml-silero-v6.2.0.bin'),
                       auto_clean=self.settings.auto_clean, merge_short=self.settings.merge_short,
                       merge_gap=self.settings.merge_gap, subtitle_chars=self.settings.subtitle_chars,
                       preset=self.preset.currentData())

    def update_track_metadata(self):
        stream = next((s for s in self.media.streams if s.index == self.stream.currentData()), None) if self.media else None
        self.track_metadata.setText('Metadata language: ' + (metadata_language(stream.language, getattr(self, 'languages', {})) if stream else '—'))

    def start(self, checked=False, override=None):
        if self.job or not self.media:
            return
        if self.unsaved and QMessageBox.question(self, 'Start a new transcript', 'The current transcript has unexported changes. Replace it with a new transcription?') != QMessageBox.StandardButton.Yes:
            return
        options = override or self.options()
        if self.preset.currentData() != 'custom' and not self.models.installed(options.model):
            if QMessageBox.question(self, 'Preset model download', f'{options.model.title()} is not installed. Download it ({size(CATALOG[options.model]["size"])}) when this job starts?') != QMessageBox.StandardButton.Yes:
                return
        from transcription.vad import valid, download
        if options.vad and not valid(options.vad_path):
            if QMessageBox.question(self, 'Voice activity detection setup', 'Download the 0.9 MB local Silero VAD model before transcription?\nChoose No to transcribe normally without VAD.') == QMessageBox.StandardButton.Yes:
                self.launch_job(lambda cancel, event: download(self.paths.models / 'vad', cancel, event),
                                lambda _: QTimer.singleShot(50, lambda: self.retry_when_idle(options)), 'download')
                return
            options = replace(options, vad=False)
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
        if self.project_path and self.result and self.result.source != self.media.path:
            self.project_path = None
        self.begin_history(self.media.path, options, backend, self.media.duration)
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
        if kind == 'language':
            code = event['language']
            self.detected_language.setText(f'Detected speech: {self.languages.get(code, code).title()} ({code.upper()})')
            return
        if kind == 'metadata':
            if self.queue.active:
                self.media = event['metadata']
                self.drop.title.setText(Path(self.media.path).name)
            if self.history_run:
                self.history_run['media_seconds'] = event['metadata'].duration
            return
        if kind == 'device':
            capacity = event['device'].get('memory_total')
            suffix = f' · VRAM: {size(capacity)}' if event['backend'] != 'cpu' and capacity else f' · Threads: {self.last_options.threads or sensible_threads()}' if event['backend'] == 'cpu' and self.last_options else ''
            self.device.setText(event['device']['name'] + '\nBackend: ' + LABELS[event['backend']] + suffix)
            return
        stage = event.get('stage')
        if self.queue.active:
            item = self.queue.active
            item.status = 'Exporting' if stage == 'Exporting' else 'Transcribing' if stage in ('Transcribing', 'Reviewing pathological repetition') else 'Preparing'
            item.progress = event.get('overall')
            if event.get('speed') is not None:
                item.speed = event['speed']
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
        self.record_history('completed', transcript)
        self.result = transcript
        self.unsaved = True
        self.viewer.set_transcript(transcript)
        self.detected_language.setText(f'Detected speech: {self.languages.get(transcript.language, transcript.language).title()} ({transcript.language.upper()})')
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        self.percent.setText('100%')
        self.stage.setText('Transcript ready')
        self.detail.setText(f'{duration(transcript.duration)} · {len(transcript.segments):,} cues · edit before exporting')
        if transcript.keywords:
            self.keyword_panel.show()
            self.keyword_text.setText('   ·   '.join(transcript.keywords))
            self.keyword_method.setText(transcript.keyword_method)
        else:
            self.keyword_panel.hide()
        if transcript.warnings:
            QMessageBox.warning(self, 'Transcript available', '\n\n'.join(transcript.warnings))

    def failed(self, message):
        self.record_history('failed')
        if self.queue.active:
            self.queue.finish('Failed', message.splitlines()[0])
            self.statusBar().showMessage('Batch job failed: ' + message.splitlines()[0], 10000)
            return
        self.stage.setText('Operation could not finish')
        self.detail.setText('Your installed models remain cached. See logs for technical details.')
        self.progress.setRange(0, 1000)
        self.retain_partial('Incomplete transcription · processing failed')
        summary = 'Vulkan backend failed to initialize or transcribe.' if 'vulkan' in message.lower() else message.splitlines()[0][:220]
        dialog = QMessageBox(QMessageBox.Icon.Warning, 'Local Transcriber', summary, parent=self)
        dialog.setDetailedText(message)
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
        self.record_history('cancelled')
        if self.queue.active:
            self.queue.finish('Cancelled')
            self.queue.paused = True
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
        if not self.closing and not self.queue.paused:
            QTimer.singleShot(50, self.process_queue)
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
        if self.history_run:
            self.history_run['peak_ram_bytes'] = max(self.history_run.get('peak_ram_bytes') or 0, data.get('job_ram', data['app_ram']))
            if data.get('vram_used') is not None:
                self.history_run['peak_vram_bytes'] = max(self.history_run.get('peak_vram_bytes') or 0, data['vram_used'])
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
        snapshot = export_snapshot(self.result, self.export_mode.currentData())
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
        if hasattr(self, 'viewer'):
            self.settings.follow_live = self.viewer.follow.isChecked()
            self.settings.show_confidence = self.viewer.show_confidence.isChecked()
            self.settings.export_segmentation = self.export_mode.currentData()
        try:
            self.settings.save(self.paths.settings)
        except OSError:
            log.exception('Cannot save settings')
            self.statusBar().showMessage('Settings could not be saved. Check application-data permissions.', 10000)

    def build_menus(self):
        menu = self.menuBar().addMenu('File')
        for title, callback in [('Open media…', self.browse), ('Open project…', self.open_project), ('Save project', self.save_current_project),
                                ('Save project as…', lambda: self.save_current_project(save_as=True)), ('Export…', self.export)]:
            action = QAction(title, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        menu = self.menuBar().addMenu('Tools')
        for title, callback in [('Local history', self.show_history), ('Batch queue', self.show_queue), ('Media preview', self.show_preview),
                                ('Diagnostics', lambda: self.open_settings_tab(2))]:
            action = QAction(title, self)
            action.triggered.connect(callback)
            menu.addAction(action)

    def open_settings_tab(self, index):
        if self.job:
            return
        dialog = SettingsDialog(self.settings, self.paths, self.models, self.backends, self)
        dialog.tabs.setCurrentIndex(index)
        if dialog.exec():
            self.settings = dialog.settings
            self.save_settings()
            self.apply_defaults()
            apply_theme(QApplication.instance(), self.settings.theme)

    def mark_custom(self, checked=False):
        if not self.preset_changing:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(self.preset.findData('custom'))
            self.preset.blockSignals(False)
            self.settings.preset = 'custom'

    def preset_selected(self):
        key = self.preset.currentData()
        if key == 'custom':
            self.settings.preset = key
            return
        self.preset_changing = True
        try:
            values = preset_values(key, getattr(self, 'active_backend', None), psutil.virtual_memory().available)
            self.model.setCurrentIndex(self.model.findData(values['model']))
            self.words.setChecked(values['words'])
            self.vad.setChecked(values['vad'])
            for name, value in values.items():
                setattr(self.settings, name, value)
            self.settings.preset = key
            self.statusBar().showMessage('Preset selected. Missing models download only after you approve and start.', 8000)
        finally:
            self.preset_changing = False

    def current_transcript(self):
        self.viewer.sync()
        return self.result or self.viewer.transcript

    def show_preview(self, seconds=0):
        if isinstance(seconds, bool):
            seconds = 0
        transcript = self.current_transcript()
        source = transcript.source if transcript else self.media.path if self.media else ''
        if not source:
            return
        try:
            if not self.preview:
                from .media_player import MediaPreview
                self.preview = MediaPreview(self.settings, self.current_transcript, self)
            track = next((i for i, stream in enumerate(self.media.streams) if stream.index == self.stream.currentData()), 0) if self.media and self.media.path == source else 0
            self.preview.open_media(source, seconds, track)
        except (ImportError, OSError, RuntimeError) as exc:
            log.exception('Optional media preview unavailable')
            QMessageBox.information(self, 'Media preview unavailable', 'Local playback support could not load. Transcription remains available.\n' + str(exc)[:200])

    def toggle_playback(self):
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox)):
            return
        if self.preview:
            self.preview.toggle()
        else:
            self.show_preview()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space and (self.isActiveWindow() or self.preview and self.preview.isActiveWindow()):
            focus = QApplication.focusWidget()
            if not isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox)):
                self.toggle_playback()
                return True
        return super().eventFilter(obj, event)

    def copy_selected(self):
        if QApplication.focusWidget() is self.viewer.table:
            QApplication.clipboard().setText('\n'.join(item.text() for item in self.viewer.table.selectedItems()))
        else:
            focus = QApplication.focusWidget()
            if hasattr(focus, 'copy'):
                focus.copy()

    def save_current_project(self, checked=False, save_as=False):
        if self.job or not self.result:
            return
        self.viewer.table.clearFocus()
        self.viewer.sync()
        path = str(self.project_path) if self.project_path and not save_as else ''
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, 'Save project', str(self.paths.projects / (Path(self.result.source).stem + '.ltproj')), 'Local Transcriber project (*.ltproj)')
        if not path:
            return
        path = str(Path(path).with_suffix('.ltproj'))
        try:
            save_project(path, self.result, asdict(self.options()), dict(timestamps=self.export_timestamps.isChecked(), speakers=self.export_speakers.isChecked(), segmentation=self.export_mode.currentData()))
            self.project_path = Path(path)
            self.unsaved = False
            self.statusBar().showMessage('Project saved: ' + Path(path).name, 8000)
        except (OSError, ValueError) as exc:
            log.exception('Project save failed')
            QMessageBox.warning(self, 'Project could not be saved', 'Check free disk space and write permissions.\n' + str(exc)[:200])

    def open_project(self, checked=False):
        if self.job:
            return
        if self.unsaved and QMessageBox.question(self, 'Unsaved changes', 'Open another project and discard current unsaved changes?') != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Open project', str(self.paths.projects), 'Local Transcriber project (*.ltproj)')
        if not path:
            return
        try:
            transcript, options, exports = load_project(path)
            self.project_stream = options.get('stream')
            self.media = None
            self.drop.title.setText(Path(transcript.source).name)
            self.metadata.setText('Project source: ' + ('available' if Path(transcript.source).is_file() else 'missing; use Open media to locate it'))
            for key, value in options.items():
                if hasattr(self.settings, key) and type(value) is type(getattr(self.settings, key)):
                    setattr(self.settings, key, value)
            self.project_path = Path(path)
            self.words.setChecked(options.get('words', self.settings.words))
            self.vad.setChecked(options.get('vad', self.settings.vad))
            self.diarization.setChecked(bool(options.get('diarization', False) and self.settings.diarization_path))
            self.keywords.setChecked(options.get('keywords', False))
            for widget, key in [(self.model, 'model'), (self.language, 'language'), (self.acceleration, 'acceleration'), (self.task, 'task')]:
                index = widget.findData(options.get(key, getattr(transcript, key, 'auto')))
                if index >= 0:
                    widget.setCurrentIndex(index)
            if not Path(transcript.source).is_file():
                file, _ = QFileDialog.getOpenFileName(self, 'Locate moved source media (optional)', str(Path(path).parent), 'Media files (*.*)')
                if file:
                    transcript.source = file
            self.completed(transcript)
            self.unsaved = False
            self.export_timestamps.setChecked(exports.get('timestamps', True))
            self.export_speakers.setChecked(exports.get('speakers', True))
            self.export_mode.setCurrentIndex(max(0, self.export_mode.findData(exports.get('segmentation', 'cleaned'))))
            self.set_busy(False)
            # Probe existing media only; opening a project never invokes inference.
            if Path(transcript.source).is_file():
                self.launch_job(lambda cancel, event: inspect(transcript.source, cancel), self.project_media_ready, 'inspect')
        except AppError as exc:
            QMessageBox.warning(self, 'Project could not be opened', str(exc))

    def project_media_ready(self, info):
        detected = self.detected_language.text()
        self.media_ready(info)
        if getattr(self, 'project_stream', None) is not None:
            index = self.stream.findData(self.project_stream)
            if index >= 0:
                self.stream.setCurrentIndex(index)
        self.detected_language.setText(detected)
        self.stage.setText('Project opened · no retranscription')

    def begin_history(self, path, options, backend, duration_value=0):
        self.history_run = dict(filename=Path(path).name, model=options.model, language=options.language, backend=backend.name,
                                device=backend.device.get('name', ''), media_seconds=duration_value, peak_ram_bytes=None, peak_vram_bytes=None)
        self.history_started = time.monotonic()

    def record_history(self, status, transcript=None):
        if not self.history_run:
            return
        value = self.history_run
        self.history_run = None
        if transcript:
            value.update(language=transcript.language, media_seconds=transcript.duration)
        value.update(status=status, processing_seconds=time.monotonic() - self.history_started)
        try:
            self.history.add(**value)
        except Exception:
            log.exception('Could not save local benchmark metadata')
            self.statusBar().showMessage('History metadata could not be saved; transcript remains available.', 8000)

    def show_history(self):
        from .history_dialog import HistoryDialog
        HistoryDialog(self.history, self).exec()

    def show_queue(self):
        from .queue_dialog import QueueDialog
        if not hasattr(self, 'queue_dialog'):
            self.queue_dialog = QueueDialog(self)
        self.queue_dialog.show()
        self.queue_dialog.raise_()

    def add_queue_files(self, files):
        for file in files:
            self.queue.add(file, self.options())
        self.show_queue()

    def resume_queue(self):
        if self.job or self.queue.active:
            return
        if self.unsaved and QMessageBox.question(self, 'Unsaved transcript', 'Batch processing will replace the viewer. Continue without saving current changes?') != QMessageBox.StandardButton.Yes:
            return
        missing = sorted({item.options.model for item in self.queue.items if item.status == 'Waiting' and not self.models.installed(item.options.model)})
        if missing and QMessageBox.question(self, 'Batch model downloads', 'Download requested models when needed?\n' + '\n'.join(f'{m}: {size(CATALOG[m]["size"])}' for m in missing)) != QMessageBox.StandardButton.Yes:
            return
        self.batch_output = self.paths.projects / ('batch-' + time.strftime('%Y%m%d-%H%M%S') + '-' + __import__('uuid').uuid4().hex[:6])
        self.batch_output.mkdir(parents=True, exist_ok=True)
        self.queue.paused = False
        self.unsaved = False
        self.process_queue()

    def process_queue(self):
        if self.job or self.closing:
            return
        item = self.queue.next()
        if not item:
            return
        options = item.options
        try:
            backend = choose(self.backends, options.acceleration)
        except AppError as exc:
            self.queue.finish('Failed', str(exc))
            QTimer.singleShot(50, self.process_queue)
            return
        self.last_options = options
        self.begin_history(item.source, options, backend)
        self.project_path = None
        self.result = None
        self.viewer.set_transcript(Transcript(item.source, options.model, options.language, 0), editable=False)
        destination = self.batch_output / (Path(item.source).stem + '-' + item.id[:6])
        def action(cancel, event):
            media = inspect(item.source, cancel)
            stream = options.stream if options.stream in {s.index for s in media.streams} else media.streams[0].index
            result = transcribe(item.source, replace(options, stream=stream), self.paths, self.settings, self.models, self.backends, cancel, event)
            cancel.check()
            save_project(Path(str(destination) + '.ltproj'), result, asdict(options))
            for i, format in enumerate(('txt', 'srt', 'vtt')):
                cancel.check()
                export_file(Path(str(destination) + '.' + format), result)
                event(dict(stage='Exporting', fraction=(i + 1) / 3, overall=(i + 1) / 3))
            return result
        def completed(result):
            processing = max(.01, time.monotonic() - self.history_started)
            if self.media:
                self.media_ready(self.media)
            self.completed(result)
            item.speed, item.output = result.duration / processing, str(destination) + '.ltproj'
            self.queue.finish('Completed')
            self.unsaved = False
        self.launch_job(action, completed, 'batch')

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
        if self.preview:
            self.preview.player.stop()
        self.monitor.stop()
        if self.monitor.isRunning():
            event.ignore()
            self.closing = True
            QTimer.singleShot(100, self.close)
            return
        event.accept()
