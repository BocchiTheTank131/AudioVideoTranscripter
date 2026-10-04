import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    model: str = 'base'
    language: str = 'auto'
    acceleration: str = 'auto'
    threads: int = 0
    beam_size: int = 5
    temperature: float = 0.0
    words: bool = False
    theme: str = 'system'
    output_dir: str = ''
    last_input_dir: str = ''
    remember_folder: bool = True
    temp_dir: str = ''
    keep_temp: bool = False
    diarization_path: str = ''
    keyword_path: str = ''
    optional_python: str = ''
    keyword_method: str = 'frequency'
    keyword_count: int = 10
    phrase_min: int = 1
    phrase_max: int = 3
    diversity: float = 0.5
    timestamp_export: bool = True
    speaker_export: bool = True
    vad: bool = True
    auto_clean: bool = False
    merge_short: bool = False
    merge_gap: float = .5
    subtitle_chars: int = 84
    preset: str = 'custom'
    follow_live: bool = True
    show_confidence: bool = False
    export_segmentation: str = 'cleaned'
    subtitle_font: int = 20
    subtitle_margin: int = 16
    subtitle_lines: int = 2

    @classmethod
    def load(cls, path):
        if not Path(path).exists():
            return cls()
        try:
            data = json.loads(Path(path).read_text('utf-8'))
            defaults = asdict(cls())
            for key, value in data.items():
                if key in defaults and type(value) is type(defaults[key]):
                    defaults[key] = value
            result = cls(**defaults)
            result.threads = max(0, min(256, result.threads))
            result.beam_size = max(1, min(20, result.beam_size))
            if result.theme not in ('system', 'light', 'dark'):
                result.theme = 'system'
            return result
        except (OSError, ValueError, TypeError):
            logging.getLogger(__name__).exception('Invalid settings; using defaults')
            return cls()

    def save(self, path):
        path = Path(path)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(asdict(self), indent=2), encoding='utf-8')
        temp.replace(path)
