import json
import logging
from pathlib import Path
from .engine import TranscriptionBackend
from hardware.detection import sensible_threads
from utils.process import run_stream
from utils.types import AppError, Segment


def tokens_to_words(tokens):
    """Group subword tokens; retain original tokens separately in JSON words."""
    words = []
    for token in tokens:
        # Byte-level tokens can split a Thai/Japanese UTF-8 character. Decode
        # after grouping, retaining the native bytes rather than losing them.
        raw = bytes.fromhex(token['bytes_hex']) if 'bytes_hex' in token else token.get('text', '').encode('utf-8')
        if not raw:
            continue
        if not words or raw[:1].isspace():
            words.append({'text': '', '_bytes': raw.strip(), 'start': token.get('start'), 'end': token.get('end'), 'tokens': [token]})
        else:
            word = words[-1]
            word['_bytes'] += raw
            if token.get('end') is not None:
                word['end'] = token['end']
            word['tokens'].append(token)
    for word in words:
        word['text'] = word.pop('_bytes').decode('utf-8', errors='replace')
    return words


class WhisperCppBackend(TranscriptionBackend):
    def __init__(self, backend, cancellation):
        self.backend = backend
        self.cancellation = cancellation
        self.model = None
        self.options = None

    def load_model(self, path, options):
        # The child process loads the model once and releases it on exit.
        if not Path(path).is_file():
            raise AppError('Whisper model is missing. Download the selected model.')
        self.model, self.options = Path(path), options

    def transcribe(self, audio, on_event):
        if not self.model:
            raise AppError('No model has been selected.')
        options = self.options
        args = [self.backend.executable, '--model', self.model, '--audio', audio,
                '--threads', options.threads or sensible_threads(), '--language', options.language,
                '--translate', int(options.task == 'translate'), '--words', int(options.words),
                '--beam', options.beam_size, '--temperature', options.temperature,
                '--suppress', int(options.suppress_non_speech)]
        result = {'segments': [], 'language': options.language}
        completed = False

        def read(line):
            nonlocal completed
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppError('Whisper runtime returned invalid structured data. Reinstall the matching runtime.') from exc
            kind = event.get('event')
            if kind == 'error':
                raise AppError(event['message'])
            if kind == 'segment':
                segment = Segment(max(0, event['start']), max(event['start'], event['end']), event['text'].strip(),
                                  words=tokens_to_words(event.get('tokens', [])), confidence=event.get('confidence'))
                if segment.text:
                    result['segments'].append(segment)
                    on_event({'event': 'segment', 'segment': segment})
            elif kind == 'done':
                result.update(language=event['language'], duration=event['duration'])
                completed = True
            else:
                on_event(event)
        run_stream(args, self.cancellation, read)
        if not completed:
            raise AppError('The transcription process stopped before producing a complete result. Check logs/app.log.')
        return result

    def cancel(self):
        self.cancellation.cancel()

    def unload(self):
        self.model = self.options = None

    def get_capabilities(self):
        return dict(word_timestamps=True, translate_to_english=True, pause=False, structured_events=True)
