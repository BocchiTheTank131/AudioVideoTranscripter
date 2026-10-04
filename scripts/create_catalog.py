"""Maintainer-only catalog refresh from the model publisher, never a startup step."""
import json
import re
from pathlib import Path
import requests

root = Path(__file__).resolve().parents[1]
files = requests.get('https://huggingface.co/api/models/ggerganov/whisper.cpp/tree/main', timeout=30).json()
indexed = {f['path']: f for f in files}
catalog = {}
for name in ['tiny', 'tiny.en', 'base', 'base.en', 'small', 'small.en', 'medium', 'medium.en', 'large', 'turbo']:
    filename = f"ggml-{'large-v3' if name == 'large' else 'large-v3-turbo' if name == 'turbo' else name}.bin"
    item = indexed[filename]
    catalog[name] = dict(file=filename, size=item['size'], sha256=item['lfs']['oid'],
                         url=f'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{filename}',
                         memory_mb={'tiny': 390, 'base': 550, 'small': 1200, 'medium': 3200, 'large': 5200, 'turbo': 2400}[name.split('.')[0]])
(root / 'src/transcription/catalog.json').write_text(json.dumps(catalog, indent=2) + '\n', 'utf-8')
source = (root / 'vendor/whisper.cpp/src/whisper.cpp').read_text('utf-8')
block = source.split('g_lang = {', 1)[1].split('};', 1)[0]
languages = {code: label.title() for code, label in re.findall(r'\{\s*"([a-z]+)",\s*\{\s*\d+,\s*"([^"]+)"', block)}
(root / 'src/transcription/languages.json').write_text(json.dumps(languages, indent=2) + '\n', 'utf-8')
print(f'{len(catalog)} models, {len(languages)} languages')
