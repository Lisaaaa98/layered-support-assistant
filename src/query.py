"""Query-side handling for a bilingual Singapore customer base.

Customers here mix languages freely, and the three forms fail differently:
  "annual fee waiver"        - lexical match works
  "我的 annual fee 可以 waive 吗"  - code-switched, the English terms carry it
  "年费可以豁免吗"             - all-Chinese, lexical match scores exactly zero

The knowledge base is English-only, so Chinese terms are expanded to their
English equivalents before retrieval. Banking vocabulary is a closed set of a
few dozen terms, which makes a glossary both sufficient for the common path
and, unlike a translation model, deterministic and auditable. Dense retrieval
covers the long tail of phrasing the glossary cannot anticipate.
"""
import re

# Chinese term -> the wording actually used in the DBS documents.
GLOSSARY = {
    "年费": "annual fee",
    "免年费": "annual fee waiver",
    "年费豁免": "annual fee waiver",
    "豁免": "waiver waive",
    "滞纳金": "late payment charge",
    "迟交": "late payment overdue",
    "晚交": "late payment overdue",
    "罚款": "charge penalty fee",
    "罚": "charge fee",
    "补办": "replacement card lost",
    "丢了": "lost card",
    "被偷": "stolen card",
    "逾期费": "late payment charge",
    "逾期": "late payment overdue",
    "利率": "interest rate",
    "财务费用": "finance charge",
    "现金预支": "cash advance",
    "预借现金": "cash advance",
    "取现": "cash advance withdrawal",
    "最低还款": "minimum payment",
    "还款": "payment repayment",
    "账单": "statement",
    "还款日": "payment due date",
    "到期日": "payment due date",
    "信用额度": "credit limit",
    "额度": "credit limit",
    "分期": "instalment payment plan",
    "积分": "DBS Points",
    "里程": "miles",
    "外币": "foreign currency",
    "外汇": "foreign currency",
    "手续费": "fee administrative fee",
    "挂失": "lost card",
    "盗刷": "unauthorised transaction",
    "补卡": "replacement card",
    "收入要求": "minimum income",
    "最低收入": "minimum income",
    "申请": "apply eligibility",
    "信用卡": "credit card",
    "附属卡": "supplementary card",
    "主卡": "principal card",
    "兑换": "conversion redeem",
    "境外": "overseas outside singapore",
}

# Longest-first so 最低还款 wins over 还款.
_TERMS = sorted(GLOSSARY, key=len, reverse=True)
_HAN = re.compile(r"[一-鿿]")


def has_han(text):
    return bool(_HAN.search(text))


def expand(query):
    """Append the English equivalents of any Chinese terms found.

    The original text is kept rather than replaced: a code-switched query
    already carries usable English tokens, and dropping them would throw away
    the strongest signal in the string.
    """
    if not has_han(query):
        return query, []
    matched, remaining = [], query
    for term in _TERMS:
        if term in remaining:
            matched.append(term)
            remaining = remaining.replace(term, " ")
    if not matched:
        return query, []
    english = " ".join(GLOSSARY[t] for t in matched)
    return f"{query} {english}", matched
