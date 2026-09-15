"""Intent routing and entity checking, ahead of retrieval.

Deliberately rule-based. Two reasons, both from measurement rather than
preference: a 3B model's routing decisions were not reproducible across
trivial prompt rewordings, and a bank has to be able to explain why a given
customer message was escalated. Rules can be read, tested and cited in an
audit; a prompt cannot.

Ordering matters more than coverage. Risk is checked first because a message
can carry several signals at once ("我的卡被盗刷了" is both an account query
and a fraud report) and the costly mistake is to treat fraud as a balance
lookup, never the reverse.
"""
import re
from dataclasses import dataclass, field

import domain

# Products the knowledge base actually covers.
# Product names, competitors and out-of-scope products come from the active
# domain's config; the routing logic below is industry-independent.
def known_products():
    return domain.known_products()


def competitors():
    return domain.config()["competitors"]


def out_of_scope_products():
    return domain.config()["out_of_scope_products"]

def product_patterns():
    return domain.product_patterns()


# Asking what the rules say about an incident is a policy question, not an
# incident report. "If my card is stolen, how much am I liable for?" must be
# answered from the terms; "My card was stolen" must reach a human.
POLICY_QUESTION = (r"\bliab|\bresponsib|how much am i|what happens if|under what circumstances|"
                   r"责任|承担|上限|条款|规定|会怎样|有什么后果")
HYPOTHETICAL = r"^\s*if\b|\bif\s+(?:my|i|the|you)\b|如果|假如|万一|要是"

# Words that reveal a greedy product-name match swallowed a clause rather than
# a product name. Used when the domain config does not supply its own list.
NOT_A_PRODUCT_NAME_FALLBACK = {"the", "of", "to", "and", "or", "for", "change", "terms", "use",
                               "my", "a", "an", "this", "that", "your", "our", "credit", "debit",
                               "new", "any", "all", "same", "other", "another"}

RISK_PATTERNS = [
    ("fraud", r"did not make|didn'?t make|unauthoris|unauthoriz|fraud|hacked|identity theft|"
              r"someone (?:used|opened|applied)|盗刷|盗用|被盗|冒用|冒名|我没有(?:刷|消费)|"
              r"有人(?:用|拿)(?:我|了我)"),
    ("lost_card", r"\blost\b|\bstolen\b|丢了|遗失|挂失|被偷"),
    ("scam", r"scam|phishing|asked for my (?:otp|cvv|pin|password)|click(?:ed)? (?:on )?(?:a|the|this) link|"
             r"verify my account|诈骗|骗子|钓鱼|要我的\s*(otp|cvv|验证码|密码)|"
             r"可疑(?:的)?(?:短信|电话|链接|邮件)|(?:短信|邮件|电话).{0,20}(?:链接|点击|更新)|"
             r"让我点|叫我点"),
    ("dispute", r"complain|complaint|dispute|charged twice|overcharg|refus(?:e|es|ed|ing) to refund|"
                r"charged for .{0,30}(?:cancel|did not|didn'?t)|reverse the charge|chargeback|"
                r"投诉|重复(?:扣款|收费)|多扣|争议|退款|不给退"),
    ("hardship", r"cannot pay|can'?t pay|lost my job|还不上|还不起|失业|没钱还"),
    ("regulatory", r"\bmas\b|monetary authority|金管局|监管"),
    ("card_blocked", r"\bblocked?\b|\bfrozen\b|被\s*(?:block|冻结|封|停)|刷不了|用不了|不能用"),
]

def advice_patterns():
    """What counts as advice the assistant must not give.

    This is the one risk rule that genuinely differs between industries rather
    than merely being worded differently. A bank must not tell a customer
    whether to take a cash advance. A telco recommending a plan is doing its
    job, so the same pattern that protects the first would cripple the second.
    """
    return domain.config()["advice_patterns"]


# An instruction, not a question: the verb leads the sentence. "How do I
# cancel my plan" asks for a procedure and stays a knowledge question;
# "Cancel my plan now" asks the assistant to act, and it must not.
IMPERATIVE_ACTION = (r"^\s*(please\s+)?(increase|decrease|lower|raise|cancel|close|terminate|waive|"
                     r"apply|redeem|change|update|set up|setup|pay|convert|block|freeze|activate|"
                     r"suspend|port|switch|upgrade|downgrade)\b")
TRANSACTION_PATTERNS = (r"帮我|替我|给我(?:办|申请|调|改|停)|请(?:帮|为)我|"
                        r"我要(?:申请|注销|取消|调整|办|停|改)|把我的[\w\s]{0,10}(改|调|停|取消)")

# "my" and the noun are often separated ("my current outstanding balance"),
# so allow a short gap rather than requiring adjacency.
# Which nouns mean "this customer's own record" is domain-specific: a bank
# customer asks about a balance, a telco customer about data left and contract
# end date. The sentence shapes around them are not.
def account_patterns():
    cfg = domain.config()
    return (r"\bmy\b[\w\s]{0,24}?\b(" + cfg["account_nouns_en"] + r")\b"
            r"|how much (?:do|have) i\b|am i eligible\b|did i\b|have i\b"
            r"|show me my|do i have\b|was i charged|have i been charged|am i (?:charged|due)"
            r"|我的[\w\s]{0,12}?(" + cfg["account_nouns_zh"] + r")"
            r"|我(?:这个月|上个月|本期|这次)|我还有多少|我还剩多少|我刷了多少|我用了多少"
            r"|我的年费(?:扣|收)|我的卡[\w\s]{0,8}(到期|有效期|额度)|我这次[\w\s]{0,14}(due|到期|账单)")


# Asking how to look something up is a procedure question, answerable from
# public documentation. Asking what the value is needs the customer's record.
PROCEDURAL = (r"^\s*how (?:do|can) i\b|^\s*how to\b|\bwhere (?:do|can) i (?:check|see|find)\b"
              r"|怎么(?:查|看|下载|设置|开通|取消)|如何(?:查|看|申请|办理)|在哪(?:里)?(?:查|看)")


@dataclass
class Route:
    layer: str
    action: str
    reason: str
    cards: list = field(default_factory=list)
    blocked_entity: str = None


def not_a_product_name():
    return set(domain.config().get("not_a_product_name") or NOT_A_PRODUCT_NAME_FALLBACK)


def find_cards(text):
    """Card names mentioned, split into known and unknown."""
    known, unknown = [], []
    low = text.lower()
    catalogue = known_products()
    for key in catalogue:
        if key in low:
            known.append(key)
    for pattern in product_patterns():
        for match in pattern.finditer(text):
            name = match.group(1).strip().lower()
            name = re.sub(r"^(the|a|an|my|this)\s+", "", name)
            if not name or name in catalogue or name in {"credit", "debit", "the", "my"}:
                continue
            if any(k in name for k in catalogue):
                continue
            # A real product name is one or two distinctive words; anything
            # containing function words came from a greedy match over a clause.
            words = name.split()
            if len(words) > 3 or any(w in not_a_product_name() for w in words):
                continue
            unknown.append(name)
    return known, unknown


def route(question):
    """Decide how a message is handled before any retrieval happens."""
    text = question.strip()
    low = text.lower()

    # 1. Risk first, and generously. A false escalation costs an agent's time;
    #    a missed fraud report costs the customer money. The one exception is a
    #    question *about* the rules, which carries the same vocabulary without
    #    reporting anything.
    asks_policy = re.search(POLICY_QUESTION, low) or re.search(HYPOTHETICAL, low)
    for kind, pattern in RISK_PATTERNS:
        if re.search(pattern, low):
            if asks_policy and kind in {"fraud", "lost_card", "card_blocked", "hardship"}:
                break
            return Route("risk", "escalate", f"risk signal: {kind}")

    # 2. Third-party account data.
    if re.search(r"my (?:husband|wife|mother|father|son|daughter|friend|neighbour|neighbor)|"
                 r"我(?:先生|太太|老公|老婆|妈妈|爸爸|儿子|女儿|朋友)", low):
        return Route("risk", "refuse", "third-party account data")

    other = re.search(competitors(), low)
    if other:
        return Route("knowledge", "refuse", "another provider's product",
                     blocked_entity=other.group(0))

    non_card = re.search(out_of_scope_products(), low)
    if non_card:
        return Route("knowledge", "refuse", "out of scope for this service",
                     blocked_entity=non_card.group(0))

    # Advice is checked after scope, so a question about a product this
    # service does not cover is declined for that reason rather than
    # mislabelled as a request for advice.
    # 2. Advice is refused even when the customer is only asking an opinion.
    if re.search(advice_patterns(), low):
        return Route("risk", "refuse", "financial advice requested")



    # 4. A card the knowledge base does not cover. Caught here rather than
    #    left to the model, which was observed answering a Live Fresh question
    #    with the yuu card's fee.
    known, unknown = find_cards(text)
    if unknown:
        return Route("knowledge", "refuse", "card not in knowledge base",
                     cards=known, blocked_entity=unknown[0])

    # 5. Actions the assistant must never perform on the customer's behalf.
    if (re.search(IMPERATIVE_ACTION, low) or re.search(TRANSACTION_PATTERNS, low)) \
            and not re.search(PROCEDURAL, low):
        return Route("transaction", "guide_only", "write action requested", cards=known)

    # 6. Anything about *this customer's* records needs authentication, unless
    #    the customer is asking what the rules would do rather than what their
    #    account currently says.
    if re.search(account_patterns(), low) and not asks_policy and not re.search(PROCEDURAL, low):
        return Route("account", "api_lookup", "customer-specific data", cards=known)

    return Route("knowledge", "answer", "general product question", cards=known)
