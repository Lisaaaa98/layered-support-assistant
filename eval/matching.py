"""Fact matching for evaluation.

Numeric facts cannot be matched as plain substrings. "6%" occurs inside
"0.6%", "100" inside "100,000", "28%" inside "28.5%". Every one of those
produced a false result on this corpus, scoring the system wrong when it was
right. Matching is therefore anchored so a number cannot be found inside a
larger number.
"""
import re

_NUMERIC = re.compile(r"\d")


def fact_pattern(fact):
    """Build an anchored pattern for a fact.

    Left edge rejects a preceding digit or decimal point, right edge rejects a
    trailing digit, comma-group or decimal, so "28%" no longer matches inside
    "28.5%" and "100" no longer matches inside "100,000".
    """
    escaped = re.escape(fact.strip())
    if not _NUMERIC.search(fact):
        return re.compile(escaped, re.I)
    left = r"(?<![\d.])"
    right = r"(?![\d]|,\d|\.\d)" if not fact.rstrip().endswith("%") else r"(?![\d])"
    return re.compile(left + escaped + right, re.I)


_VALUE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(%?)")


def _numeric_value(fact):
    """Parse a fact into (number, unit) when it is purely a figure."""
    m = re.fullmatch(r"(?:S\$\s?)?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(%?)", fact.strip())
    if not m:
        return None
    return float(m.group(1).replace(",", "")), m.group(2)


def fact_present(fact, text):
    """True if the text asserts this fact.

    Numbers are compared by value, not by spelling: a system answering
    "27.80% per annum" has stated the fact recorded as "27.8%", and scoring
    that as a miss measures the label's formatting rather than the answer.
    Non-numeric facts still match literally.
    """
    if fact_pattern(fact).search(text):
        return True
    target = _numeric_value(fact)
    if target is None:
        return False
    value, unit = target
    for m in _VALUE.finditer(text):
        try:
            found = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        # A percentage must not be satisfied by a bare number and vice versa,
        # otherwise "8%" would match the "8" in an unrelated figure.
        if m.group(2) == unit and abs(found - value) < 1e-9:
            return True
    return False


def facts_satisfied(key_facts, text):
    """Evaluate a key_facts block honouring its all/any mode."""
    values = key_facts.get("values", [])
    if not values:
        return None  # nothing asserted, not scoreable
    hits = [v for v in values if fact_present(v, text)]
    if key_facts.get("mode", "all") == "any":
        return bool(hits)
    return len(hits) == len(values)
