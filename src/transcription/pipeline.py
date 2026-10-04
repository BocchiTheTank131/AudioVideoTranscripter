import json
import logging
from pathlib import Path
import shutil
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict, replace
from diarization.alignment import align_speakers
from hardware.detection import choose
from keywords.extraction import frequency_keywords
from media.ffmpeg import inspect, normalize
from translation.tasks import validate_task
from utils.process import run_json_worker
from utils.types import AppError, Cancelled, Transcript
from .whisper_cpp import WhisperCppBackend
from .quality import analyse, repetitive, obvious_hallucination
from .cleanup import merge_segments
from .vad import valid as vad_valid
from media.ffmpeg import find_tool
from utils.process import run_stream

log = logging.getLogger(__name__)


def transcribe(path, options, paths, settings, manager, backends, cancel, on_event):
    validate_task(options.model, options.task)
    backend = choose(backends, options.acceleration)
    temporary_root = Path(settings.temp_dir or paths.cache)
    temporary_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='job-', dir=temporary_root))
    engine = WhisperCppBackend(backend, cancel)
    warnings = []
    if options.vad and not vad_valid(options.vad_path or paths.models / 'vad' / 'ggml-silero-v6.2.0.bin'):
        options = replace(options, vad=False, vad_path='')
        warnings.append('VAD model is unavailable. Continued without speech detection; install VAD in Settings → Advanced features.')
    elif options.vad:
        options = replace(options, vad_path=options.vad_path or str(paths.models / 'vad' / 'ggml-silero-v6.2.0.bin'))
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
        transcript.warnings.extend(warnings + output.get('warnings', []))
        transcript.transcription_settings = asdict(options)
        analyse(transcript.segments)
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
        # Keep exact recognized cues before any optional cleanup, including flags.
        for index, segment in enumerate(transcript.segments):
            segment.source_ids = [index]
        transcript.raw_segments = deepcopy(transcript.segments)
        removed, retried = [], []
        if options.auto_clean:
            on_event({'stage': 'Reviewing pathological repetition', 'overall': None})
            with manager.lease(options.model):
                engine.load_model(model, replace(options, beam_size=1, temperature=0.0, vad=False))
                cleaned = []
                try:
                    for index, segment in enumerate(transcript.segments):
                        cancel.check()
                        if not obvious_hallucination(segment):
                            cleaned.append(segment)
                            continue
                        # One retry per obvious repetition; never delete ordinary low confidence.
                        retry_audio = directory / 'retry.wav'
                        run_stream([find_tool('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error', '-y', '-ss', str(segment.start),
                                    '-i', str(audio), '-t', str(max(.1, segment.end - segment.start)), '-c:a', 'pcm_s16le', str(retry_audio)], cancel)
                        retried.append(index)
                        try:
                            retry = engine.transcribe(retry_audio, lambda e: None)['segments']
                            if retry and not any(repetitive(s.text) for s in retry):
                                for cue in retry:
                                    cue.start = min(segment.end, cue.start + segment.start)
                                    cue.end = min(segment.end, cue.end + segment.start)
                                    for token in cue.tokens:
                                        for k in ('start', 'end'):
                                            if token.get(k) is not None:
                                                token[k] = min(segment.end, token[k] + segment.start)
                                    for word in cue.words:
                                        for k in ('start', 'end'):
                                            if word.get(k) is not None:
                                                word[k] = min(segment.end, word[k] + segment.start)
                                    cue.speaker, cue.source_ids = segment.speaker, [index]
                                cleaned.extend(retry)
                            else:
                                removed.append(index)
                        except Cancelled:
                            raise
                        except Exception:
                            log.exception('Hallucination retry failed; retaining recognized cue')
                            transcript.warnings.append('A repetition retry failed; the flagged original cue was retained.')
                            cleaned.append(segment)
                        retry_audio.unlink(missing_ok=True)
                finally:
                    engine.unload()
            transcript.segments = cleaned
        transcript.cleanup = dict(removed_original_ids=removed, retried_original_ids=retried,
                                  merge_enabled=options.merge_short, merge_gap=options.merge_gap, subtitle_chars=options.subtitle_chars)
        if options.merge_short:
            transcript.segments = merge_segments(transcript.segments, options.merge_gap, options.subtitle_chars)
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
