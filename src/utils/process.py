import json
import logging
import os
import queue
import subprocess
import threading
from collections import deque
import psutil
from .types import AppError, Cancelled

log = logging.getLogger(__name__)
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._processes = set()

    def check(self):
        if self.event.is_set():
            raise Cancelled('Cancelled. Temporary processing files have been removed.')

    def register(self, process):
        with self._lock:
            self._processes.add(process)
        if self.event.is_set():
            terminate_tree(process)
            self.check()

    def unregister(self, process):
        with self._lock:
            self._processes.discard(process)

    def cancel(self):
        self.event.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            terminate_tree(process)


def terminate_tree(process):
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        parent.terminate()
        _, alive = psutil.wait_procs(children + [parent], timeout=1)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    except (psutil.Error, OSError):
        log.exception('Unable to terminate process tree; trying direct kill')
        try:
            process.kill()
        except OSError:
            log.exception('Direct process termination failed')


def run_stream(command, cancel: Cancellation, on_line=None, env=None):
    cancel.check()
    try:
        process = subprocess.Popen([str(x) for x in command], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                   creationflags=CREATE_FLAGS, env=env)
    except OSError as exc:
        raise AppError(f'Could not start {command[0]}: {exc}') from exc
    events = queue.Queue()
    errors = deque(maxlen=100)

    def reader(pipe, kind):
        try:
            for line in pipe:
                events.put((kind, line.rstrip()))
        finally:
            pipe.close()
            events.put((kind, None))

    readers = [threading.Thread(target=reader, args=(process.stdout, 'out'), daemon=True),
               threading.Thread(target=reader, args=(process.stderr, 'err'), daemon=True)]
    try:
        cancel.register(process)
        for thread in readers:
            thread.start()
        closed = 0
        while closed < 2:
            cancel.check()
            try:
                kind, line = events.get(timeout=0.15)
            except queue.Empty:
                continue
            if line is None:
                closed += 1
            elif kind == 'err':
                errors.append(line)
                log.debug('%s', line)
            elif on_line:
                on_line(line)
        result = process.wait()
        cancel.check()
        if result:
            detail = '\n'.join(errors)[-6000:]
            log.error('Process %s exited %s: %s', command[0], result, detail)
            if 'out of memory' in detail.lower() or 'bad_alloc' in detail.lower():
                raise AppError('The backend ran out of memory. Select a smaller model or use CPU acceleration.')
            raise AppError(f'{PathName(command[0])} failed (exit {result}). Check the media file, model and backend installation.\n{detail[-1200:]}')
    finally:
        if process.poll() is None:
            terminate_tree(process)
        process.wait()
        cancel.unregister(process)
        for thread in readers:
            if thread.ident:
                thread.join(timeout=2)


def PathName(value):
    from pathlib import Path
    return Path(value).name


def run_json_worker(module, request_path, cancel, callback=None):
    import sys
    from .paths import resource_root
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0')
    executable = env.get('LOCAL_TRANSCRIBER_OPTIONAL_PYTHON', sys.executable)
    if getattr(sys, 'frozen', False) and executable == sys.executable:
        raise AppError('Optional Python features need an external environment. Set its Python path in Settings → Advanced features.')
    env['PYTHONPATH'] = str(resource_root() / 'src') + os.pathsep + env.get('PYTHONPATH', '')

    def read(line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.debug('Optional worker: %s', line)
            return
        if callback:
            callback(event)
    try:
        run_stream([executable, '-m', module, request_path], cancel, read, env)
    except Cancelled:
        raise
    except AppError as exc:
        raise AppError('The optional local worker failed. Check the selected Python environment, optional requirements and model folder. Technical details are in logs/app.log.') from exc
