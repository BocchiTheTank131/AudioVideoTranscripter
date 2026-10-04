from dataclasses import dataclass, field, asdict


class AppError(Exception):
    """An actionable error suitable for display to the user."""


class Cancelled(AppError):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[dict] = field(default_factory=list)
    confidence: float | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    tokens: list[dict] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    source_ids: list[int] = field(default_factory=list)


@dataclass
class Transcript:
    source: str
    model: str
    language: str
    duration: float
    segments: list[Segment] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    keyword_method: str = ""
    task: str = "transcribe"
    backend: str = "cpu"
    warnings: list[str] = field(default_factory=list)
    speaker_names: dict[str, str] = field(default_factory=dict)
    raw_segments: list[Segment] = field(default_factory=list)
    cleanup: dict = field(default_factory=dict)
    transcription_settings: dict = field(default_factory=dict)

    def to_dict(self):
        data = asdict(self)
        for segment in data['segments']:
            if segment['speaker']:
                segment['speaker'] = self.speaker_names.get(segment['speaker'], segment['speaker'])
        return data


@dataclass
class Options:
    model: str = "base"
    language: str = "auto"
    task: str = "transcribe"
    acceleration: str = "auto"
    threads: int = 0
    beam_size: int = 5
    temperature: float = 0.0
    words: bool = False
    suppress_non_speech: bool = True
    stream: int = 0
    diarization: bool = False
    speaker_count: int = 0
    min_speakers: int = 0
    max_speakers: int = 0
    diarization_path: str = ""
    keywords: bool = False
    keyword_method: str = "frequency"
    keyword_count: int = 10
    phrase_min: int = 1
    phrase_max: int = 3
    diversity: float = 0.5
    keyword_path: str = ""
    keep_temp: bool = False
    vad: bool = True
    vad_path: str = ""
    auto_clean: bool = False
    merge_short: bool = False
    merge_gap: float = .5
    subtitle_chars: int = 84
    preset: str = "custom"
