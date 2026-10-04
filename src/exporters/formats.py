import csv
import io
import json
from pathlib import Path
from utils.types import AppError

FORMATS = ('txt', 'srt', 'vtt', 'json', 'csv', 'md', 'tsv', 'lrc')


def timestamp(seconds, separator='.'):
    ms = max(0, int(round(seconds * 1000)))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}{separator}{ms:03d}'


def speaker_name(transcript, segment):
    return transcript.speaker_names.get(segment.speaker, segment.speaker) if segment.speaker else ''


def plain(transcript, timestamps=False, speakers=False):
    paragraphs = []
    for segment in transcript.segments:
        prefix = f'[{timestamp(segment.start)}] ' if timestamps else ''
        name = speaker_name(transcript, segment) if speakers else ''
        paragraphs.append(prefix + (name + ':\n' if name else '') + segment.text)
    return '\n\n'.join(paragraphs)


def render(transcript, format, timestamps=True, speakers=True):
    if format == 'txt':
        return plain(transcript, timestamps, speakers) + '\n'
    if format == 'json':
        return json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2) + '\n'
    if format in ('srt', 'vtt'):
        blocks = ['WEBVTT\n'] if format == 'vtt' else []
        for i, segment in enumerate(transcript.segments, 1):
            # Blank lines end a subtitle cue. Keep multiline edits within the cue.
            text = '\n'.join(line for line in segment.text.splitlines() if line.strip()).replace('-->', '→')
            name = speaker_name(transcript, segment) if speakers else ''
            text = (name + ': ' if name else '') + text
            end = max(segment.end, segment.start + .001)
            separator = ',' if format == 'srt' else '.'
            blocks.append(f'{i}\n{timestamp(segment.start, separator)} --> {timestamp(end, separator)}\n{text}\n')
        return '\n'.join(blocks) + '\n'
    if format in ('csv', 'tsv'):
        output = io.StringIO(newline='')
        writer = csv.writer(output, delimiter=',' if format == 'csv' else '\t')
        writer.writerow(['start', 'end', 'speaker', 'text'])
        for segment in transcript.segments:
            writer.writerow([f'{segment.start:.3f}', f'{segment.end:.3f}', speaker_name(transcript, segment), segment.text])
        return output.getvalue()
    if format == 'md':
        return f'# Transcript\n\n{plain(transcript, timestamps, speakers)}\n\n' + ('## Keywords\n\n' + '\n'.join('- ' + word for word in transcript.keywords) + '\n' if transcript.keywords else '')
    if format == 'lrc':
        lines = []
        for segment in transcript.segments:
            cs = max(0, round(segment.start * 100))
            minutes, cs = divmod(cs, 6000)
            seconds, cs = divmod(cs, 100)
            name = speaker_name(transcript, segment) if speakers else ''
            lines.append(f'[{minutes:02d}:{seconds:02d}.{cs:02d}]' + (name + ': ' if name else '') + segment.text.replace('\n', ' '))
        return '\n'.join(lines) + '\n'
    raise AppError('Unsupported export format.')


def export_file(path, transcript, timestamps=True, speakers=True, overwrite=False):
    path = Path(path)
    text = render(transcript, path.suffix.lstrip('.').lower(), timestamps, speakers)
    # Exclusive creation is the final guard, even if another process creates the file after the GUI prompt.
    with path.open('w' if overwrite else 'x', encoding='utf-8', newline='') as output:
        output.write(text)
