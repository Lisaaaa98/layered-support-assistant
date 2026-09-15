"""Answer plan questions from the extracted table, not from the model.

The account layer already works this way, for the same reason: a figure that
can be looked up exactly gains nothing by being restated by a probabilistic
system, and can lose something. Plan data qualifies once it has been parsed
into fields, and the measurements say it should be: the model answers plan
questions from the flattened comparison grid correctly about half the time,
and the errors are the expensive kind — a wrong price, or a flat denial that a
plan includes roaming it does include.

Recommendation is here too, and is the one capability the bank build has no
equivalent of. A bank must not tell a customer which product to take; a telco
doing exactly that is customer service. Doing it in code keeps the reasoning
inspectable: the answer names the constraint each plan failed, so a customer
disagreeing with the recommendation can see why it was made.

Returns None whenever the question does not resolve cleanly, so the caller
falls back to ordinary retrieval rather than receiving a guess.
"""
import json
import re

import domain

UNLIMITED = "UNLIMITED"


def _load():
    path = domain.processed_dir() / "plans.json"
    if not path.exists():
        return {"plans": [], "roaming_passes": []}
    return json.loads(path.read_text(encoding="utf-8"))


def available():
    return bool(_load()["plans"])


def _lang(text):
    from query import has_han

    return "zh" if has_han(text) else "en"


def _gb(value):
    """Allowance as a number for comparison. Unlimited sorts above everything."""
    if not value:
        return None
    if value.upper().startswith(UNLIMITED):
        return float("inf")
    m = re.match(r"([\d.]+)", value)
    return float(m.group(1)) if m else None


# Question wording -> the field that answers it. Explicit, so a plan question
# can never be served by a field that merely looks related.
# Order matters: "how much" appears in every one of these questions, so the
# specific subject has to be tested before the generic price wording, or a
# question about a roaming allowance is answered with a monthly price.
ATTRIBUTES = [
    ("malaysia_roaming", r"malaysia|msia|马来|大马"),
    ("worldwide_roaming", r"worldwide|global|全球|世界"),
    ("asia_roaming", r"asia roaming|\basia\b|亚洲"),
    ("local_data", r"local data|data allowance|how much data|本地流量|多少流量|流量"),
    ("talktime", r"talktime|minutes|\bmins\b|通话|分钟"),
    ("sms", r"\bsms\b|text message|短信"),
    ("contract", r"contract|lock[- ]?in|合约|签约|绑约"),
    ("age_limit", r"\bage\b|年龄"),
    ("monthly_price", r"how much|price|cost|多少钱|价格|月费|费用|多少"),
]

LABEL = {
    "monthly_price": ("monthly price", "月费"),
    "usual_price": ("usual price", "原价"),
    "contract": ("contract", "合约"),
    "local_data": ("local data", "本地流量"),
    "malaysia_roaming": ("Malaysia roaming", "马来西亚漫游"),
    "asia_roaming": ("Asia roaming", "亚洲漫游"),
    "worldwide_roaming": ("worldwide roaming", "全球漫游"),
    "talktime": ("talktime", "通话"),
    "sms": ("SMS", "短信"),
    "age_limit": ("eligibility", "适用年龄"),
}


def _find_plan(question, table):
    low = question.lower()
    for plan in table["plans"]:
        if plan["key"] in low or plan["name"].lower() in low:
            return plan
    # "seniors plan" and similar wording that does not repeat the product name.
    if re.search(r"senior|长者|老年", low):
        return next((p for p in table["plans"] if p["age_limit"]), None)
    return None


def _find_pass(question, table):
    low = question.lower()
    for p in table["roaming_passes"]:
        tier = p["key"].split()[-1]
        if p["key"] in low or re.search(rf"unlimitedroam\s+{tier}|{tier}\b", low):
            return p
    return None


def _render_plan(plan, field, lang):
    value = plan.get(field)
    if value is None:
        return None
    label_en, label_zh = LABEL[field]

    if field == "monthly_price":
        return (f"{plan['name']} 的月费为 ${plan['monthly_price']:.2f}"
                f"(原价 ${plan['usual_price']:.2f})。" if lang == "zh" else
                f"{plan['name']} is ${plan['monthly_price']:.2f}/mth "
                f"(usual price ${plan['usual_price']:.2f}).")
    if field == "contract":
        if value == "none":
            return (f"{plan['name']} 无合约。" if lang == "zh"
                    else f"{plan['name']} has no contract.")
        return (f"{plan['name']} 为 {value} 合约。" if lang == "zh"
                else f"{plan['name']} is on a {value} contract.")
    # Allowances carry their own unit; counts do not, so they need one added.
    unit_en, unit_zh = {"talktime": (" mins", " 分钟"), "sms": ("", " 条")}.get(field, ("", ""))
    unlimited = value.upper().startswith(UNLIMITED)
    shown_en = value if unlimited else f"{value}{unit_en}"
    shown_zh = value if unlimited else f"{value}{unit_zh}"
    return (f"{plan['name']} 的{label_zh}为 {shown_zh}。" if lang == "zh"
            else f"{plan['name']} includes {shown_en} of {label_en}.")


CHARGE_WORDS = {
    "late payment fee": r"late payment fee|final (?:reminder|notice)|逾期费|最终.{0,4}通知",
    "reminder notice charge": r"reminder (?:notice|charge)|催缴|提醒通知",
}


def _charges(question, table, lang):
    """The two late-payment charges are separate fees. Asked for one, the
    model answered with the other, which is the single critical error the
    telco set still recorded."""
    low = question.lower()
    records = {c["key"]: c for c in table.get("charges", [])}
    if not records:
        return None
    wanted = [k for k, pattern in CHARGE_WORDS.items() if re.search(pattern, low)]
    asks_both = len(wanted) == 2 or re.search(r"分别|both|and the", low)

    if asks_both or (not wanted and re.search(r"逾期|late payment|miss(?:ed)? .{0,12}payment", low)):
        parts = [records[k] for k in ("reminder notice charge", "late payment fee") if k in records]
        if not parts:
            return None
        if lang == "zh":
            return "、".join(f"{'催缴通知费' if p['key'].startswith('reminder') else '最终逾期费'}"
                            f" ${p['amount']:.2f}" for p in parts) + "(均含消费税)。"
        return "; ".join(f"{p['name']} is ${p['amount']:.2f}" for p in parts) + " (both incl. GST)."
    if len(wanted) == 1:
        c = records[wanted[0]]
        return (f"{c['name']} 为 ${c['amount']:.2f}(含消费税),{c['when']}。" if lang == "zh"
                else f"The {c['name'].lower()} is ${c['amount']:.2f} (incl. GST), {c['when']}.")
    return None


def _prepaid(question, table, lang):
    low = question.lower()
    tiers = table.get("prepaid", [])
    if not tiers or not re.search(r"hi!|prepaid|预付|储值", low):
        return None
    m = re.search(r"\$(\d+)", low)
    if m:
        tier = next((t for t in tiers if t["price"] == float(m.group(1))), None)
        if tier:
            return (f"{tier['name']} 提供 {tier['data']} 流量,有效期 {tier['days']} 天。" if lang == "zh"
                    else f"{tier['name']} includes {tier['data']} of data for {tier['days']} days.")
    if re.search(r"senior|长者", low):
        tier = next((t for t in tiers if t["senior"]), None)
        if tier:
            return (f"{tier['name']},{tier['data']} 流量,适用长者。" if lang == "zh"
                    else f"{tier['name']} with {tier['data']} of data, for seniors.")
    if re.search(r"most data|最多流量|流量最多", low):
        tier = max(tiers, key=lambda t: (t["data"].upper().startswith("UNLIMITED"),
                                         _gb(t["data"]) or 0))
        return (f"流量最多的是 {tier['name']},{tier['data']}。" if lang == "zh"
                else f"The most data is on {tier['name']}, with {tier['data']}.")
    return None


def _roaming_discount(question, table, lang):
    low = question.lower()
    if not re.search(r"discount|% off|折扣|优惠", low):
        return None
    tier = "priority" if re.search(r"priority", low) else ("enhanced" if re.search(r"enhanced", low) else None)
    if not tier:
        return None
    field = f"discount_{tier}"
    value = next((p[field] for p in table.get("roaming_passes", []) if p.get(field)), None)
    if not value:
        return None
    return (f"5G+ {tier.title()} 客户的漫游通行证享 {value}% 折扣。" if lang == "zh"
            else f"5G+ {tier.title()} customers get {value}% off roaming passes.")


def lookup(question):
    """Answer one plan, pass, prepaid or charge question, or return None."""
    table = _load()
    if not table["plans"]:
        return None
    lang = _lang(question)
    low = question.lower()

    for handler in (_charges, _prepaid, _roaming_discount):
        served = handler(question, table, lang)
        if served:
            return served

    pass_record = _find_pass(question, table)
    if pass_record and re.search(r"how much|price|cost|多少钱|价格|多少", low):
        return (f"{pass_record['name']} 为 ${pass_record['price']:.0f},"
                f"有效期 {pass_record['days']} 天。" if lang == "zh" else
                f"{pass_record['name']} is ${pass_record['price']:.0f} "
                f"for {pass_record['days']} days.")

    plan = _find_plan(question, table)
    if not plan:
        return None
    field = next((f for f, pattern in ATTRIBUTES if re.search(pattern, low)), None)
    if not field:
        # "Is there a plan for seniors?" names a product without naming a field.
        if re.search(r"is there|do you have|有没有|有.{0,4}吗", low):
            return _render_plan(plan, "monthly_price", lang)
        return None
    return _render_plan(plan, field, lang)


RECOMMEND = r"which plan|recommend|suitable|best plan|should i get|哪个套餐|推荐|适合|哪个好|划算"


def recommend(question):
    """Rank plans against the constraints stated in the question.

    Every rejected plan is reported with the constraint it failed, because a
    recommendation a customer cannot check is worth little in a channel whose
    job is to be trusted.
    """
    table = _load()
    if not table["plans"] or not re.search(RECOMMEND, question.lower()):
        return None

    low = question.lower()
    lang = _lang(question)

    need_gb = None
    m = re.search(r"(\d+)\s*gb", low)
    if m:
        need_gb = float(m.group(1))
    budget = None
    m = re.search(r"(?:under|below|less than|budget|不超过|以内|预算)\D{0,6}\$?(\d+)", low)
    if m:
        budget = float(m.group(1))
    wants_malaysia = bool(re.search(r"malaysia|msia|johor|jb\b|马来|大马", low))
    wants_senior = bool(re.search(r"senior|长者|老年|60", low))

    if not any([need_gb, budget, wants_malaysia, wants_senior]):
        return None  # no constraint to reason about; let retrieval answer

    kept, rejected = [], []
    for plan in table["plans"]:
        if plan["age_limit"] and not wants_senior:
            rejected.append((plan, "仅限 60 岁及以上" if lang == "zh" else "seniors only"))
            continue
        if need_gb is not None and (_gb(plan["local_data"]) or 0) < need_gb:
            rejected.append((plan, f"本地流量 {plan['local_data']} 不足" if lang == "zh"
                             else f"only {plan['local_data']} local data"))
            continue
        if budget is not None and plan["monthly_price"] > budget:
            rejected.append((plan, f"月费 ${plan['monthly_price']:.2f} 超出预算" if lang == "zh"
                             else f"${plan['monthly_price']:.2f} is over budget"))
            continue
        if wants_malaysia and not plan["malaysia_roaming"]:
            rejected.append((plan, "不含马来西亚漫游" if lang == "zh"
                             else "no Malaysia roaming"))
            continue
        kept.append(plan)

    if not kept:
        return ("按你说的条件,现有套餐都不满足。以下为各套餐未通过的原因:\n"
                + "\n".join(f"  {p['name']}:{why}" for p, why in rejected) if lang == "zh" else
                "No current plan meets all of those. Here is what each one fails on:\n"
                + "\n".join(f"  {p['name']}: {why}" for p, why in rejected))

    kept.sort(key=lambda p: p["monthly_price"])
    best = kept[0]
    reasons = []
    if need_gb is not None:
        reasons.append(f"本地流量 {best['local_data']}" if lang == "zh"
                       else f"{best['local_data']} local data")
    if wants_malaysia:
        reasons.append(f"马来西亚漫游 {best['malaysia_roaming']}" if lang == "zh"
                       else f"{best['malaysia_roaming']} Malaysia roaming")
    detail = "、".join(reasons) if lang == "zh" else ", ".join(reasons)

    if lang == "zh":
        text = (f"按你说的条件,最便宜的是 {best['name']},月费 ${best['monthly_price']:.2f}"
                f"(原价 ${best['usual_price']:.2f})")
        text += f",{detail}。" if detail else "。"
        if len(kept) > 1:
            text += "\n其他符合条件的:" + "、".join(
                f"{p['name']} ${p['monthly_price']:.2f}" for p in kept[1:])
        if rejected:
            text += "\n未选择的原因:" + ";".join(f"{p['name']} {why}" for p, why in rejected)
        return text

    text = (f"The cheapest plan meeting that is {best['name']} at "
            f"${best['monthly_price']:.2f}/mth (usual ${best['usual_price']:.2f})")
    text += f", with {detail}." if detail else "."
    if len(kept) > 1:
        text += "\nAlso qualifying: " + ", ".join(
            f"{p['name']} at ${p['monthly_price']:.2f}" for p in kept[1:])
    if rejected:
        text += "\nRuled out: " + "; ".join(f"{p['name']} ({why})" for p, why in rejected)
    return text


def answer(question):
    """Table-backed answer, or None to let the normal pipeline handle it."""
    return recommend(question) or lookup(question)
