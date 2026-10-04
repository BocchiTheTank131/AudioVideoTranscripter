from bisect import bisect_left


def align_speakers(segments, turns):
    """Assign the largest temporal overlap; never invent a label for unassigned speech."""
    turns = sorted(turns, key=lambda turn: turn['start'])
    ends = [turn['end'] for turn in turns]
    # Exclusive diarization has monotone ends. Fall back to scanning for overlapping input.
    monotone = ends == sorted(ends)
    for segment in segments:
        scores = {}
        begin = bisect_left(ends, segment.start) if monotone else 0
        for turn in turns[begin:]:
            if turn['start'] >= segment.end:
                break
            overlap = max(0, min(segment.end, turn['end']) - max(segment.start, turn['start']))
            if overlap:
                scores[turn['speaker']] = scores.get(turn['speaker'], 0) + overlap
        segment.speaker = max(scores, key=scores.get) if scores else None
    return segments
