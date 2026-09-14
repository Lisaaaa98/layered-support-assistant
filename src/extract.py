"""Extractive fallback for when generation declines a question it could answer.

The 3B model refuses 18 of 146 cases that have a supported answer, sometimes
while naming the source it claims not to have ("INSUFFICIENT_CONTEXT [1], [2],
[3] both mention an 8% cash advance fee"). Those refusals are a property of the
model, not of the evidence.

So when generation gives up, the question is answered by quoting instead of
composing: locate the sentence in the retrieved sources that states the fact
asked about and return it verbatim with its citation. Nothing is rephrased,
so nothing can be mis-stated — the failure mode of an extractive answer is
quoting the wrong sentence, which is visible to the reader, rather than
inventing a figure, which is not.

Conservative by construction: it fires only when the candidate figures agree.
Two different values for the fact means the question stays refused.
"""
import re

from query import expand
from verify import CHECKED_UNITS, VERIFY_PATTERNS, _claim_like

STOP = {"the", "a", "an", "of", "on", "in", "at", "to", "for", "and", "or", "by",
        "is", "are", "be", "will", "shall", "you", "your", "we", "our", "us",
        "any", "such", "each", "from", "with", "that", "this", "if", "not", "as",
        "per", "which", "it", "its", "may", "must", "all", "no", "what", "how",
        "much", "many", "do", "does", "did", "my", "me", "i", "can", "would"}

MIN_OVERLAP = 2


def _content_words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower())
            if w not in STOP and len(w) > 2}


def _sentence_around(text, pos):
    """The sentence containing a position, so the quote carries its own context."""
    start = max(text.rfind(". ", 0, pos), text.rfind("\n", 0, pos))
    start = 0 if start < 0 else start + 1
    tail = min([p for p in (text.find(". ", pos), text.find("\n", pos)) if p != -1]
               or [len(text)])
    return re.sub(r"\s+", " ", text[start:tail + 1]).strip()


def find_fact(question, chunks):
    """Locate the single figure in the sources that answers this question.

    Returns (sentence, chunk, raw_value) or None when the evidence is absent
    or divided.
    """
    expanded, _ = expand(question)
    asked = _content_words(expanded)
    if not asked:
        return None

    candidates = []
    for index, chunk in enumerate(chunks):
        text = chunk["text"]
        for unit, pattern in VERIFY_PATTERNS:
            if unit not in CHECKED_UNITS:
                continue
            for m in re.finditer(pattern, text):
                before = text[max(0, m.start() - 70):m.start()]
                claim = _claim_like(float(m.group(1).replace(",", "")), unit,
                                    before, chunk.get("section", ""))
                if len(asked & claim.head_words) >= MIN_OVERLAP:
                    candidates.append((claim.value, m.group(0), index, m.start()))

    if not candidates:
        return None
    # Divided evidence is not an answer. Staying silent beats picking a side,
    # which is the same rule the conflict layer applies.
    if len({c[0] for c in candidates}) > 1:
        return None

    _, raw, index, pos = candidates[0]
    chunk = chunks[index]
    return _sentence_around(chunk["text"], pos), chunk, raw


def _sentences(text):
    parts = re.split(r"(?<=[.;])\s+|\n", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def extractive_answer(question, chunks, max_sentences=3):
    """Quote the passage that best addresses the question.

    An earlier version tried to identify *which figure* was the answer and
    quote that sentence. It fired on 4 of 18 refusals and picked the wrong
    sentence in 2 of them, because deciding which number answers a question is
    the same fragile word-matching that defeated the field-level verifier.

    Selecting a passage instead removes that decision. The reader sees the
    clause and judges it, so a mis-selected passage is obvious rather than
    silently wrong, and a question whose answer spans several figures
    ("8% of the amount withdrawn, minimum S$15") is served whole instead of
    being rejected as contradictory evidence.
    """
    expanded, _ = expand(question)
    asked = _content_words(expanded)
    if not asked or not chunks:
        return None

    scored = []
    for index, chunk in enumerate(chunks, 1):
        for sentence in _sentences(chunk["text"]):
            overlap = len(asked & _content_words(sentence))
            if overlap >= MIN_OVERLAP:
                scored.append((overlap, index, sentence))
    if not scored:
        return None

    scored.sort(key=lambda s: -s[0])
    top_index = scored[0][1]
    # Keep to one source so the quote stays internally consistent.
    chosen = [s for s in scored if s[1] == top_index][:max_sentences]
    body = " ".join(s[2] for s in chosen)
    return f"{body} [{top_index}]"
