"""Sweep caution against containment on the current domain's test set.

The bank build optimises for never stating a wrong figure and pays 18 handoffs
for it. A telco cannot pay that: its questions repeat, the cost of an error is
lower, and a bot that sends one in eight answerable questions to a human has no
business case. Same pipeline, opposite end of the same curve.

This runs the identical code under several caution settings and reports what
each buys and costs, so the choice of operating point is a decision with
numbers attached rather than a preference.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import domain  # noqa: E402
from llm import get_backend  # noqa: E402
from matching import fact_present, facts_satisfied  # noqa: E402
from pipeline import BANKING, Policy, answer  # noqa: E402
from retrieve import HybridRetriever  # noqa: E402

PROFILES = [
    BANKING,
    Policy(name="soft-prompt", min_score=1.0, dense_floor=0.82, strict_refusal=False),
    Policy(name="telco", min_score=0.6, dense_floor=0.78, strict_refusal=False,
           promote_fallback=True),
    Policy(name="permissive", min_score=0.3, dense_floor=0.74, strict_refusal=False,
           promote_fallback=True, verify_figures=False,
           escalate_unresolved_conflict=False),
]


def score(tests, retriever, backend, policy):
    rows = []
    for t in tests:
        a = answer(t["question"], retriever, backend, policy=policy)
        answered = a.action == "answer"
        forbidden = [f for f in t["forbidden_facts"] if fact_present(f, a.text)] if answered else []
        facts_ok = facts_satisfied(t["key_facts"], a.text) if answered else None
        rows.append({
            "id": t["id"], "severity": t["severity"], "action": a.action,
            # Which layer produced this, so "never reached a model" can be
            # counted rather than inferred.
            "backend": a.backend, "cited": "[" in a.text,
            "answered": answered, "facts_ok": facts_ok,
            "confidently_wrong": bool(forbidden),
            "critical_wrong": bool(forbidden) and t["severity"] == "critical",
            # A question the sources demonstrably answer, sent to a human anyway.
            "avoidable_handoff": a.action == "escalate" and "answer" in t["accepted_behaviors"]
                                 and bool(t["key_facts"]["values"]),
            "behaviour_ok": a.action in t["accepted_behaviors"],
            "text": a.text[:200],
        })
    return rows


def summarise(policy, rows, seconds):
    n = len(rows)
    answered = [r for r in rows if r["answered"]]
    scoreable = [r for r in answered if r["facts_ok"] is not None]
    handled = [r for r in rows if r["action"] in ("answer", "api_lookup", "guide_only")]
    return {
        "policy": policy.name,
        "cases": n,
        "handled_rate": round(len(handled) / n, 3),
        "answer_rate": round(len(answered) / n, 3),
        "escalation_rate": round(sum(1 for r in rows if r["action"] == "escalate") / n, 3),
        "refusal_rate": round(sum(1 for r in rows if r["action"] == "refuse") / n, 3),
        "critical_wrong": sum(1 for r in rows if r["critical_wrong"]),
        "confidently_wrong": sum(1 for r in rows if r["confidently_wrong"]),
        "facts_correct": f"{sum(1 for r in scoreable if r['facts_ok'])}/{len(scoreable)}"
                         if scoreable else "-",
        "avoidable_handoffs": sum(1 for r in rows if r["avoidable_handoff"]),
        "behaviour_ok": round(sum(1 for r in rows if r["behaviour_ok"]) / n, 3),
        "seconds": round(seconds),
    }


def main():
    wanted = set(sys.argv[1:])
    profiles = [p for p in PROFILES if not wanted or p.name in wanted]
    tests = [json.loads(l) for l in (domain.eval_dir() / "testset.jsonl").open(encoding="utf-8")]
    retriever = HybridRetriever()
    backend = get_backend("local")

    results, detail = [], {}
    for policy in profiles:
        started = time.time()
        rows = score(tests, retriever, backend, policy)
        results.append(summarise(policy, rows, time.time() - started))
        detail[policy.name] = rows
        print(f"  {policy.name} 完成 ({results[-1]['seconds']}s)", flush=True)

    suffix = "" if not wanted else "_" + "_".join(sorted(wanted))
    out = domain.eval_dir() / f"policy_sweep{suffix}.json"
    out.write_text(json.dumps({"summary": results, "detail": detail},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    cols = [("policy", "策略", 12), ("handled_rate", "自助处理率", 11),
            ("answer_rate", "作答率", 9), ("escalation_rate", "转人工率", 10),
            ("critical_wrong", "critical错误", 12), ("facts_correct", "事实正确", 10),
            ("avoidable_handoffs", "本可作答却转人工", 16)]
    print("\n" + "".join(f"{label:<{w}}" for _, label, w in cols))
    print("-" * sum(w for _, _, w in cols))
    for r in results:
        line = ""
        for key, _, w in cols:
            v = r[key]
            v = f"{v:.0%}" if isinstance(v, float) else str(v)
            line += f"{v:<{w}}"
        print(line)
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
