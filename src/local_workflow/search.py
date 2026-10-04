import re


def matches(texts, needle):
    if not needle:
        return []
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    return [(row, match.start(), match.end()) for row, text in enumerate(texts) for match in pattern.finditer(text)]
