import hashlib
from pathlib import Path
import pytest
import requests
from transcription.models import ModelManager, CATALOG
from utils.types import AppError, Cancelled
from utils.process import Cancellation


@pytest.fixture
def catalog(monkeypatch):
    payload = b'valid test model' * 100
    monkeypatch.setitem(CATALOG, 'test', dict(file='test.bin', size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), url='https://example.test/model'))
    return payload


class Response:
    def __init__(self, blocks):
        self.blocks = blocks

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def raise_for_status(self): pass
    def iter_content(self, *args): yield from self.blocks


def test_default_is_base_and_only_requested_downloads(tmp_path, catalog, monkeypatch):
    from settings.store import Settings
    assert Settings().model == 'base'
    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **kw: (calls.append(url), Response([catalog]))[1])
    manager = ModelManager(tmp_path)
    manager.ensure('test', Cancellation(), lambda _: None)
    assert calls == ['https://example.test/model']
    assert [p.name for p in tmp_path.iterdir()] == ['test.bin']


def test_offline_cache_and_corruption(tmp_path, catalog, monkeypatch):
    manager = ModelManager(tmp_path)
    manager.path('test').write_bytes(catalog)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('Network used for installed model'))
    assert manager.ensure('test', Cancellation(), lambda _: None).read_bytes() == catalog
    manager.path('test').write_bytes(b'x' * len(catalog))
    assert not manager.verify('test', Cancellation())


def test_download_hash_failure_and_partial_cleanup(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response([b'x' * len(catalog)]))
    manager = ModelManager(tmp_path)
    with pytest.raises(AppError, match='integrity'):
        manager.ensure('test', Cancellation(), lambda _: None)
    assert not list(tmp_path.glob('*.part'))
    assert not manager.installed('test')


def test_download_cancellation(tmp_path, catalog, monkeypatch):
    cancel = Cancellation()
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response([catalog[:30], catalog[30:]]))
    with pytest.raises(Cancelled):
        ModelManager(tmp_path).ensure('test', cancel, lambda _: cancel.cancel())
    assert not list(tmp_path.iterdir())


def test_network_failure_does_not_leave_partial(tmp_path, catalog, monkeypatch):
    def fail(*a, **k): raise requests.ConnectionError('offline')
    monkeypatch.setattr(requests, 'get', fail)
    with pytest.raises(AppError, match='connection'):
        ModelManager(tmp_path).ensure('test', Cancellation(), lambda _: None)
    assert not list(tmp_path.iterdir())


def test_protected_lease_and_safe_paths(tmp_path, catalog):
    manager = ModelManager(tmp_path)
    with pytest.raises(AppError):
        manager.path('../../outside')
    assert manager.path('test').parent == tmp_path
    with manager.lease('test'):
        with pytest.raises(AppError, match='in use'):
            manager.delete('test')
        with pytest.raises(AppError):
            with manager.lease('test'): pass
    assert not manager.busy('test')
