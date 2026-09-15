"""Extract the plan and roaming tables into structured fields.

Why this exists rather than more retrieval tuning: on the telco set the model
answers factual questions correctly 49% of the time against 79% on the bank
corpus, and the difference is the shape of the source. The bank publishes prose
("you must pay a finance charge at a prevailing interest rate of 27.8% p.a.").
Singtel publishes a comparison grid, which arrives as one flat string per plan:

    Enhanced Lite 12-mths contract $24.50/mth $35.00/mth Get Data 300GB local
    data 10GB Malaysia Roaming ... Comes with 400 mins Talktime & 400 SMS

A small model reading that picks the wrong figure, states the wrong negation,
or names the plan without its price. None of those are retrieval failures.

The grid is regular, so the fields can be parsed once and answered from
deterministically, the way account figures already are. Every extracted value
is checked back against the chunk it came from, so a layout change on Singtel's
side fails loudly here instead of silently producing wrong answers.

Run after src/ingest.py:
    ASSISTANT_DOMAIN=telco uv run python scripts/extract_plans.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import domain  # noqa: E402

UNLIMITED = "UNLIMITED"


def _first(pattern, text, group=1, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def parse_plan(chunk):
    text = re.sub(r"\s+", " ", chunk["text"])
    prices = re.findall(r"\$(\d+(?:\.\d{2})?)/mth", text)
    if len(prices) < 2:
        return None

    name = _first(r"·\s*([a-z ]+?)\s+(?:Enhanced|Priority|Seniors|[A-Z])", text) \
        or chunk["section"].split("·")[-1].strip()
    # Case-sensitive: the section header repeats the name in lower case, and an
    # ignore-case match picks that up instead of the product's own wording.
    display = _first(r"((?:Enhanced|Priority)\s+(?:Lite|Core|Plus|Ultra)|Seniors SIM Only Plan)",
                     text, flags=0)

    local = _first(r"Get Data (UNLIMITED|[\d.]+GB) local data", text)
    if not local:
        # A block with prices but no data allowance is a promotional banner,
        # not a plan.
        return None

    # Key comes from the display name, not the section header: the header
    # capture is non-greedy and stopped at the first word, collapsing
    # "Enhanced Lite" and "Enhanced Core" onto the same key.
    label = display or name.strip().title()
    return {
        "key": label.lower(),
        "name": label,
        "monthly_price": float(prices[0]),
        "usual_price": float(prices[1]),
        "contract": "12 months" if re.search(r"12-mths contract", text, re.I) else "none",
        "local_data": local,
        "malaysia_roaming": _first(r"(UNLIMITED|[\d.]+GB) Malaysia Roaming", text),
        "asia_roaming": _first(r"(UNLIMITED|[\d.]+GB) Asia Roaming", text),
        "worldwide_roaming": _first(r"(UNLIMITED|[\d.]+GB) Worldwide Roaming", text),
        "talktime": _first(r"(UNLIMITED|\d+) (?:mins Talktime|calls)", text),
        "sms": _first(r"&\s*(UNLIMITED|\d+) SMS", text),
        "age_limit": "60 and above" if re.search(r"seniors aged 60", text, re.I) else None,
        "source_chunk": chunk["chunk_id"],
    }


def parse_passes(chunks):
    passes = []
    seen = set()
    for chunk in chunks:
        text = re.sub(r"\s+", " ", chunk["text"])
        for m in re.finditer(r"UnlimitedRoam (\w+)(.{0,140}?)\$(\d+) for (\d+) days", text, re.I):
            tier, middle, price, days = m.groups()
            if tier.lower() in seen:
                continue
            seen.add(tier.lower())
            destinations = re.sub(r"\s+", " ", middle).strip(" –-|")
            passes.append({
                "key": f"unlimitedroam {tier.lower()}",
                "name": f"UnlimitedRoam {tier.title()}",
                "price": float(price),
                "days": int(days),
                "destinations": destinations[:120],
                "discount_priority": _first(r"(\d+)% OFF for 5G\+ Priority", text),
                "discount_enhanced": _first(r"(\d+)% OFF for 5G\+ Enhanced", text),
                "source_chunk": chunk["chunk_id"],
            })
    return passes


def parse_prepaid(chunks):
    """hi! prepaid tiers: price, validity and the data bundle."""
    out, seen = [], set()
    for chunk in chunks:
        text = re.sub(r"\s+", " ", chunk["text"])
        for m in re.finditer(r"\$(\d+)/(\d+) days(.{0,120}?)(?:Get Local and roaming data )?"
                             r"([\d.]+GB|Unlimited\*?)", text, re.I):
            price, days, middle, data = m.groups()
            key = f"hi! ${price}"
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "key": key.lower(), "name": f"hi! ${price}/{days} days",
                "price": float(price), "days": int(days), "data": data,
                "note": re.sub(r"\s+", " ", middle).strip()[:80],
                "senior": bool(re.search(r"senior", middle, re.I)),
                "source_chunk": chunk["chunk_id"],
            })
    return out


def parse_charges(chunks):
    """The two late-payment charges, which are separate fees a customer
    routinely conflates and the model did too: asked for the late payment fee
    it answered with the reminder notice charge."""
    out = []
    for chunk in chunks:
        text = re.sub(r"\s+", " ", chunk["text"])
        reminder = _first(r"payment reminder notice and \$([\d.]+)", text)
        if reminder:
            out.append({"key": "reminder notice charge", "name": "Reminder notice charge",
                        "amount": float(reminder), "gst": "inclusive",
                        "when": "issued when the payment due date is missed",
                        "source_chunk": chunk["chunk_id"]})
        final = _first(r"late payment fee of \$([\d.]+)", text)
        if final:
            out.append({"key": "late payment fee", "name": "Late payment fee",
                        "amount": float(final), "gst": "inclusive",
                        "when": "issued with the final reminder notice",
                        "source_chunk": chunk["chunk_id"]})
    return out


def verify(records, chunks):
    """Every figure must be findable in the chunk it was taken from.

    Extraction is only worth trusting if it fails loudly, so a value that no
    longer appears in its source is an error rather than a stale field.
    """
    by_id = {c["chunk_id"]: re.sub(r"\s+", " ", c["text"]) for c in chunks}
    problems = []
    for r in records:
        source = by_id.get(r["source_chunk"], "")
        for field, value in r.items():
            if field in ("key", "name", "source_chunk", "contract", "age_limit",
                         "destinations", "gst", "when", "note", "senior") or value is None:
                continue
            needle = f"{value:g}" if isinstance(value, float) else str(value)
            if needle not in source:
                problems.append(f"{r['key']}.{field} = {needle!r} 在来源块中找不到")
    return problems


def main():
    domain.use("telco")
    chunks = [json.loads(l) for l in domain.chunks_path().open(encoding="utf-8")]

    plans = [p for p in (parse_plan(c) for c in chunks
                         if c["doc_id"] == "sim-only-plans") if p]
    passes = parse_passes([c for c in chunks if c["doc_id"] == "roaming"])
    prepaid = parse_prepaid([c for c in chunks if c["doc_id"] == "hi-prepaid"])
    charges = parse_charges([c for c in chunks if c["doc_id"] == "late-payment-fees"])

    problems = (verify(plans, chunks) + verify(passes, chunks)
                + verify(prepaid, chunks) + verify(charges, chunks))
    out = domain.processed_dir() / "plans.json"
    out.write_text(json.dumps({"plans": plans, "roaming_passes": passes,
                               "prepaid": prepaid, "charges": charges},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"套餐 {len(plans)} · 漫游包 {len(passes)} · 预付 {len(prepaid)} · 收费 {len(charges)} -> {out}\n")
    for p in plans:
        print(f"  {p['name']:<26} ${p['monthly_price']:>6.2f} (原价 ${p['usual_price']:.2f}) "
              f"合约 {p['contract']:<10} 本地 {p['local_data']:<10} 马来 {p['malaysia_roaming']}")
    for p in passes:
        print(f"  {p['name']:<26} ${p['price']:>6.0f} / {p['days']} 天   {p['destinations'][:52]}")
    for c in charges:
        print(f"  {c['name']:<26} ${c['amount']:>6.2f}   {c['when']}")
    for c in prepaid:
        print(f"  {c['name']:<26} ${c['price']:>6.0f} / {c['days']} 天   {c['data']}")
    print(f"\n回指校验: {'通过' if not problems else str(len(problems)) + ' 处不符'}")
    for pr in problems:
        print("  ", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
