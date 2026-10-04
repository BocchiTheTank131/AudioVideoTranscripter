from copy import deepcopy
from dataclasses import replace
from difflib import SequenceMatcher


def merge_segments(segments, gap=.5, max_chars=84):
    result = []
    for segment in segments:
        current = deepcopy(segment)
        previous = result[-1] if result else None
        if (previous and previous.speaker == current.speaker and 0 <= current.start - previous.end <= gap
                and (previous.end - previous.start < 2 or len(previous.text) < 25)
                and not previous.text.rstrip().endswith(('.', '!', '?', '。', '！', '？'))
                and len(previous.text) + len(current.text) + 1 <= max_chars):
            previous.end = current.end
            previous.text += ' ' + current.text
            previous.words.extend(current.words)
            previous.tokens.extend(current.tokens)
            previous.source_ids.extend(current.source_ids)
            previous.quality_flags = list(dict.fromkeys(previous.quality_flags + current.quality_flags))
            previous.confidence = previous.avg_logprob = previous.no_speech_prob = None
        else:
            result.append(current)
    return result


def original_with_edits(transcript):
    """Overlay edits on original cue boundaries without exporting stale recognized text."""
    if not transcript.raw_segments:
        return deepcopy(transcript.segments)
    original = deepcopy(transcript.raw_segments)
    visible_ids = set()
    grouped = []
    for current in transcript.segments:
        if grouped and current.source_ids == grouped[-1].source_ids and len(current.source_ids) == 1:
            previous = grouped[-1]
            previous.text += ' ' + current.text
            previous.end = current.end
        else:
            grouped.append(deepcopy(current))
    for current in grouped:
        ids = [i for i in current.source_ids if 0 <= i < len(original)]
        if not ids:
            continue
        visible_ids.update(ids)
        before = ' '.join(original[i].text for i in ids)
        boundaries = []
        offset = 0
        for i in ids[:-1]:
            offset += len(original[i].text) + 1
            boundaries.append(offset)
        # Map old textual boundaries through the user's edit operations.
        mapped = []
        operations = SequenceMatcher(None, before, current.text, autojunk=False).get_opcodes()
        for boundary in boundaries:
            value = len(current.text)
            for _, a, b, c, d in operations:
                if a <= boundary <= b:
                    value = c + round((boundary - a) * (d - c) / max(1, b - a))
                    break
            mapped.append(value)
        limits = [0] + mapped + [len(current.text)]
        for pos, i in enumerate(ids):
            text = current.text[limits[pos]:limits[pos+1]].strip()
            if text != original[i].text:
                original[i].words = []
                original[i].confidence = original[i].avg_logprob = None
            original[i].text = text
            original[i].speaker = current.speaker
        original[ids[0]].start = current.start
        original[ids[-1]].end = current.end
    # Original export retains deliberately cleaned-out hallucinations, marked as such.
    retained = visible_ids | set(transcript.cleanup.get('removed_original_ids', []))
    return [s for index, s in enumerate(original) if s.text and index in retained]


def export_snapshot(transcript, mode='cleaned'):
    result = deepcopy(transcript)
    if mode == 'original':
        result.segments = original_with_edits(transcript)
    return result
