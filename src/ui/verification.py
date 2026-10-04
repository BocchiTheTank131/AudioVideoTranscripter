"""Opt-in packaged-app QA, activated only with --verify-media."""
import json
from pathlib import Path
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from media.ffmpeg import find_tool
from utils.paths import resource_root


def start_verification(window, media, directory, backend='auto', offline=False, model='base', workflow=False, project=None):
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
        report['version'] = QApplication.applicationVersion()
        report['model'] = model
        report['language'] = window.result.language if window.result else None
        report['duration'] = window.result.duration if window.result else None
        report['quality_metadata_present'] = bool(window.result and any(s.avg_logprob is not None for s in window.result.raw_segments or window.result.segments))
        report['workflow'] = state.get('workflow', {})
        (directory / 'packaged-report.json').write_text(json.dumps(report, indent=2), 'utf-8')
        window.unsaved = False
        window.closing = True
        window.close()

    def advance():
        state['ticks'].append(time.monotonic())
        if time.monotonic() - state['started'] > 1800:
            finish(False, 'Timed out')
            return
        if window.job:
            return
        try:
            phase = state['phase']
            if phase == 'hardware' and window.backends:
                if project:
                    QFileDialog.getOpenFileName = lambda *args, **kwargs: (str(Path(project).resolve()), '')
                    state['phase'] = 'reopened'
                    window.open_project()
                    return
                state['phase'] = 'import'
                window.import_file(str(Path(media).resolve()))
            elif phase == 'reopened':
                if not window.result or not window.result.segments[0].text.startswith('Edited: '):
                    raise RuntimeError('Restarted application lost saved project edits')
                if any('Transcribing' in stage for stage in state['stages']):
                    raise RuntimeError('Opening a saved project unexpectedly ran inference')
                state['workflow'] = {'reopened_project_without_inference': True}
                finish(not state['errors'], '\n'.join(state['errors']))
            elif phase == 'import':
                if not window.media:
                    raise RuntimeError('Import did not produce media metadata')
                from .settings_dialog import SettingsDialog
                settings_dialog = SettingsDialog(window.settings, window.paths, window.models, window.backends, window)
                settings_dialog.tabs.setCurrentIndex(3)
                settings_dialog.show()
                settings_dialog.close()
                window.model.setCurrentIndex(window.model.findData(model))
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
                if not workflow:
                    finish(not state['errors'], '\n'.join(state['errors']))
                    return
                from local_workflow.projects import load_project
                saved_project = directory / 'verification.ltproj'
                window.project_path = saved_project
                window.save_current_project()
                loaded, _, _ = load_project(saved_project)
                if not loaded.segments[0].text.startswith('Edited: '):
                    raise RuntimeError('Project did not preserve edited transcript')
                QFileDialog.getOpenFileName = lambda *args, **kwargs: (str(saved_project), '')
                window.open_project()
                state['phase'] = 'project'
                state['workflow'] = {'project_save_load': True}
            elif phase == 'project':
                if not window.result or not window.result.segments[0].text.startswith('Edited: '):
                    raise RuntimeError('Opening a project lost transcript edits')
                window.viewer.find_text.setText('Edited')
                window.viewer.find()
                if window.viewer.match_count.text() != '1 / 1 matches':
                    raise RuntimeError('Transcript search match count is incorrect')
                state['workflow']['search'] = True
                window.show_preview(1.0)
                state['phase'] = 'preview'
                state['preview_started'] = time.monotonic()
            elif phase == 'preview':
                if not window.preview or window.preview.player.duration() == 0:
                    if time.monotonic() - state['preview_started'] > 20:
                        raise RuntimeError('Local media player could not load the source')
                    return
                window.preview.player.setPosition(1500)
                state['phase'] = 'seek'
            elif phase == 'seek':
                if abs(window.preview.player.position() - 1500) > 200:
                    raise RuntimeError('Media preview did not seek to requested timestamp')
                window.preview.player.play()
                state['phase'] = 'play'
                state['play_started'] = time.monotonic()
            elif phase == 'play':
                if time.monotonic() - state['play_started'] < .5:
                    return
                if window.preview.player.position() <= 1500:
                    raise RuntimeError('Media preview did not advance during playback')
                window.preview.player.pause()
                window.preview.close()
                state['workflow']['media_preview_seek_play_pause'] = True
                state['phase'] = 'batch'
                window.add_queue_files([str(media), str(media)])
                window.resume_queue()
            elif phase == 'batch':
                if any(item.status in ('Waiting', 'Preparing', 'Transcribing', 'Exporting') for item in window.queue.items):
                    return
                if len(window.queue.items) != 2 or any(item.status != 'Completed' for item in window.queue.items):
                    raise RuntimeError('Two-file batch did not complete successfully')
                if window.pause_button.isEnabled() or window.cancel_button.isEnabled() or not window.start_button.isEnabled():
                    raise RuntimeError('Idle job controls have incorrect enabled states')
                from local_workflow.projects import load_project
                for item in window.queue.items:
                    load_project(item.output)
                    for suffix in ('txt', 'srt', 'vtt'):
                        if not Path(item.output).with_suffix('.' + suffix).exists():
                            raise RuntimeError('Batch export is missing')
                state['workflow']['batch_two_files'] = True
                state['workflow']['idle_controls'] = True
                state['workflow']['history_rows'] = len(window.history.rows())
                window.settings.theme = 'light'
                window.save_settings()
                from settings.store import Settings
                if Settings.load(window.paths.settings).theme != 'light':
                    raise RuntimeError('Settings did not persist')
                state['workflow']['settings_persisted'] = True
                finish(not state['errors'], '\n'.join(state['errors']))
        except Exception as exc:
            finish(False, str(exc))
    timer.timeout.connect(advance)
    timer.start()
