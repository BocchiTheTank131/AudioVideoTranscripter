"""Conservative review flags. Confidence is a token statistic, not calibrated accuracy."""
import re
import zlib
from difflib import SequenceMatcher
from collections import Counter


def compression_ratio(text):
    data = text.encode('utf-8')
    return len(data) / max(1, len(zlib.compress(data)))


def repetitive(text):
    compact = re.sub(r'\s+', '', text.casefold())
    # Character units handle Thai and other scripts without whitespace.
    for width in range(1, min(64, len(compact) // 8) + 1):
        match = re.search(r'(.{' + str(width) + r'})(?:\1){7,}', compact)
        if match and len(match.group()) >= 24 and len(match.group()) >= .65 * len(compact):
            return True
    words = re.findall(r'\w+', text.casefold())
    for n in range(1, 5):
        grams = Counter(tuple(words[i:i+n]) for i in range(len(words) - n + 1))
        if grams and max(grams.values()) >= 8 and max(grams.values()) * n >= .7 * len(words):
            return True
    return False


def flag(segment, previous=None, duplicate_count=0):
    flags = []
    segment.compression_ratio = compression_ratio(segment.text)
    if repetitive(segment.text):
        flags.append('Pathological repetition')
    ids = [t.get('id') for t in segment.tokens if t.get('id') is not None]
    if len(ids) >= 30:
        for n in range(1, 5):
            grams = Counter(tuple(ids[i:i+n]) for i in range(len(ids) - n + 1))
            if grams and max(grams.values()) >= 10 and max(grams.values()) * n >= .7 * len(ids):
                flags.append('Token repetition')
                break
    if len(segment.text.encode('utf-8')) > 160 and segment.compression_ratio > 2.8:
        flags.append('Highly repetitive output')
    if segment.avg_logprob is not None and segment.avg_logprob < -1.0:
        flags.append('Low token confidence')
    if segment.no_speech_prob is not None and segment.no_speech_prob > .7:
        flags.append('Likely non-speech; review audio')
    if previous and duplicate_count >= 2 and SequenceMatcher(None, previous.text.casefold(), segment.text.casefold()).ratio() > .96:
        flags.append('Consecutive near-identical segments')
    segment.quality_flags = flags
    return segment


def analyse(segments):
    previous = None
    duplicates = 0
    for segment in segments:
        duplicates = duplicates + 1 if previous and SequenceMatcher(None, previous.text.casefold(), segment.text.casefold()).ratio() > .96 else 0
        flag(segment, previous, duplicates)
        previous = segment
    return segments


def confidence_label(segment):
    if segment.confidence is None:
        return 'N/A'
    return f'{segment.confidence:.0%} token probability'


def obvious_hallucination(segment):
    # Audio/model evidence must corroborate repetition before automatic removal.
    return repetitive(segment.text) and ((segment.no_speech_prob is not None and segment.no_speech_prob > .6)
                                         or (segment.avg_logprob is not None and segment.avg_logprob < -1.0))
