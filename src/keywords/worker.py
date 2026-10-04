import json
import os
import sys
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')


def main():
    request = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    from keybert import KeyBERT
    from sentence_transformers import SentenceTransformer
    encoder = SentenceTransformer(request['model'], local_files_only=True)
    extractor = KeyBERT(model=encoder)
    # Embed bounded text windows; never send text to an API.
    text = request['text']
    chunks = [text[i:i + 6000] for i in range(0, len(text), 6000)] or ['']
    scores = {}
    for i, chunk in enumerate(chunks):
        keywords = extractor.extract_keywords(chunk, keyphrase_ngram_range=(request['minimum'], request['maximum']),
                                               stop_words='english', use_mmr=True, diversity=request['diversity'], top_n=request['count'])
        for keyword, score in keywords:
            scores[keyword] = max(scores.get(keyword, -1), float(score))
        print(json.dumps({'event': 'progress', 'stage': 'Extracting AI keywords', 'fraction': (i + 1) / len(chunks)}), flush=True)
    result = sorted(scores, key=scores.get, reverse=True)[:request['count']]
    Path(request['output']).write_text(json.dumps(result, ensure_ascii=False), 'utf-8')


if __name__ == '__main__':
    main()
