import logging
import threading
from PySide6.QtCore import QThread, Signal
from monitoring.resources import ResourceMonitor
from utils.types import Cancelled, AppError
from utils.process import Cancellation


class Job(QThread):
    event = Signal(object)
    result = Signal(object)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action
        self.cancellation = Cancellation()

    def run(self):
        try:
            value = self.action(self.cancellation, self.event.emit)
            self.cancellation.check()
            self.result.emit(value)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            logging.getLogger(__name__).exception('Background operation failed')
            self.error.emit(str(exc) if isinstance(exc, AppError) else 'The operation failed. ' + str(exc)[:500] + '\nTechnical details are saved in logs/app.log.')

    def cancel(self):
        # Termination may wait briefly; never run that wait in the GUI thread.
        threading.Thread(target=self.cancellation.cancel, daemon=True).start()


class MonitorThread(QThread):
    sample = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_event = threading.Event()

    def run(self):
        monitor = ResourceMonitor()
        try:
            while not self.stop_event.is_set():
                try:
                    self.sample.emit(monitor.sample())
                except Exception:
                    logging.getLogger(__name__).exception('Resource sampling failed')
                self.stop_event.wait(1)
        finally:
            monitor.close()

    def stop(self):
        self.stop_event.set()
