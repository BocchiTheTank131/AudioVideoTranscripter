import json
import logging
from pathlib import Path
import shutil
import tempfile
import time
from diarization.alignment import align_speakers
from hardware.detection import choose
from keywords.extraction import frequency_keywords
from media.ffmpeg import inspect, normalize
from translation.tasks import validate_task
from utils.process import run_json_worker
from utils.types import AppError, Cancelled, Transcript
from .whisper_cpp import WhisperCppBackend

log = logging.getLogger(__name__)


def transcribe(path, options, paths, settings, manager, backends, cancel, on_event):
    validate_task(options.model, options.task)
    backend = choose(backends, options.acceleration)
    temporary_root = Path(settings.temp_dir or paths.cache)
    temporary_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='job-', dir=temporary_root))
    engine = WhisperCppBackend(backend, cancel)
    try:
        on_event({'stage': 'Inspecting media', 'overall': 0})
        info = inspect(path, cancel)
        on_event({'event': 'metadata', 'metadata': info})
        audio = directory / 'audio.wav'
        normalize(info, options.stream, audio, cancel,
                  lambda e: on_event(dict(e, overall=0.08 * e['fraction'] if e.get('fraction') is not None else None)))
        with manager.lease(options.model):
            model = manager.ensure(options.model, cancel, lambda e: on_event(dict(e, overall=.08 + .12 * e['fraction'])))
            cancel.check()
            engine.load_model(model, options)
            began = time.monotonic()

            def inference_event(event):
                if event.get('event') == 'progress':
                    processed, total = event['processed'], event['total']
                    elapsed = max(.01, time.monotonic() - began)
                    event.update(stage='Transcribing', fraction=processed / total if total else None,
                                 overall=.20 + .65 * processed / total if total else None,
                                 speed=processed / elapsed, eta=(total - processed) / (processed / elapsed) if processed > 1 else None)
                elif event.get('event') == 'stage':
                    event['overall'] = None
                on_event(event)
            output = engine.transcribe(audio, inference_event)
            engine.unload()
        transcript = Transcript(info.path, options.model, output['language'], output['duration'], output['segments'],
                                task=options.task, backend=backend.name)
        if not transcript.segments:
            transcript.warnings.append('No speech was recognized in the selected audio track.')
        # Save a core checkpoint before running any optional feature.
        (directory / 'core-transcript.json').write_text(json.dumps(transcript.to_dict(), ensure_ascii=False), 'utf-8')
        cancel.check()
        if options.diarization:
            on_event({'stage': 'Diarizing speakers', 'overall': None})
            try:
                if not Path(options.diarization_path or '__missing__').is_dir():
                    raise AppError('Set up a local Community-1 model in Settings → Advanced features.')
                output_file = directory / 'speakers.json'
                request_file = directory / 'diarization-request.json'
                request_file.write_text(json.dumps(dict(model=options.diarization_path, audio=str(audio), output=str(output_file),
                                                        num_speakers=options.speaker_count, min_speakers=options.min_speakers,
                                                        max_speakers=options.max_speakers)), 'utf-8')
                run_json_worker('diarization.worker', request_file, cancel, on_event)
                align_speakers(transcript.segments, json.loads(output_file.read_text('utf-8')))
            except Cancelled:
                raise
            except Exception as exc:
                log.exception('Optional diarization failed')
                transcript.warnings.append('Speaker diarization could not complete. The transcript is available. Check optional dependencies, the local Community-1 model and FFmpeg/torchcodec. ' + str(exc)[:300])
        cancel.check()
        if options.keywords:
            on_event({'stage': 'Extracting keywords', 'overall': None})
            text = '\n'.join(segment.text for segment in transcript.segments)
            if options.keyword_method == 'ai':
                try:
                    if not Path(options.keyword_path or '__missing__').is_dir():
                        raise AppError('Install the local embedding model in Settings → Advanced features.')
                    request_file = directory / 'keywords-request.json'
                    output_file = directory / 'keywords.json'
                    request_file.write_text(json.dumps(dict(model=options.keyword_path, text=text, output=str(output_file),
                                                            count=options.keyword_count, minimum=options.phrase_min,
                                                            maximum=options.phrase_max, diversity=options.diversity)), 'utf-8')
                    run_json_worker('keywords.worker', request_file, cancel, on_event)
                    transcript.keywords = json.loads(output_file.read_text('utf-8'))
                    transcript.keyword_method = 'KeyBERT · all-MiniLM-L6-v2 (English)'
                except Cancelled:
                    raise
                except Exception as exc:
                    log.exception('Optional AI keywords failed')
                    transcript.warnings.append('AI keyword extraction was unavailable; used the offline frequency method. ' + str(exc)[:300])
            if not transcript.keyword_method:
                transcript.keywords = frequency_keywords(text, options.keyword_count, options.phrase_min, options.phrase_max)
                transcript.keyword_method = 'Offline word / phrase frequency (non-AI)'
        cancel.check()
        on_event({'stage': 'Building transcript', 'overall': .98})
        return transcript
    finally:
        engine.unload()
        if not options.keep_temp:
            shutil.rmtree(directory)
        else:
            log.info('Debug files retained: %s', directory)
