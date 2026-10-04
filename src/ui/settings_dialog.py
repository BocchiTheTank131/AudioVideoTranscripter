import json
import os
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
                              QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QTableWidget,
                              QTableWidgetItem, QHeaderView, QDialogButtonBox, QFileDialog, QMessageBox,
                              QProgressBar, QInputDialog, QScrollArea)
from transcription.models import CATALOG
from hardware.detection import LABELS, sensible_threads
from utils.optional_models import download_optional, ESTIMATES
from .widgets import label, button, size
from .workers import Job


class SettingsDialog(QDialog):
    def __init__(self, settings, paths, models, backends, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Local Transcriber · Settings')
        self.resize(820, 650)
        self.settings = replace(settings)
        self.paths, self.models, self.backends = paths, models, backends
        self.job = None
        self.fields = {}
        self.model_actions = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(label('Settings', 'title'))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        general = self.form_tab('General')
        self.path_field(general, 'Output folder', 'output_dir', directory=True)
        self.field(general, 'Remember folders', 'remember_folder', 'check')
        self.field(general, 'Theme', 'theme', ['system', 'light', 'dark'])
        self.path_field(general, 'Temporary files', 'temp_dir', directory=True)
        self.field(general, 'Retain temporary files for debugging', 'keep_temp', 'check')
        info = label('Media, transcripts and speaker data are never uploaded.\nOnly explicit model downloads use the network. No telemetry.', 'muted')
        info.setWordWrap(True)
        general.addRow(info)
        general.addRow(button('Open logs folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs)))))
        transcription = self.form_tab('Transcription')
        self.field(transcription, 'Default model', 'model', list(CATALOG))
        self.field(transcription, 'Default language', 'language', ['auto'] + sorted(json.loads((Path(__file__).parents[1] / 'transcription/languages.json').read_text('utf-8'))))
        threads = self.field(transcription, 'CPU threads · 0 = automatic', 'threads', (0, 256))
        threads.setToolTip(f'Automatic uses {sensible_threads()} threads on this system.')
        self.field(transcription, 'Beam size', 'beam_size', (1, 20))
        self.field(transcription, 'Temperature', 'temperature', (0.0, 1.0))
        self.field(transcription, 'Word timestamps', 'words', 'check')
        hardware = self.form_tab('Hardware')
        acceleration = self.field(hardware, 'Default acceleration', 'acceleration', list(LABELS))
        for index, name in enumerate(LABELS):
            acceleration.setItemData(index, name)
            available = name == 'auto' or (backends.get(name) and backends[name].available)
            acceleration.setItemText(index, LABELS[name] + ('' if available else ' · unavailable'))
            acceleration.model().item(index).setEnabled(bool(available))
        acceleration.setCurrentIndex(list(LABELS).index(self.settings.acceleration) if self.settings.acceleration in LABELS else 0)
        diagnostics = []
        for name, backend in backends.items():
            diagnostics.append(f'{LABELS[name]}: ' + ('available · ' + backend.device.get('name', '') if backend.available else backend.reason))
            if backend.executable:
                diagnostics.append('  ' + backend.executable)
        device_info = label('\n'.join(diagnostics), 'muted')
        device_info.setWordWrap(True)
        device_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hardware.addRow(device_info)
        explanation = label('CUDA requires NVIDIA. Vulkan supports AMD, NVIDIA and compatible Intel GPUs.\nHIP is optional and requires a compatible ROCm build.\nRuntime probes enumerate actual devices; unavailable choices cannot start inference.', 'muted')
        explanation.setWordWrap(True)
        hardware.addRow(explanation)
        model_page = QWidget()
        model_layout = QVBoxLayout(model_page)
        model_layout.addWidget(label('Downloads occur only for the model you request. Every model is SHA-256 verified.', 'muted'))
        self.model_table = QTableWidget(len(CATALOG), 4)
        self.model_table.setHorizontalHeaderLabels(['Model', 'Disk / state', 'Path', 'Manage'])
        self.model_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.model_table.setColumnWidth(3, 215)
        self.model_table.verticalHeader().hide()
        self.model_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        model_layout.addWidget(self.model_table)
        self.tabs.addTab(model_page, 'Models')
        self.populate_models()
        advanced = self.form_tab('Advanced features')
        optional_info = label('Optional dependencies run in a separate Python process. Normal transcription needs neither.\nCommunity-1 requires accepting its Hugging Face conditions; inference stays local.\nAI keywords use an English embedding model. The frequency method needs no downloads.', 'muted')
        optional_info.setWordWrap(True)
        advanced.addRow(optional_info)
        self.path_field(advanced, 'Optional environment · python.exe', 'optional_python')
        self.path_field(advanced, 'Community-1 local folder', 'diarization_path', directory=True)
        advanced.addRow(button('Download Community-1…', lambda: self.optional_download('diarization')))
        advanced.addRow(button('Open Community-1 conditions', lambda: QDesktopServices.openUrl(QUrl('https://huggingface.co/pyannote/speaker-diarization-community-1'))))
        self.path_field(advanced, 'Embedding model local folder', 'keyword_path', directory=True)
        advanced.addRow(button('Download keyword model…', lambda: self.optional_download('keywords')))
        self.field(advanced, 'Keyword method', 'keyword_method', ['frequency', 'ai'])
        self.field(advanced, 'Number of keywords', 'keyword_count', (1, 50))
        self.field(advanced, 'Minimum phrase length', 'phrase_min', (1, 5))
        self.field(advanced, 'Maximum phrase length', 'phrase_max', (1, 5))
        self.field(advanced, 'Diversity', 'diversity', (0.0, 1.0))
        advanced.addRow(label('Tokens are used only for the requested download and are never saved.\nInstall optional requirements in the environment you select. See README for setup.', 'muted'))
        self.progress_label = label('', 'muted')
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress)
        self.cancel_button = button('Cancel download', self.cancel_job)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def form_tab(self, title):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(12, 20, 12, 20)
        form.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.tabs.addTab(scroll, title)
        return form

    def field(self, form, title, key, type):
        value = getattr(self.settings, key)
        if type == 'check':
            widget = QCheckBox()
            widget.setChecked(value)
        elif isinstance(type, list):
            widget = QComboBox()
            widget.addItems(type)
            widget.setCurrentText(value)
        elif isinstance(type[0], float):
            widget = QDoubleSpinBox()
            widget.setRange(*type)
            widget.setSingleStep(.1)
            widget.setValue(value)
        else:
            widget = QSpinBox()
            widget.setRange(*type)
            widget.setValue(value)
        self.fields[key] = widget
        form.addRow(title, widget)
        return widget

    def path_field(self, form, title, key, directory=False):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        field = QLineEdit(getattr(self.settings, key))
        field.setPlaceholderText('Default' if key not in ('diarization_path', 'keyword_path', 'optional_python') else 'Not configured')
        self.fields[key] = field
        layout.addWidget(field)

        def browse():
            selected = QFileDialog.getExistingDirectory(self, title, field.text()) if directory else QFileDialog.getOpenFileName(self, title, field.text())[0]
            if selected:
                field.setText(selected)
        layout.addWidget(button('Browse…', browse))
        form.addRow(title, row)

    def populate_models(self):
        self.model_actions = []
        for row, (name, info) in enumerate(CATALOG.items()):
            installed = self.models.installed(name)
            self.model_table.setItem(row, 0, QTableWidgetItem(name))
            self.model_table.setItem(row, 1, QTableWidgetItem(size(info['size']) + (' · Installed' if installed else ' · Download needed')))
            self.model_table.setItem(row, 2, QTableWidgetItem(str(self.models.path(name))))
            controls = QWidget()
            layout = QHBoxLayout(controls)
            layout.setContentsMargins(2, 2, 2, 2)
            download = button('Verify' if installed else 'Download', lambda checked=False, n=name: self.download(n))
            delete = button('Delete', lambda checked=False, n=name: self.delete(n))
            delete.setEnabled(installed and not self.models.busy(name))
            download.setEnabled(not self.models.busy(name))
            layout.addWidget(download)
            layout.addWidget(delete)
            self.model_actions.extend([download, delete])
            self.model_table.setCellWidget(row, 3, controls)
            self.model_table.setRowHeight(row, 44)

    def delete(self, name):
        if QMessageBox.question(self, 'Delete model', f'Delete the downloaded {name} model? You can download it again later.') != QMessageBox.StandardButton.Yes:
            return
        try:
            self.models.delete(name)
            self.populate_models()
        except Exception as exc:
            QMessageBox.warning(self, 'Model is unavailable', str(exc))

    def begin(self, action, success):
        if self.job:
            return
        self.job = Job(action, self)
        self.job.event.connect(self.show_progress)
        self.job.result.connect(success)
        self.job.error.connect(lambda message: QMessageBox.warning(self, 'Download failed', message))
        self.job.cancelled.connect(lambda: self.progress_label.setText('Cancelled · incomplete downloads removed'))
        self.job.finished.connect(self.finished)
        self.tabs.setEnabled(False)
        self.buttons.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 0)
        self.cancel_button.show()
        self.job.start()

    def finished(self):
        job = self.job
        self.job = None
        job.deleteLater()
        self.tabs.setEnabled(True)
        self.buttons.setEnabled(True)
        self.progress.hide()
        self.cancel_button.hide()
        self.populate_models()

    def download(self, name):
        def action(cancel, progress):
            with self.models.lease(name):
                return self.models.ensure(name, cancel, progress)
        self.begin(action, lambda _: self.progress_label.setText(f'{name} installed and verified'))

    def optional_download(self, kind):
        if QMessageBox.question(self, 'Optional model download', f'Download {kind} model?\n{ESTIMATES[kind]}\nOnly model files are downloaded. No user content is uploaded.') != QMessageBox.StandardButton.Yes:
            return
        token = ''
        if kind == 'diarization':
            token, ok = QInputDialog.getText(self, 'Hugging Face setup', 'Accept the Community-1 conditions, then enter a read token.\nThis token is used in memory and will not be saved.', QLineEdit.EchoMode.Password)
            if not ok:
                return
        key = 'diarization_path' if kind == 'diarization' else 'keyword_path'
        destination = self.fields[key].text().strip() or str(self.paths.models / kind)
        self.begin(lambda cancel, progress: download_optional(kind, destination, token, cancel, progress),
                   lambda path: (self.fields[key].setText(path), self.progress_label.setText(f'{kind} model downloaded. Save settings to use it.')))

    def show_progress(self, event):
        fraction = event.get('fraction')
        if fraction is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(fraction * 1000))
        text = event.get('stage', '')
        if event.get('total'):
            text += f" · {size(event['downloaded'])} / {size(event['total'])} · {size(event['speed'])}/s"
        self.progress_label.setText(text)

    def cancel_job(self):
        if self.job:
            self.job.cancel()
            self.progress_label.setText('Cancelling…')

    def save(self):
        for key, widget in self.fields.items():
            value = widget.isChecked() if isinstance(widget, QCheckBox) else widget.currentText() if isinstance(widget, QComboBox) else widget.value() if isinstance(widget, (QSpinBox, QDoubleSpinBox)) else widget.text().strip()
            if key == 'acceleration':
                value = widget.currentData()
            setattr(self.settings, key, value)
        if self.settings.phrase_min > self.settings.phrase_max:
            QMessageBox.warning(self, 'Phrase length', 'Minimum phrase length must not exceed maximum phrase length.')
            return
        self.accept()

    def reject(self):
        if self.job:
            self.cancel_job()
            return
        super().reject()

    def closeEvent(self, event):
        if self.job:
            self.cancel_job()
            event.ignore()
        else:
            event.accept()
