"""Measure the retrieval layer on its own, before any model is attached.

Two things can be scored without generation, and both are prerequisites for
a correct answer: whether the ground-truth source survives into the context,
and whether values that must never be quoted have been removed from it.
Running this first means later generation failures can be attributed rather
than guessed at.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import domain  # noqa: E402
from conflict import resolve_context  # noqa: E402
from retrieve import Retriever  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOP_K = 6


def main():
    tests = [json.loads(l) for l in (domain.eval_dir() / "testset.jsonl").open(encoding="utf-8")]
    retriever = Retriever()
    rows, leaks = [], []
    recall_hit = recall_total = supp_ok = supp_total = 0

    for t in tests:
        result = resolve_context(retriever.search(t["question"], k=TOP_K))
        context = result["context"]
        docs = {c["doc_id"] for c in context}
        blob = " ".join(c["text"] for c in context)

        recalled = None
        if t["expected_sources"]:
            recall_total += 1
            recalled = bool(set(t["expected_sources"]) & docs)
            recall_hit += recalled

        remaining = [f for f in t["forbidden_facts"] if re.search(re.escape(f), blob)]
        if t["forbidden_facts"]:
            supp_total += 1
            if remaining:
                leaks.append({"id": t["id"], "remaining": remaining,
                              "question": t["question"], "trap": t["trap"]})
            else:
                supp_ok += 1

        rows.append({"id": t["id"], "layer": t["layer"], "trap": t["trap"],
                     "severity": t["severity"], "lang": t["lang"],
                     "recalled": recalled, "leaked": remaining,
                     "empty": not context,
                     "suppressed": list(result["suppressed"]),
                     "escalate": result["escalate"]})

    # Retrieval must return nothing when the question is out of scope, and
    # must return something when it is answerable. Both directions are scored.
    needs_context = [r for r in rows if r["recalled"] is not None]
    should_be_empty = [t for t in tests if "refuse" in t["accepted_behaviors"]
                       and not t["expected_sources"]]
    empty_ids = {r["id"] for r in rows if r["empty"]}
    correct_empty = sum(1 for t in should_be_empty if t["id"] in empty_ids)

    by_lang = {}
    for lg in ("en", "zh", "mixed"):
        sub = [r for r in needs_context if r["lang"] == lg]
        if sub:
            by_lang[lg] = {"hit": sum(1 for r in sub if r["recalled"]), "total": len(sub)}

    crit_leaks = [l for l in leaks if next(t for t in tests if t["id"] == l["id"])["severity"] == "critical"]

    report = {
        "top_k": TOP_K,
        "cases": len(tests),
        "source_recall": {"hit": recall_hit, "total": recall_total,
                          "rate": round(recall_hit / recall_total, 3) if recall_total else None},
        "forbidden_suppression": {"ok": supp_ok, "total": supp_total,
                                  "rate": round(supp_ok / supp_total, 3) if supp_total else None},
        "by_language": by_lang,
        "critical_leaks": crit_leaks,
        "refusal_cases": {"correctly_empty": correct_empty, "total": len(should_be_empty)},
        "leaks": leaks,
        "rows": rows,
    }
    out = domain.eval_dir() / "baseline_retrieval.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Retrieval in isolation: lexical only, before routing and card-scope")
    print("filtering. Leak counts here are higher than end-to-end by design —")
    print("this measures what retrieval alone puts within reach of the model.\n")
    print(f"用例 {len(tests)}  top_k={TOP_K}")
    print(f"来源召回率  {recall_hit}/{recall_total} = {report['source_recall']['rate']:.0%}")
    print(f"禁忌值压制率 {supp_ok}/{supp_total} = {report['forbidden_suppression']['rate']:.0%}")
    print("\n按语言的来源召回:")
    for lg, v in by_lang.items():
        print(f"  {lg:<6} {v['hit']}/{v['total']} = {v['hit']/v['total']:.0%}")
    print(f"\n应拒答题的空召回: {correct_empty}/{len(should_be_empty)}"
          "   (检索层能主动交白卷的比例)")
    print(f"\ncritical 级禁忌值残留: {len(crit_leaks)} 条  ← 主指标,目标为 0")
    for l in crit_leaks:
        print(f"  {l['id']:<5} {str(l['remaining']):<24} trap={l['trap']}")
    print(f"\n-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
