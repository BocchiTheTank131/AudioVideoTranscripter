import csv
import sqlite3
from datetime import datetime, timezone

COLUMNS = ('date', 'filename', 'model', 'language', 'backend', 'device', 'media_seconds',
           'processing_seconds', 'realtime_speed', 'peak_ram_bytes', 'peak_vram_bytes', 'status')


def realtime_speed(media_seconds, processing_seconds):
    return media_seconds / processing_seconds if processing_seconds > 0 else None


class History:
    """No audio, transcript, source directory, or token is stored."""
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, date TEXT, filename TEXT, model TEXT, language TEXT, backend TEXT, device TEXT, media_seconds REAL, processing_seconds REAL, realtime_speed REAL, peak_ram_bytes INTEGER, peak_vram_bytes INTEGER, status TEXT)')

    def add(self, **job):
        job.setdefault('date', datetime.now(timezone.utc).isoformat(timespec='seconds'))
        job['realtime_speed'] = realtime_speed(job.get('media_seconds', 0), job.get('processing_seconds', 0)) if job.get('status') == 'completed' else None
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO jobs (' + ','.join(COLUMNS) + ') VALUES (' + ','.join('?' for _ in COLUMNS) + ')', [job.get(k) for k in COLUMNS])

    def rows(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id DESC')]

    def clear(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM jobs')

    def export(self, path):
        with open(path, 'w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(self.rows())
