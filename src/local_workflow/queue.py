from dataclasses import dataclass, field
from copy import deepcopy
import uuid


@dataclass
class QueueItem:
    source: str
    options: object
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = 'Waiting'
    progress: float | None = None
    speed: float | None = None
    error: str = ''
    output: str = ''


class Queue:
    def __init__(self):
        self.items = []
        self.paused = True
        self.active = None

    def add(self, source, options):
        item = QueueItem(str(source), deepcopy(options))
        self.items.append(item)
        return item

    def next(self):
        if self.paused or self.active:
            return None
        self.active = next((item for item in self.items if item.status == 'Waiting'), None)
        if self.active:
            self.active.status = 'Preparing'
        return self.active

    def finish(self, status, error=''):
        if self.active:
            self.active.status, self.active.error = status, error
            if status == 'Completed':
                self.active.progress = 1.0
            self.active = None

    def remove(self, identifier):
        self.items = [item for item in self.items if item.id != identifier or item is self.active]

    def retry(self, identifier):
        for item in self.items:
            if item.id == identifier and item.status in ('Failed', 'Cancelled'):
                item.status, item.error, item.progress = 'Waiting', '', None

    def clear_completed(self):
        self.items = [item for item in self.items if item.status != 'Completed']
