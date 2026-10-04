import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QLockFile
from utils.paths import Paths
from utils.version import VERSION


def main():
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('PYANNOTE_METRICS_ENABLED', '0')
    app = QApplication(sys.argv)
    app.setApplicationName('Local Transcriber')
    app.setApplicationVersion(VERSION)
    app.setOrganizationName('LocalTranscriber')
    try:
        paths = Paths()
        lock = QLockFile(str(paths.root / 'app.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            QMessageBox.information(None, 'Local Transcriber', 'Another instance is already using this application-data directory. Close it before starting another instance.')
            return 0
        handler = RotatingFileHandler(paths.logs / 'app.log', maxBytes=5_000_000, backupCount=3, encoding='utf-8')
        logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(name)s: %(message)s')
        from ui.main_window import MainWindow
        window = MainWindow(paths)
        window.show()
        from ui.theme import apply_theme
        app.styleHints().colorSchemeChanged.connect(lambda scheme: apply_theme(app, 'system') if window.settings.theme == 'system' else None)
        if '--verify-media' in sys.argv:
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument('--verify-media', required=True)
            parser.add_argument('--verify-output', required=True)
            parser.add_argument('--verify-backend', default='auto')
            parser.add_argument('--verify-offline', action='store_true')
            parser.add_argument('--verify-model', default='base')
            parser.add_argument('--verify-workflow', action='store_true')
            parser.add_argument('--verify-project')
            args = parser.parse_args()
            from ui.verification import start_verification
            start_verification(window, args.verify_media, args.verify_output, args.verify_backend, args.verify_offline, args.verify_model, args.verify_workflow, args.verify_project)
        return app.exec()
    except Exception as exc:
        logging.exception('Startup failed')
        QMessageBox.critical(None, 'Local Transcriber could not start', 'Check installation and application-data write permissions.\n' + str(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
