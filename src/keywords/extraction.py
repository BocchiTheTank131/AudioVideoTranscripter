from collections import Counter
import re

# Lightweight offline alternative; deliberately labelled as a frequency method.
STOPWORDS = set('a an the and or but is are was were be been being to of in on at for from with as by it its this that these those i you he she we they our your their my me us not no do does did can could would should will have has had so if then than very just about into also there here what which who how when where all more most some any each'.split())


def frequency_keywords(text, count=10, minimum=1, maximum=3):
    words = re.findall(r'[^\W\d_]+', text.casefold(), re.UNICODE)
    scored = Counter()
    for size in range(minimum, maximum + 1):
        for i in range(len(words) - size + 1):
            phrase = words[i:i + size]
            if any(word in STOPWORDS or len(word) < 3 for word in phrase):
                continue
            scored[' '.join(phrase)] += size ** .5
    return [key for key, _ in scored.most_common(count)]
