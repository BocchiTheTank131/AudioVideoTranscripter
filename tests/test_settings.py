from settings.store import Settings
from utils.paths import Paths


def test_settings_round_trip_preserves_advanced_values(tmp_path):
    paths = Paths(tmp_path)
    settings = Settings(temperature=.4, beam_size=7, threads=6, model='base', theme='dark', output_dir='D:/Exports')
    settings.save(paths.settings)
    restored = Settings.load(paths.settings)
    assert restored == settings


def test_invalid_settings_use_base_default(tmp_path):
    paths = Paths(tmp_path)
    paths.settings.write_text('{broken', 'utf-8')
    settings = Settings.load(paths.settings)
    assert settings.model == 'base'
    assert settings.language == 'auto'
    assert not settings.words


def test_settings_do_not_accept_boolean_thread_counts(tmp_path):
    paths = Paths(tmp_path)
    paths.settings.write_text('{"threads": true, "theme": "invalid"}', 'utf-8')
    settings = Settings.load(paths.settings)
    assert settings.threads == 0
    assert settings.theme == 'system'
