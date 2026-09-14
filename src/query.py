"""Query-side handling for a bilingual Singapore customer base.

Customers here mix languages freely, and the three forms fail differently:
  "annual fee waiver"        - lexical match works
  "我的 annual fee 可以 waive 吗"  - code-switched, the English terms carry it
  "年费可以豁免吗"             - all-Chinese, lexical match scores exactly zero

The knowledge base is English-only, so Chinese terms are expanded to their
English equivalents before retrieval. Banking vocabulary is a closed set of a
few dozen terms, which makes a glossary both sufficient for the common path
and, unlike a translation model, deterministic and auditable. Dense retrieval
covers the long tail of phrasing the glossary cannot anticipate.
"""
import re

# Chinese term -> the wording actually used in the DBS documents.
import domain


def glossary():
    return domain.glossary()

def _terms():
        return sorted(domain.glossary(), key=len, reverse=True)
_HAN = re.compile(r"[一-鿿]")


def has_han(text):
    return bool(_HAN.search(text))


def expand(query):
    """Append the English equivalents of any Chinese terms found.

    The original text is kept rather than replaced: a code-switched query
    already carries usable English tokens, and dropping them would throw away
    the strongest signal in the string.
    """
    if not has_han(query):
        return query, []
    matched, remaining = [], query
    for term in _terms():
        if term in remaining:
            matched.append(term)
            remaining = remaining.replace(term, " ")
    if not matched:
        return query, []
    english = " ".join(domain.glossary()[t] for t in matched)
    return f"{query} {english}", matched
