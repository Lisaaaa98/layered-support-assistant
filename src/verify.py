"""Post-generation grounding check on figures.

The conflict layer guarantees the context holds one value per fact. It cannot
guarantee the model quotes that value: an observed answer converted
"5,000 DBS Points for 10,000 miles" into "10,000 for 10,000". That error
carried no forbidden value, so nothing upstream caught it.

So every monetary amount and percentage in an answer is checked back against
the sources it was given. A figure that appears nowhere in the context was
invented, whatever else the answer got right, and the answer does not ship.

Deliberately narrow: amounts and percentages only. Bare small integers are
left alone because they are usually counts or list positions ("the last 5
transactions"), and flagging them would make the check fire constantly and
be switched off.
"""
import re

from conflict import PATTERNS  # same value grammar the conflict layer uses

CITATION = re.compile(r"\[\d+\]")

# Points, miles and income figures are bare numbers, so the conflict layer's
# grammar does not reach them, yet they are exactly where an off-by-a-factor
# error hides ("10,000 points for 10,000 miles" instead of 5,000 for 10,000).
# Requiring a thousands separator admits those while excluding years and the
# small counts that would make this check noisy.
GROUPED = ("count", r"(?<![\d.,$])(\d{1,3}(?:,\d{3})+)(?![\d,])")
VERIFY_PATTERNS = PATTERNS + [GROUPED]

# Only these carry the kind of claim worth blocking an answer over.
CHECKED_UNITS = {"pct", "sgd", "count"}


def extract_figures(text):
    """Amounts and percentages asserted by an answer, with their surface form."""
    stripped = CITATION.sub(" ", text)
    found = []
    for unit, pattern in VERIFY_PATTERNS:
        if unit not in CHECKED_UNITS:
            continue
        for m in re.finditer(pattern, stripped):
            found.append((unit, float(m.group(1).replace(",", "")), m.group(0)))
    return found


def _present_in(unit, value, context_text):
    for ctx_unit, pattern in VERIFY_PATTERNS:
        if ctx_unit != unit:
            continue
        for m in re.finditer(pattern, context_text):
            if abs(float(m.group(1).replace(",", "")) - value) < 1e-9:
                return True
    return False


def verify_figures(answer_text, chunks):
    """Return the figures in an answer that no source supports.

    An empty list means every amount and percentage stated is traceable to a
    source the model was actually shown.
    """
    context_text = " ".join(c["text"] for c in chunks)
    unsupported = []
    for unit, value, raw in extract_figures(answer_text):
        if not _present_in(unit, value, context_text):
            unsupported.append({"unit": unit, "value": value, "text": raw})
    return unsupported


def _claim_like(value, unit, before, section=""):
    """Adapt a figure and its preceding words into the conflict layer's Claim,
    so answers and sources are compared by the same notion of 'same fact'."""
    from conflict import Claim

    stub = {"section": section, "text": "", "card_scope": ["all"],
            "txn_scope": ["general"], "doc_id": "answer", "chunk_id": "answer",
            "date_confidence": "stated", "authority": 9, "effective_date": None}
    return Claim(value=value, unit=unit, raw=str(value), before=before,
                 after="", pos=0, chunk=stub)


def _source_claims(chunk):
    """Claims in a source chunk, using the widened grammar."""
    text = chunk["text"]
    out = []
    for unit, pattern in VERIFY_PATTERNS:
        if unit not in CHECKED_UNITS:
            continue
        for m in re.finditer(pattern, text):
            out.append(_claim_like(
                float(m.group(1).replace(",", "")), unit,
                text[max(0, m.start() - 70):m.start()], chunk.get("section", "")))
    return out


def verify_claims(answer_text, chunks):
    """Field-level check. NOT ENABLED — kept as a recorded negative result.

    The intent was to catch a figure that exists in the sources but is attached
    to the wrong fact, e.g. "10,000 DBS Points for 10,000 miles" where the table
    says 5,000 for 10,000. Both numbers appear, so the existence check passes.

    It does not work, and the reason is structural rather than a tuning issue.
    The check asks whether a figure matches the source claims naming the same
    fact, but that candidate set is built by the same fragile word matching it
    is meant to police. A correct answer of S$98.10 for a supplementary fee was
    rejected because the source phrase "Supplementary Card: S$98.10" carries too
    few adjacent words to be collected, while the principal card's S$196.20 has
    more and was, leaving a candidate set containing only the wrong value.

    Measured on 54 answered cases: 2 blocked, both correct answers, and the
    error it was built for still slipped through. A verifier that rejects valid
    answers and misses the target is worse than none, so `answer()` uses
    `verify_figures` alone. Retained because the failure is more instructive
    than the function would have been: a guard cannot be built on the same
    signal as the thing it guards.
    """
    source_claims = [c for ch in chunks for c in _source_claims(ch)]
    stripped = CITATION.sub(" ", answer_text)

    problems = []
    for unit, pattern in VERIFY_PATTERNS:
        if unit not in CHECKED_UNITS:
            continue
        for m in re.finditer(pattern, stripped):
            value = float(m.group(1).replace(",", ""))
            before = stripped[max(0, m.start() - 70):m.start()]
            answer_claim = _claim_like(value, unit, before)
            head = answer_claim.head_words
            if not head:
                continue

            # Source claims naming the same fact. Unit must match first: an
            # amount and a points balance are never the same fact however
            # similar the words around them read, and omitting this check made
            # the verifier reject correct answers by pairing S$599.50 against
            # a 12,500-point award.
            rivals = [c for c in source_claims
                      if c.unit == unit
                      and len(head & c.head_words) >= min(2, len(head), len(c.head_words))]
            if not rivals:
                continue  # no source names this fact; existence check covers it
            if not any(abs(c.value - value) < 1e-9 for c in rivals):
                problems.append({
                    "unit": unit, "stated": m.group(0),
                    "sources_say": sorted({c.raw for c in rivals}),
                    "fact_words": sorted(head),
                })
    return problems
