"""Author the telco evaluation set.

Same schema as the bank set, one deliberate difference in policy: recommending
a plan is normal customer service here, so "which plan should I get" is a case
the assistant is expected to answer, not decline. The bank set marks the
equivalent question as advice it must refuse. That single flip is the clearest
statement of why the two industries need different tuning of the same pipeline.

The severity mix differs too. A wrong monthly price or late-payment fee still
costs the customer money and stays critical; a wrong roaming destination list
is an annoyance and does not.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import domain  # noqa: E402
from casekit import CaseSet  # noqa: E402

domain.use("telco")
S = CaseSet()
case = S.case

# ══ 套餐资费 ══════════════════════════════════════════════════════
case("P001", "How much is the Enhanced Lite SIM Only plan?", "en",
     "$24.50/mth promotional, usual price $35.00/mth.",
     facts=["$24.50", "$35.00"], mode="any", sources=["sim-only-plans"], sev="critical",
     note="页面同时载明促销价与原价,两者皆为事实;引用原价并非错误,故任一即可")
case("P002", "Enhanced Lite 套餐多少钱?", "zh",
     "促销价每月 $24.50,原价 $35.00。",
     facts=["$24.50", "$35.00"], mode="any", sources=["sim-only-plans"], sev="critical")
case("P003", "What is the usual price of Enhanced Lite before the promotion?", "en",
     "$35.00/mth.", facts=["$35.00"], sources=["sim-only-plans"], sev="critical",
     note="促销价与原价并存,属同一事实的两个口径,不是冲突")
case("P004", "How much is Enhanced Core?", "en", "$36.00/mth, usual price $40.00.",
     facts=["$36.00", "$40.00"], mode="any", forbid=["$24.50"], sources=["sim-only-plans"],
     sev="critical", trap="cross_product")
case("P005", "How much is Priority Plus?", "en", "$49.50/mth, usual price $55.00.",
     facts=["$49.50", "$55.00"], mode="any", forbid=["$24.50", "$36.00"],
     sources=["sim-only-plans"], sev="critical", trap="cross_product")
case("P006", "How much is Priority Ultra?", "en", "$72.00/mth, usual price $80.00.",
     facts=["$72.00", "$80.00"], mode="any", forbid=["$49.50"], sources=["sim-only-plans"],
     sev="critical", trap="cross_product")
case("P007", "Priority Ultra 每月多少钱?", "zh", "促销价每月 $72.00,原价 $80.00。",
     facts=["$72.00", "$80.00"], mode="any", sources=["sim-only-plans"], sev="critical")
case("P008", "Is there a plan for seniors?", "en",
     "Seniors SIM Only Plan at $6.00/mth, no contract.",
     facts=["$6.00"], sources=["sim-only-plans"], sev="critical")
case("P009", "长者套餐多少钱?", "zh", "长者 SIM Only 每月 $6.00。",
     facts=["$6.00"], sources=["sim-only-plans"], sev="critical")
case("P010", "How much local data does Enhanced Lite give me?", "en",
     "300GB local data.", facts=["300GB"], sources=["sim-only-plans"])
case("P011", "Enhanced Lite 有多少本地流量?", "zh", "300GB 本地流量。",
     facts=["300GB"], sources=["sim-only-plans"])
case("P012", "Does Enhanced Core have unlimited data?", "en",
     "Yes, unlimited local data.", facts=[], sources=["sim-only-plans"])
case("P013", "How much Malaysia roaming is included with Enhanced Lite?", "en",
     "10GB Malaysia roaming.", facts=["10GB"], forbid=["50GB"],
     sources=["sim-only-plans"], trap="cross_product")
case("P014", "How much Malaysia roaming comes with Enhanced Core?", "en",
     "50GB Malaysia roaming.", facts=["50GB"], forbid=["10GB"],
     sources=["sim-only-plans"], trap="cross_product")
case("P015", "How many talktime minutes does Enhanced Lite include?", "en",
     "400 mins talktime and 400 SMS.", facts=["400"], sources=["sim-only-plans"])
case("P016", "Is Enhanced Lite a contract plan?", "en",
     "Yes, 12-month contract.", facts=["12"], sources=["sim-only-plans"])
case("P017", "Does Priority Plus need a contract?", "en",
     "No contract.", facts=[], sources=["sim-only-plans"])
case("P018", "SIM Only 计划需要签约吗?", "zh",
     "视套餐而定:Enhanced Lite 为 12 个月合约,Enhanced Core 与 Priority 系列无合约。",
     facts=[], sources=["sim-only-plans"], beh=("answer", "clarify"))

# ══ 漫游 ══════════════════════════════════════════════════════════
case("R001", "How much is UnlimitedRoam Asia?", "en", "$35 for 14 days.",
     facts=["$35"], sources=["roaming"], sev="critical")
case("R002", "UnlimitedRoam Asia 多少钱?", "zh", "14 天 $35。",
     facts=["$35"], sources=["roaming"], sev="critical")
case("R003", "How much is UnlimitedRoam Worldwide?", "en", "$45 for 14 days.",
     facts=["$45"], forbid=["$35"], sources=["roaming"], sev="critical",
     trap="cross_product")
case("R004", "How much is UnlimitedRoam Neighbours?", "en",
     "$20 for 14 days, covering Malaysia, Indonesia and Thailand.",
     facts=["$20"], forbid=["$45"], sources=["roaming"], sev="critical",
     trap="cross_product")
case("R005", "What is the cheapest way to roam in Johor Bahru?", "en",
     "ReadyRoam plans start from $5.", facts=["$5"], sources=["roaming"])
case("R006", "去马来西亚短途漫游最便宜的选择是什么?", "zh",
     "ReadyRoam 起价 $5。", facts=["$5"], sources=["roaming"])
case("R007", "Which countries does UnlimitedRoam Worldwide cover?", "en",
     "135 destinations including France, the UK and the USA.",
     facts=["135"], sources=["roaming"])
case("R008", "How many destinations does UnlimitedRoam Asia cover?", "en",
     "19 destinations.", facts=["19"], forbid=["135"], sources=["roaming"],
     trap="cross_product")
case("R009", "Do Priority customers get a roaming discount?", "en",
     "30% off roaming passes for 5G+ Priority.",
     facts=["30%"], sources=["roaming"], sev="critical")
case("R010", "Enhanced 用户漫游有折扣吗?", "zh", "5G+ Enhanced 享 15% 折扣。",
     facts=["15%"], forbid=["30%"], sources=["roaming"], sev="critical",
     trap="cross_product")
case("R011", "roaming pass 的有效期多久?", "mixed", "UnlimitedRoam 系列为 14 天。",
     facts=["14"], sources=["roaming"])
case("R012", "Is there a fair usage policy on roaming?", "en",
     "Yes, a fair usage policy applies.", facts=[], sources=["roaming"])

# ══ 账单与逾期 ════════════════════════════════════════════════════
case("B001", "What happens if I miss my bill payment?", "en",
     "A payment reminder notice is sent and $5.45 (incl. GST) applies.",
     facts=["$5.45"], sources=["late-payment-fees"], sev="critical")
case("B002", "逾期没交话费会怎样?", "zh",
     "会收到催缴通知并收取 $5.45(含消费税)。",
     facts=["$5.45"], sources=["late-payment-fees"], sev="critical")
case("B003", "How much is the late payment fee?", "en",
     "$21.80 (incl. GST) on the final reminder notice.",
     facts=["$21.80"], forbid=["$5.45"], sources=["late-payment-fees"], sev="critical",
     note="催缴通知费与最终逾期费是两笔不同的收费,最易混淆")
case("B004", "催缴通知费和逾期费分别是多少?", "zh",
     "催缴通知 $5.45,最终通知的逾期费 $21.80,均含消费税。",
     facts=["$5.45", "$21.80"], mode="all", sources=["late-payment-fees"], sev="critical")
case("B005", "Are the late payment charges inclusive of GST?", "en",
     "Yes, both figures are inclusive of GST.", facts=[], sources=["late-payment-fees"])
case("B006", "Why was my service suspended?", "en",
     "Service can be suspended for late or non-payment; explain how to check and settle.",
     facts=[], sources=["service-suspension"])
case("B007", "停机了怎么恢复?", "zh", "结清欠款后恢复,说明查询与缴费渠道。",
     facts=[], sources=["service-suspension"])
case("B008", "How do I check my detailed bill charges?", "en",
     "Through My Account or the My Singtel app.", facts=[], sources=["bill-charges"])
case("B009", "How do I check my data usage?", "en",
     "Through My Account with a OnePass login, or the My Singtel app.",
     facts=[], sources=["check-data-usage"])
case("B010", "怎么查我的流量用了多少?", "zh",
     "通过 My Account 或 My Singtel App 查询。", facts=[], sources=["check-data-usage"])

# ══ 预付 ══════════════════════════════════════════════════════════
case("H001", "How much is the hi! prepaid plan with the most data?", "en",
     "$20 for 30 days with 700GB.", facts=["$20"], sources=["hi-prepaid"])
case("H002", "What does the $15 hi! plan include?", "en",
     "500GB of 5G+ local and roaming data for 30 days.",
     facts=["500GB"], forbid=["700GB"], sources=["hi-prepaid"], trap="cross_product")
case("H003", "hi! 有长者套餐吗?", "zh", "有,$7 hi! Senior Plan,适用 60 岁及以上。",
     facts=["$7"], sources=["hi-prepaid"])
case("H004", "How long does a hi! prepaid plan last?", "en", "30 days.",
     facts=["30 days"], sources=["hi-prepaid"])
case("H005", "预付卡和后付套餐有什么区别?", "zh",
     "预付按有效期充值使用,后付按月账单结算;可说明两类产品各自的入口。",
     facts=[], sources=["hi-prepaid", "sim-only-plans"])
case("H006", "Can I port in to a hi! prepaid plan?", "en",
     "Yes, with a port-in offer, though not from Singtel or GOMO by Singtel.",
     facts=[], sources=["hi-prepaid"])

# ══ 套餐推荐:电信允许,银行禁止 ═══════════════════════════════════
case("D001", "Which plan should I get if I use about 40GB a month?", "en",
     "可基于流量说明各档位,推荐属正常客服范围,不构成越权建议。",
     facts=[], sources=["sim-only-plans"], sev="normal", trap="recommendation",
     note="与银行集的对应用例相反:银行必须拒答,电信应当作答")
case("D002", "我每个月常去马来西亚,哪个套餐合适?", "zh",
     "可比较各套餐的马来西亚漫游额度并说明差异。",
     facts=[], sources=["sim-only-plans", "roaming"], trap="recommendation")
case("D003", "Which is better value, Enhanced Core or Priority Plus?", "en",
     "可陈述两者价格与额度差异供客户判断。",
     facts=[], sources=["sim-only-plans"], trap="recommendation")
case("D004", "Should I take a loan to buy the phone?", "en",
     "属信贷建议,应拒答。", facts=[], sources=[], beh=("refuse",), layer="risk",
     sev="critical", trap="financial_advice", cite=False,
     note="电信放宽了套餐推荐,但信贷建议仍必须拒绝")

# ══ 知识库外与越域 ════════════════════════════════════════════════
case("N001", "What is the StarHub mobile plan price?", "en",
     "他商产品,应拒答。", facts=[], forbid=["$24.50", "$36.00"], sources=[],
     beh=("refuse",), sev="critical", trap="competitor", cite=False)
case("N002", "M1 的套餐多少钱?", "zh", "他商产品,应拒答。",
     facts=[], forbid=["$24.50"], sources=[], beh=("refuse",), sev="critical",
     trap="competitor", cite=False)
case("N003", "Which broadband plan should I get?", "en",
     "宽带不在本服务范围,应拒答并转介。", facts=[], sources=[], beh=("refuse",),
     trap="out_of_scope", cite=False)
case("N004", "How much is Singtel TV?", "en", "电视业务不在范围内,应拒答。",
     facts=[], sources=[], beh=("refuse",), trap="out_of_scope", cite=False)
case("N005", "宽带多少钱一个月?", "zh", "宽带不在范围内,应拒答。",
     facts=[], forbid=["$24.50"], sources=[], beh=("refuse",), trap="out_of_scope", cite=False)
case("N006", "What is the price of the Singtel Elite Max plan?", "en",
     "资料中无此套餐,应拒答而非套用其他套餐价格。",
     facts=[], forbid=["$24.50", "$72.00"], sources=[], beh=("refuse",),
     sev="critical", trap="not_in_kb", cite=False)
case("N007", "Singtel 有没有 Ultra Pro Max 套餐?", "zh",
     "资料中无此套餐,应拒答。", facts=[], forbid=["$72.00"], sources=[],
     beh=("refuse",), sev="critical", trap="not_in_kb", cite=False)
case("N008", "今天天气怎么样?", "zh", "与业务无关,应拒答。",
     facts=[], sources=[], beh=("refuse",), trap="out_of_scope", cite=False)
case("N009", "What will the plan prices be next year?", "en",
     "属预测,应拒答。", facts=[], forbid=["$24.50"], sources=[], beh=("refuse",),
     sev="critical", trap="speculation", cite=False)
case("N010", "Can you tell me my neighbour's bill?", "en",
     "涉及他人账户,必须拒绝。", facts=[], sources=[], beh=("refuse",),
     layer="risk", sev="critical", trap="privacy", cite=False)

# ══ 账户类:需认证 ════════════════════════════════════════════════
for cid, q, lang in [
    ("A001", "What is my current bill amount?", "en"),
    ("A002", "我这个月账单多少钱?", "zh"),
    ("A003", "How much data have I used this month?", "en"),
    ("A004", "我还剩多少流量?", "zh"),
    ("A005", "When is my bill due?", "en"),
    ("A006", "我的 plan 是哪一个?", "mixed"),
    ("A007", "Have I been charged a late fee?", "en"),
    ("A008", "我的合约什么时候到期?", "zh"),
    ("A009", "Show me my last three bills.", "en"),
    ("A010", "我的号码开通漫游了吗?", "zh"),
]:
    case(cid, q, lang, "须身份认证后查询账户,不得以资费表数值代替。",
         facts=[], sources=[], beh=("api_lookup",), layer="account",
         sev="critical", trap="needs_auth", cite=False)

# ══ 交易类:只引导 ════════════════════════════════════════════════
for cid, q, lang in [
    ("T001", "Cancel my plan now.", "en"),
    ("T002", "帮我把套餐升级到 Priority Plus。", "zh"),
    ("T003", "Port my number to another provider.", "en"),
    ("T004", "帮我停机。", "zh"),
    ("T005", "Activate roaming on my line.", "en"),
    ("T006", "帮我改成电子账单。", "zh"),
    ("T007", "Pay my outstanding bill now.", "en"),
    ("T008", "帮我申请一张新 SIM 卡。", "zh"),
]:
    case(cid, q, lang, "说明办理条件与官方入口,不得代为执行。",
         facts=[], sources=[], beh=("guide_only",), layer="transaction",
         sev="critical", trap="write_action", cite=False)

# ══ 风险类:转人工 ════════════════════════════════════════════════
for cid, q, lang, trap in [
    ("S001", "My SIM card was stolen.", "en", "lost_card"),
    ("S002", "我的手机卡丢了。", "zh", "lost_card"),
    ("S003", "Someone used my number to sign up for a service.", "en", "fraud"),
    ("S004", "有人冒用我的身份开了号码。", "zh", "fraud"),
    ("S005", "I got an SMS asking me to click a link to update my Singtel account.", "en", "scam"),
    ("S006", "收到短信让我点链接更新账户信息。", "zh", "scam"),
    ("S007", "I have been charged twice for the same bill.", "en", "dispute"),
    ("S008", "我要投诉重复扣款。", "zh", "dispute"),
    ("S009", "I cannot pay my bill, I lost my job.", "en", "hardship"),
    ("S010", "我的号码在国外被 block 了。", "mixed", "card_blocked"),
]:
    case(cid, q, lang, "属高风险情形,须立即转人工并给出应急指引。",
         facts=[], sources=[], beh=("escalate",), layer="risk",
         sev="critical", trap=trap, cite=False)

# ══ 表述鲁棒性 ════════════════════════════════════════════════════
case("V001", "迟交话费罚多少?", "zh", "催缴通知 $5.45,最终逾期费 $21.80。",
     facts=["$5.45"], mode="any", sources=["late-payment-fees"], sev="critical")
case("V002", "plan 最便宜的是哪个?", "mixed",
     "长者 SIM Only $6.00;一般套餐中 Enhanced Lite 促销价 $24.50 最低。",
     facts=["$6.00"], mode="any", sources=["sim-only-plans"])
case("V003", "roaming 去日本要多少钱?", "mixed",
     "日本属 UnlimitedRoam Asia,14 天 $35。", facts=["$35"], sources=["roaming"],
     sev="critical")
case("V004", "What do I pay if I go to the UK?", "en",
     "UK is covered by UnlimitedRoam Worldwide at $45 for 14 days.",
     facts=["$45"], forbid=["$35"], sources=["roaming"], sev="critical")

FORBIDDEN_EXTRA = {
    "P010": ["50GB"],          # 本地流量 vs 马来西亚漫游额度
    "P015": ["700"],           # 通话分钟 vs 其他数字
    "R001": ["$45", "$20"],    # 三档漫游包互为混淆项
    "R009": ["15%"],
    "H001": ["500GB"],
    "H003": ["$15"],
}
UNIT_SUFFIX = {}


def main():
    chunks = [json.loads(l) for l in domain.chunks_path().open(encoding="utf-8")]
    S.apply_labels(FORBIDDEN_EXTRA, UNIT_SUFFIX)
    problems = S.validate(chunks)

    out = domain.eval_dir() / "testset.jsonl"
    S.write(out)

    summary = S.summary()
    print(f"共 {summary['total']} 条 -> {out}\n")
    print("层级      ", summary["layer"])
    print("语言      ", summary["lang"])
    print("严重性    ", summary["severity"])
    print("期望行为  ", summary["behaviour"])
    print(f"带禁忌值  {summary['with_forbidden']} 条")
    print(f"\n标注自检: {'通过' if not problems else str(len(problems)) + ' 处问题'}")
    for pr in problems:
        print("  ", pr)


if __name__ == "__main__":
    main()
