"""Real GUI + FFmpeg + Whisper verification. No mocked inference or hardware."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('media', type=Path)
    parser.add_argument('--backend', default='cpu', choices=['auto', 'cpu', 'vulkan', 'cuda', 'hip'])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--translate', action='store_true')
    parser.add_argument('--theme', default='dark', choices=['light', 'dark'])
    parser.add_argument('--model', default='base')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expected-language')
    args = parser.parse_args()
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QTimer, Qt, QMimeData, QUrl, QPoint, QPointF
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from PySide6.QtTest import QTest
    from ui.main_window import MainWindow
    from utils.paths import Paths
    import logging
    logging.basicConfig(filename=ROOT / 'verification/e2e.log', level=logging.INFO)
    if args.offline:
        import requests
        def block(*a, **kw):
            raise AssertionError('Network accessed during offline verification')
        requests.get = block
    app = QApplication([])
    window = MainWindow(Paths())
    window.settings.theme = args.theme
    from ui.theme import apply_theme
    apply_theme(app, args.theme)
    window.show()
    errors = []
    original_warning = QMessageBox.warning
    QMessageBox.warning = lambda parent, title, message, *a, **kw: errors.append((title, message))
    stage_history = []
    original_event = window.handle_event
    # The window connects the callable at each launch; substitute before launching.
    def event(value):
        stage = value.get('stage')
        if stage and (not stage_history or stage_history[-1] != stage):
            stage_history.append(stage)
            print(stage, flush=True)
        original_event(value)
    window.handle_event = event
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(30)

    def until(condition, timeout=600):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise RuntimeError('Verification timed out: ' + window.stage.text())
            app.processEvents()
            time.sleep(.005)

    until(lambda: window.job is None and window.backends)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(args.media.resolve()))])
    drag = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.drop, drag)
    drop = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.drop, drop)
    until(lambda: window.job is None and window.media is not None)
    assert window.media.streams and window.media.duration > 0
    window.model.setCurrentIndex(window.model.findData(args.model))
    window.acceleration.setCurrentIndex(window.acceleration.findData(args.backend))
    window.words.setChecked(True)
    if args.translate:
        window.task.setCurrentIndex(1)
    window.keywords.setChecked(True)
    window.settings.keyword_method = 'frequency'
    QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    until(lambda: window.job is None and window.result is not None)
    assert not errors, errors
    transcript = window.result
    text = ' '.join(segment.text for segment in transcript.segments).casefold()
    if args.media.name == 'sample.mp4':
        assert 'local' in text and 'private' in text, 'Expected reference speech absent'
    if args.expected_language:
        assert transcript.language == args.expected_language
    assert transcript.backend == args.backend or args.backend == 'auto'
    assert transcript.keywords
    assert any(segment.words for segment in transcript.segments), 'Word timestamps absent'
    first = window.viewer.table.item(0, 2)
    first.setText('Edited: ' + first.text())
    directory = args.output or ROOT / 'verification' / (args.backend + ('-translation' if args.translate else ''))
    directory.mkdir(parents=True, exist_ok=True)
    QFileDialog.getExistingDirectory = lambda *a, **kw: str(directory)
    QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Yes
    window.export(all_formats=True)
    until(lambda: window.job is None)
    assert not errors, errors
    data = json.loads((directory / (args.media.stem + '.json')).read_text('utf-8'))
    assert data['segments'][0]['text'].startswith('Edited: ')
    assert data['task'] == ('translate' if args.translate else 'transcribe')
    for format in ('txt', 'srt', 'vtt', 'json', 'csv', 'md', 'tsv', 'lrc'):
        assert 'Edited:' in (directory / (args.media.stem + '.' + format)).read_text('utf-8')
    window.grab().save(str(directory / 'application.png'))
    from ui.settings_dialog import SettingsDialog
    settings_dialog = SettingsDialog(window.settings, window.paths, window.models, window.backends, window)
    settings_dialog.tabs.setCurrentIndex(3)
    settings_dialog.show()
    app.processEvents()
    settings_dialog.grab().save(str(directory / 'models.png'))
    settings_dialog.reject()
    maximum_gap = max((b - a for a, b in zip(ticks, ticks[1:])), default=0)
    result = dict(backend=transcript.backend, detected_device=window.active_backend.device,
                  duration=transcript.duration, language=transcript.language, segments=len(transcript.segments),
                  keywords=transcript.keywords, stages=stage_history, gui_timer_ticks=len(ticks),
                  max_gui_timer_gap_seconds=round(maximum_gap, 3), models=[name for name in window.models.directory.glob('*.bin')],
                  export_formats=8, offline=args.offline, warnings=errors)
    result['models'] = [p.name for p in result['models']]
    (directory / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    window.unsaved = False
    window.close()
    until(lambda: not window.monitor.isRunning(), 5)
    window.close()
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
