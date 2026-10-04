ALIASES = {'eng': 'en', 'tha': 'th', 'jpn': 'ja', 'zho': 'zh', 'chi': 'zh', 'spa': 'es', 'fra': 'fr', 'fre': 'fr',
           'deu': 'de', 'ger': 'de', 'kor': 'ko', 'rus': 'ru', 'ara': 'ar', 'por': 'pt', 'ita': 'it', 'hin': 'hi', 'und': ''}


def metadata_language(code, languages):
    mapped = ALIASES.get(code.lower(), code.lower())
    return languages.get(mapped, code or 'Unspecified').title() if mapped else 'Unspecified'
