"""Detect and adjudicate contradictions among retrieved chunks.

Deliberately model-free. Handed two contradictory rates, an LLM tends to pick
one and state it confidently, or to hedge into uselessness, and it will not do
either reproducibly. In a banking context an unreproducible answer about money
is not acceptable, so detection and adjudication are plain code with an audit
trail, and the model is only ever handed a resolved, single-valued fact.

Scope: numeric contradictions only (rates, amounts, day counts). Semantic
contradictions, e.g. one document saying a fee is waivable and another saying
it is not, are out of scope and recorded in docs/findings.md as a known limit.
"""
import re
from dataclasses import dataclass, field

from scopes import TXN_PATTERNS

# Value patterns, each mapped to a comparable unit.
PATTERNS = [
    ("pct", r"(\d{1,3}(?:\.\d{1,2})?)\s*%"),
    ("sgd", r"S\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)"),
    ("days", r"\b(\d{1,3})\s+days?\b"),
]

STOP = {
    "the", "a", "an", "of", "on", "in", "at", "to", "for", "and", "or", "by",
    "is", "are", "be", "will", "shall", "you", "your", "we", "our", "us", "any",
    "such", "each", "from", "with", "that", "this", "if", "not", "as", "per",
    "which", "it", "its", "may", "must", "all", "no",
}

SCOPE_WINDOW = 260  # scope needs a wider view than naming does
CONTEXT_BEFORE = 70
CONTEXT_AFTER = 35
SIMILARITY_THRESHOLD = 0.45


# Words that mark a value as a variant of a nearby fact rather than the same
# fact: "additional 3%" and "prevailing 27.8%" are not competing answers.
QUALIFIERS = {"additional", "increased", "minimum", "maximum", "total", "whichever", "least"}

ANCHOR_WORDS = 5


@dataclass
class Claim:
    """One numeric assertion, tied back to the chunk it came from."""
    value: float
    unit: str
    raw: str
    before: str
    after: str
    pos: int
    chunk: dict

    @property
    def context(self):
        return f"{self.chunk['section']} {self.before}{self.raw}{self.after}"

    @property
    def anchor(self):
        """The words that name this fact: the heading plus what immediately
        precedes the number. Matching on these is far tighter than matching
        the whole surrounding window."""
        head = [w for w in re.findall(r"[a-z]+", self.chunk["section"].lower())
                if w not in STOP and len(w) > 2]
        near = [w for w in re.findall(r"[a-z]+", self.before.lower())
                if w not in STOP and len(w) > 2]
        return set(head[:6]) | set(near[-ANCHOR_WORDS:])

    @property
    def head_words(self):
        """The two or three content words immediately before the number.

        This is what actually names a value. 'prevailing interest rate of X'
        on both sides is one fact with two answers; 'administrative fee of X'
        against 'charged by bank | Y' is two different line items that merely
        share the phrase 'administrative fee' further upstream.
        """
        words = [w for w in re.findall(r"[a-z]+", self.before.lower())
                 if w not in STOP and len(w) > 2]
        return set(words[-3:])

    @property
    def qualifiers(self):
        near = re.findall(r"[a-z]+", self.before.lower())[-6:]
        return QUALIFIERS.intersection(near)

    @property
    def txn_scope(self):
        """Scope inferred from this claim's own neighbourhood.

        Chunk-level scope is too coarse: one long clause can carry a retail
        rate and a cash-advance fee, and tagging the whole block 'general'
        silently hides the second one from conflict detection.
        """
        text = self.chunk["text"]
        lo = max(0, self.pos - SCOPE_WINDOW)
        blob = f"{self.chunk['section']} {text[lo:self.pos + SCOPE_WINDOW]}".lower()
        hits = frozenset(n for n, pat in TXN_PATTERNS if re.search(pat, blob))
        return hits or frozenset({"general"})


@dataclass
class Conflict:
    unit: str
    claims: list
    verdict: dict = field(default_factory=dict)


def extract_claims(chunk):
    text = chunk["text"]
    claims = []
    for unit, pat in PATTERNS:
        for m in re.finditer(pat, text):
            value = float(m.group(1).replace(",", ""))
            start = max(0, m.start() - CONTEXT_BEFORE)
            end = min(len(text), m.end() + CONTEXT_AFTER)
            claims.append(Claim(
                value=value,
                unit=unit,
                raw=m.group(0),
                before=text[start:m.start()],
                after=text[m.end():end],
                pos=m.start(),
                chunk=chunk,
            ))
    return claims


def same_fact(a, b):
    """Do two claims describe the same fact?

    Three gates, cheapest and most decisive first: comparable unit, same
    scope, then wording. Scope is checked before wording because a
    cash-advance rate and a retail rate stay different facts however
    similarly the sentences read.
    """
    if a.unit != b.unit:
        return False
    # A contradiction is by definition across documents. Two different numbers
    # inside one document are two different facts (a fee table listing 1.00%
    # network + 2.25% bank + 3.25% total is arithmetic, not disagreement).
    if a.chunk["doc_id"] == b.chunk["doc_id"]:
        return False
    cards_a, cards_b = set(a.chunk["card_scope"]), set(b.chunk["card_scope"])
    if not (cards_a & cards_b or "all" in cards_a or "all" in cards_b):
        return False
    if a.txn_scope != b.txn_scope:
        return False
    # A qualifier on one side only means one of them is a variant of the other.
    if a.qualifiers != b.qualifiers:
        return False
    # The naming words next to the number must overlap, not just the
    # vocabulary of the surrounding paragraph.
    # One shared word is not enough: 'fee' alone is common to every charge in
    # the book. Genuine duplicates of a fact share the whole naming phrase.
    need = min(2, len(a.head_words), len(b.head_words))
    if len(a.head_words & b.head_words) < need:
        return False
    anchor_a, anchor_b = a.anchor, b.anchor
    if not anchor_a or not anchor_b:
        return False
    return len(anchor_a & anchor_b) / len(anchor_a | anchor_b) >= SIMILARITY_THRESHOLD


DATE_RANK = {"stated": 2, "inferred": 1, "unknown": 0}


def _rank(chunk):
    return (DATE_RANK[chunk["date_confidence"]], chunk["authority"])


def adjudicate(conflict):
    """Apply fixed precedence rules and record which one decided it.

    Currency beats authority on purpose: an authoritative but undated document
    is the more dangerous of the two, because it looks official while being stale.
    """
    claims = conflict.claims
    best = max(claims, key=lambda c: _rank(c.chunk))
    # Only a claim carrying a *different* value is a rival. Corroborating
    # sources that happen to rank lower must survive: suppressing them would
    # strip correct evidence out of the model's context.
    rivals = [c for c in claims if c.value != best.value]
    if not rivals:
        return {"resolved": True, "rule": "unanimous", "detail": "候选值一致",
                "confidence": "high", "winner": best.chunk["chunk_id"],
                "winner_value": best.raw, "winner_date": best.chunk["effective_date"],
                "suppressed": [], "action": "answer"}
    top_rank, runner_rank = _rank(best.chunk), max(_rank(c.chunk) for c in rivals)

    if top_rank == runner_rank:
        return {
            "resolved": False,
            "rule": "no_discriminator",
            "action": "refuse_and_escalate",
            "reason": "候选文档时效与权威性完全相同,无裁决依据",
        }

    if top_rank[0] > runner_rank[0]:
        rule = "recency_confidence"
        detail = (f"{best.chunk['date_confidence']} 优先于 "
                  f"{max(rivals, key=lambda c: _rank(c.chunk)).chunk['date_confidence']}")
        # Winning only because the rival is undated is weak evidence.
        strong = top_rank[0] == 2
    else:
        rule = "authority"
        detail = f"authority {top_rank[1]} 高于 {runner_rank[1]}"
        strong = (top_rank[1] - runner_rank[1]) >= 2

    return {
        "resolved": True,
        "rule": rule,
        "detail": detail,
        "confidence": "high" if strong else "low",
        "winner": best.chunk["chunk_id"],
        "winner_value": best.raw,
        "winner_date": best.chunk["effective_date"],
        "suppressed": [{"chunk_id": c.chunk["chunk_id"], "value": c.raw,
                        "doc": c.chunk["doc_id"]} for c in rivals],
        # Even a resolved money conflict decided on weak evidence should not be
        # asserted flatly to a customer.
        "action": "answer" if strong else "answer_with_caveat",
    }


def detect(chunks):
    """Group the claims across chunks into conflicts and adjudicate each."""
    claims = [c for ch in chunks for c in extract_claims(ch)]
    conflicts, used = [], set()

    for i, a in enumerate(claims):
        if i in used:
            continue
        group = [a]
        for j in range(i + 1, len(claims)):
            if j in used:
                continue
            b = claims[j]
            # Every member must agree with every other member, not just with
            # the seed. Single-link grouping lets one loosely-matching claim
            # drag in a chain of unrelated ones.
            if all(same_fact(m, b) for m in group):
                group.append(b)
                used.add(j)
        # A conflict needs at least two different values for the same fact,
        # from different documents.
        values = {c.value for c in group}
        docs = {c.chunk["doc_id"] for c in group}
        if len(values) > 1 and len(docs) > 1:
            conflict = Conflict(unit=a.unit, claims=group)
            conflict.verdict = adjudicate(conflict)
            conflicts.append(conflict)
            used.add(i)
    return conflicts


def resolve_context(hits):
    """Clean a retrieval result before it ever reaches the model.

    Suppression happens upstream of generation on purpose. Filtering the
    stale chunk out beats asking the model to weigh two rates and beats
    checking its answer afterwards: it cannot quote a number it was never
    shown. What comes back is the surviving context plus an audit record of
    every value that was withheld and the rule that withheld it.
    """
    conflicts = detect(hits)
    suppressed, audit = {}, []
    escalate = False

    for c in conflicts:
        v = c.verdict
        record = {
            "unit": c.unit,
            "scope": sorted(c.claims[0].txn_scope),
            "values": sorted({cl.raw for cl in c.claims}),
            "verdict": v,
        }
        audit.append(record)
        if v["resolved"]:
            for s in v["suppressed"]:
                suppressed[s["chunk_id"]] = s["value"]
        else:
            escalate = True

    kept = [h for h in hits if h["chunk_id"] not in suppressed]
    return {
        "context": kept,
        "suppressed": suppressed,
        "conflicts": audit,
        # An unresolvable contradiction about money is a hand-off, not a guess.
        "escalate": escalate,
        "caveat": any(a["verdict"].get("action") == "answer_with_caveat" for a in audit),
    }
