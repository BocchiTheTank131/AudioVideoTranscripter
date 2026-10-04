"""Opt-in packaged-app QA, activated only with --verify-media."""
import json
from pathlib import Path
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from media.ffmpeg import find_tool
from utils.paths import resource_root


def start_verification(window, media, directory, backend='auto', offline=False):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if offline:
        import requests
        def block(*args, **kwargs):
            raise RuntimeError('Network request blocked by offline verification')
        requests.sessions.Session.request = block
    state = {'phase': 'hardware', 'ticks': [], 'started': time.monotonic(), 'stages': [], 'errors': []}
    original_event = window.handle_event

    def event(value):
        stage = value.get('stage')
        if stage and (not state['stages'] or state['stages'][-1] != stage):
            state['stages'].append(stage)
        original_event(value)
    window.handle_event = event
    QMessageBox.warning = lambda parent, title, message, *args, **kwargs: state['errors'].append(message)
    QMessageBox.question = lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    QFileDialog.getExistingDirectory = lambda *args, **kwargs: str(directory)
    timer = QTimer(window)
    timer.setInterval(30)
    window._verification_timer = timer

    def finish(success, error=''):
        timer.stop()
        ticks = state['ticks']
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        report = dict(success=success, error=error, stages=state['stages'], offline=offline,
                      gui_timer_ticks=len(ticks), max_gui_timer_gap_seconds=max(gaps, default=0),
                      backend=window.result.backend if window.result else None,
                      segments=len(window.result.segments) if window.result else 0,
                      device=window.active_backend.device if getattr(window, 'active_backend', None) else {},
                      warnings=state['errors'])
        report['resource_root'] = str(resource_root())
        report['ffmpeg'] = find_tool('ffmpeg')
        report['ffprobe'] = find_tool('ffprobe')
        (directory / 'packaged-report.json').write_text(json.dumps(report, indent=2), 'utf-8')
        window.unsaved = False
        window.closing = True
        window.close()

    def advance():
        state['ticks'].append(time.monotonic())
        if time.monotonic() - state['started'] > 180:
            finish(False, 'Timed out')
            return
        if window.job:
            return
        try:
            phase = state['phase']
            if phase == 'hardware' and window.backends:
                state['phase'] = 'import'
                window.import_file(str(Path(media).resolve()))
            elif phase == 'import':
                if not window.media:
                    raise RuntimeError('Import did not produce media metadata')
                window.model.setCurrentIndex(window.model.findData('base'))
                index = window.acceleration.findData(backend)
                if index < 0 or not window.acceleration.model().item(index).isEnabled():
                    raise RuntimeError('Requested verification backend is unavailable')
                window.acceleration.setCurrentIndex(index)
                window.words.setChecked(True)
                window.keywords.setChecked(True)
                window.settings.keyword_method = 'frequency'
                state['phase'] = 'transcribe'
                window.start_button.click()
            elif phase == 'transcribe':
                if not window.result or not window.result.segments:
                    raise RuntimeError('Transcription did not produce speech segments')
                window.viewer.table.item(0, 2).setText('Edited: ' + window.result.segments[0].text)
                state['phase'] = 'export'
                window.export(all_formats=True)
            elif phase == 'export':
                stem = Path(media).stem
                data = json.loads((directory / (stem + '.json')).read_text('utf-8'))
                if not data['segments'][0]['text'].startswith('Edited: '):
                    raise RuntimeError('Edited text was lost in JSON export')
                for format in ('txt', 'srt', 'vtt', 'json', 'csv', 'md', 'tsv', 'lrc'):
                    if 'Edited:' not in (directory / (stem + '.' + format)).read_text('utf-8'):
                        raise RuntimeError('Edited text absent from ' + format)
                window.grab().save(str(directory / 'packaged-application.png'))
                finish(not state['errors'], '\n'.join(state['errors']))
        except Exception as exc:
            finish(False, str(exc))
    timer.timeout.connect(advance)
    timer.start()
