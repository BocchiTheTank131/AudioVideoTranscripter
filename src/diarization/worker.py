import json
import os
import sys
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0')


def main():
    request = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    from pyannote.audio import Pipeline
    pipeline = Pipeline.from_pretrained(request['model'])
    if pipeline is None:
        raise RuntimeError('Could not load the local Community-1 pipeline.')
    kwargs = {key: request[key] for key in ('num_speakers', 'min_speakers', 'max_speakers') if request.get(key)}

    def hook(step_name, step_artifact, file=None, total=None, completed=None):
        print(json.dumps({'event': 'progress', 'stage': 'Diarizing speakers · ' + step_name,
                          'fraction': completed / total if total and completed is not None else None}), flush=True)
    output = pipeline(request['audio'], hook=hook, **kwargs)
    annotation = output.exclusive_speaker_diarization
    labels = {}
    turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        label = labels.setdefault(speaker, f'Speaker {len(labels) + 1}')
        turns.append(dict(start=turn.start, end=turn.end, speaker=label))
    Path(request['output']).write_text(json.dumps(turns), 'utf-8')


if __name__ == '__main__':
    main()
