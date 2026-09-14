"""End-to-end evaluation over the full test set.

Reports two metrics that must be read together. "Confidently wrong" counts
answers that state a value the case forbids; "wrongly refused" counts cases
with a supported answer that the system declined. Optimising either one alone
produces a system that is useless in the opposite direction, so neither is
reported without the other.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import domain  # noqa: E402
sys.path.insert(0, str(ROOT / "eval"))

from llm import get_backend  # noqa: E402
from matching import fact_present, facts_satisfied  # noqa: E402
from pipeline import NO_CONTEXT_REPLY, answer  # noqa: E402
from retrieve import HybridRetriever  # noqa: E402

REFUSAL_MARKERS = ("抱歉", NO_CONTEXT_REPLY, "无法回答", "转接",
                   "Sorry, I", "transferring you", "verify your identity")


def is_refusal(text):
    return any(m in text for m in REFUSAL_MARKERS)


def main():
    tests = [json.loads(l) for l in (domain.eval_dir() / "testset.jsonl").open(encoding="utf-8")]
    retriever = HybridRetriever()
    backend = get_backend("local")

    rows = []
    started = time.time()
    for n, t in enumerate(tests, 1):
        a = answer(t["question"], retriever, backend)
        answered = a.action == "answer" and not is_refusal(a.text)

        forbidden_hit = [f for f in t["forbidden_facts"] if fact_present(f, a.text)]
        facts_ok = facts_satisfied(t["key_facts"], a.text) if answered else None
        cited = "[" in a.text

        rows.append({
            "id": t["id"], "layer": t["layer"], "severity": t["severity"],
            "lang": t["lang"], "trap": t["trap"],
            "expected": t["accepted_behaviors"], "action": a.action,
            "behaviour_ok": a.action in t["accepted_behaviors"],
            "answered": answered, "facts_ok": facts_ok,
            "confidently_wrong": bool(forbidden_hit) and answered,
            "forbidden_hit": forbidden_hit,
            # A supported answer that was declined. Only counted where the
            # case asserts facts, i.e. an answer demonstrably existed.
            "wrongly_refused": (not answered) and "answer" in t["accepted_behaviors"]
                               and bool(t["key_facts"]["values"]),
            "cited": cited if answered else None,
            "backend": a.backend, "seconds": a.seconds, "text": a.text[:300],
        })
        if n % 25 == 0:
            print(f"  {n}/{len(tests)}  ({time.time() - started:.0f}s)", flush=True)

    out = domain.eval_dir() / "e2e_results.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def rate(pred, pool=rows):
        hit = sum(1 for r in pool if pred(r))
        return hit, len(pool), (hit / len(pool) if pool else 0)

    crit = [r for r in rows if r["severity"] == "critical"]
    answered = [r for r in rows if r["answered"]]
    scoreable = [r for r in answered if r["facts_ok"] is not None]

    print(f"\n{'='*62}\n用例 {len(rows)}  耗时 {time.time()-started:.0f}s"
          f"  调用模型 {sum(1 for r in rows if r['backend'] not in ('router','retriever'))} 次\n")
    print(f"行为一致率        {rate(lambda r: r['behaviour_ok'])[0]}/{len(rows)} = {rate(lambda r: r['behaviour_ok'])[2]:.0%}")
    print(f"  critical        {rate(lambda r: r['behaviour_ok'], crit)[0]}/{len(crit)} = {rate(lambda r: r['behaviour_ok'], crit)[2]:.0%}")
    if scoreable:
        h, n, p = rate(lambda r: r["facts_ok"], scoreable)
        print(f"答案事实正确率    {h}/{n} = {p:.0%}   (在已作答且有标注事实的用例上)")
    print(f"引用率            {rate(lambda r: r['cited'], answered)[0]}/{len(answered)} = {rate(lambda r: r['cited'], answered)[2]:.0%}")
    print()
    cw = [r for r in rows if r["confidently_wrong"]]
    cwc = [r for r in cw if r["severity"] == "critical"]
    wr = [r for r in rows if r["wrongly_refused"]]
    print(f"错误且自信        {len(cw)} 条,其中 critical {len(cwc)} 条   ← 主指标,目标 0")
    for r in cwc:
        print(f"   {r['id']:<6} 出现禁忌值 {r['forbidden_hit']}  trap={r['trap']}")
    print(f"有据可答却拒答    {len(wr)} 条   ← 对偶指标")
    for r in wr[:10]:
        print(f"   {r['id']:<6} {r['action']:<11} {r['text'][:60]}")
    print(f"\n-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
