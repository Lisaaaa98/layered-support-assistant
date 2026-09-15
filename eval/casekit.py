"""Shared machinery for authoring a labelled test set.

Extracted when the second industry needed the same thing. The schema decisions
in here were all paid for once already, on the bank set:

  accepted_behaviors  A list, not a value. Answering an ambiguous question by
                      enumerating the options can be better than asking which
                      one, and a set that scores the better behaviour as
                      failure will push the system the wrong way.
  key_facts           Carries an explicit all/any mode, and values are written
                      as the source writes them, so substring matching cannot
                      count "100" inside "100,000".
  severity            A wrong price and a wrong opening hour are not the same
                      failure, and averaging them hides the one that matters.

`validate` is the part worth reusing most: it caught a label that forbade a
value which was also that case's correct answer, which would have scored a
correct system as wrong.
"""
import json


class CaseSet:
    """Collects cases, then normalises, validates and writes them."""

    def __init__(self):
        self.cases = []

    def case(self, cid, question, lang, answer, *, facts=(), mode="all", forbid=(),
             sources=(), beh=("answer",), layer="knowledge", sev="normal",
             trap=None, cite=True, note=""):
        self.cases.append({
            "id": cid, "question": question, "lang": lang, "layer": layer,
            "accepted_behaviors": list(beh),
            "expected_answer": answer,
            "key_facts": {"mode": mode, "values": list(facts)},
            "forbidden_facts": list(forbid),
            "expected_sources": list(sources),
            "require_citation": cite,
            "severity": sev, "trap": trap, "note": note,
        })
        return self.cases[-1]

    def apply_labels(self, forbidden_extra=None, unit_suffix=None):
        """Merge per-case forbidden values and normalise numeric units.

        Units are normalised because a figure recorded bare in one case and
        with a unit in another made the same correct answer pass one and fail
        the other.
        """
        forbidden_extra = forbidden_extra or {}
        unit_suffix = unit_suffix or {}
        for c in self.cases:
            merged = sorted(set(c["forbidden_facts"]) | set(forbidden_extra.get(c["id"], [])))
            c["key_facts"]["values"] = [unit_suffix.get(v, v) for v in c["key_facts"]["values"]]
            c["forbidden_facts"] = [unit_suffix.get(v, v) for v in merged
                                    if unit_suffix.get(v, v) not in c["key_facts"]["values"]]

    def validate(self, chunks):
        """Catch labelling mistakes before they become measurement mistakes."""
        problems = []
        corpus = " ".join(c["text"] for c in chunks)
        seen = set()
        for c in self.cases:
            if c["id"] in seen:
                problems.append(f"{c['id']}: 重复 id")
            seen.add(c["id"])

            overlap = set(c["key_facts"]["values"]) & set(c["forbidden_facts"])
            if overlap:
                problems.append(f"{c['id']}: key_facts 与 forbidden_facts 重叠 {overlap}")

            # A forbidden value that appears in the gold answer is a labelling
            # error: it is a correct fact, and enforcing it would score correct
            # answers as wrong.
            for v in c["forbidden_facts"]:
                if c["expected_answer"] and v in c["expected_answer"]:
                    problems.append(f"{c['id']}: forbidden_fact {v!r} 出现在标准答案中")

            # A key fact that appears nowhere in the corpus means the gold
            # answer itself is wrong, and the case would measure the system
            # against fiction.
            for v in c["key_facts"]["values"]:
                if v not in corpus:
                    problems.append(f"{c['id']}: key_fact {v!r} 在语料中不存在")

            if c["accepted_behaviors"] == ["answer"] and not c["expected_sources"]:
                problems.append(f"{c['id']}: 期望作答但未标注来源")
        return problems

    def write(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for c in self.cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    def summary(self):
        from collections import Counter
        return {
            "total": len(self.cases),
            "layer": dict(Counter(c["layer"] for c in self.cases)),
            "lang": dict(Counter(c["lang"] for c in self.cases)),
            "severity": dict(Counter(c["severity"] for c in self.cases)),
            "behaviour": dict(Counter(b for c in self.cases for b in c["accepted_behaviors"])),
            "with_forbidden": sum(1 for c in self.cases if c["forbidden_facts"]),
        }
