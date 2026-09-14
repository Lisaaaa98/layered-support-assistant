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

# Products the knowledge base actually covers.
KNOWN_CARDS = {"altitude": "DBS Altitude Card",
               "yuu": "DBS yuu Card",
               "vantage": "DBS Vantage Visa Infinite Card"}

OTHER_BANKS = r"\b(ocbc|uob|citibank|citi|standard chartered|maybank|hsbc|trust bank|gxs)\b"

NON_CARD_PRODUCTS = (r"home loan|mortgage|savings account|fixed deposit|时间存款|定期存款|"
                     r"insurance|unit trust|investment account|房贷|房屋贷款|储蓄账户|保险|理财")

# "DBS <something> card" / "<something> 卡" where <something> names a product.
CARD_PATTERNS = [
    re.compile(r"\bdbs\s+([a-z][a-z'\s]{1,24}?)\s+(?:card|cards)\b", re.I),
    re.compile(r"\bdbs\s+([a-z][a-z'\s]{1,24}?)\s*卡"),
    re.compile(r"([A-Za-z][A-Za-z'\s]{1,24}?)\s*卡(?:的|片)?"),
]

# Asking what the rules say about an incident is a policy question, not an
# incident report. "If my card is stolen, how much am I liable for?" must be
# answered from the agreement; "My card was stolen" must reach a human.
POLICY_QUESTION = (r"\bliab|\bresponsib|how much am i|what happens if|under what circumstances|"
                   r"责任|承担|上限|条款|规定|会怎样|有什么后果")
HYPOTHETICAL = r"^\s*if\b|\bif\s+(?:my|i|the|you)\b|如果|假如|万一|要是"

# Words that reveal a greedy card-name match swallowed a clause rather than a
# product name.
NOT_A_CARD_NAME = {"the", "of", "to", "and", "or", "for", "change", "terms", "use",
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

# Asking what one *should* do is advice, regardless of politeness.
ADVICE_PATTERNS = (r"should i\b|worth (?:paying|it)\b|which card should\b|better (?:to|option)\b|"
                   r"help me improve my credit|maximise my (?:investment|return)|"
                   r"该不该|应不应该|值不值得|划算|建议我|你觉得我|帮我提高.{0,6}(?:信用|评分)")

# An instruction, not a question: the verb leads the sentence. "How do I
# cancel my card" asks for a procedure and stays a knowledge question; "Cancel
# my card now" asks the assistant to act, and it must not.
IMPERATIVE_ACTION = (r"^\s*(please\s+)?(increase|decrease|lower|raise|cancel|close|terminate|waive|"
                     r"apply|redeem|change|update|set up|setup|pay|convert|block|freeze|activate)\b")
TRANSACTION_PATTERNS = (r"帮我|替我|给我(?:办|申请|调|改|停)|请(?:帮|为)我|"
                        r"我要(?:申请|注销|取消|调整|办|停|改)|把我的[\w\s]{0,10}(改|调|停|取消)")

# "my" and the noun are often separated ("my current outstanding balance"),
# so allow a short gap rather than requiring adjacency.
ACCOUNT_PATTERNS = (r"\bmy\b[\w\s]{0,24}?\b(balance|statement|bill|due date|payment|limit|points|account|transactions?|expiry|annual fee)\b"
                    r"|how much (?:do|have) i\b|am i eligible\b|did i\b|have i\b"
                    r"|show me my|do i have\b|was i charged|have i been charged|am i (?:charged|due)"
                    r"|我的[\w\s]{0,12}?(账单|余额|额度|积分|账户|消费|还款|欠款|limit|balance|statement|points|payment)"
                    r"|我(?:这个月|上个月|本期|这次)|我还有多少|我刷了多少|我的年费(?:扣|收)"
                    r"|我的卡[\w\s]{0,8}(到期|有效期|额度)|我这次[\w\s]{0,14}(due|到期|账单)")


@dataclass
class Route:
    layer: str
    action: str
    reason: str
    cards: list = field(default_factory=list)
    blocked_entity: str = None


def find_cards(text):
    """Card names mentioned, split into known and unknown."""
    known, unknown = [], []
    low = text.lower()
    for key in KNOWN_CARDS:
        if key in low:
            known.append(key)
    for pattern in CARD_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip().lower()
            name = re.sub(r"^(the|a|an|my|this)\s+", "", name)
            if not name or name in KNOWN_CARDS or name in {"credit", "debit", "the", "my"}:
                continue
            if any(k in name for k in KNOWN_CARDS):
                continue
            # A real product name is one or two distinctive words; anything
            # containing function words came from a greedy match over a clause.
            words = name.split()
            if len(words) > 3 or any(w in NOT_A_CARD_NAME for w in words):
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

    # 2. Advice is refused even when the customer is only asking an opinion.
    if re.search(ADVICE_PATTERNS, low):
        return Route("risk", "refuse", "financial advice requested")

    # 3. Third-party or third-bank data.
    if re.search(r"my (?:husband|wife|mother|father|son|daughter|friend|neighbour|neighbor)|"
                 r"我(?:先生|太太|老公|老婆|妈妈|爸爸|儿子|女儿|朋友)", low):
        return Route("risk", "refuse", "third-party account data")

    other = re.search(OTHER_BANKS, low)
    if other:
        return Route("knowledge", "refuse", "another bank's product",
                     blocked_entity=other.group(0))

    non_card = re.search(NON_CARD_PRODUCTS, low)
    if non_card:
        return Route("knowledge", "refuse", "outside credit cards",
                     blocked_entity=non_card.group(0))

    # 4. A card the knowledge base does not cover. Caught here rather than
    #    left to the model, which was observed answering a Live Fresh question
    #    with the yuu card's fee.
    known, unknown = find_cards(text)
    if unknown:
        return Route("knowledge", "refuse", "card not in knowledge base",
                     cards=known, blocked_entity=unknown[0])

    # 5. Actions the assistant must never perform on the customer's behalf.
    if re.search(IMPERATIVE_ACTION, low) or re.search(TRANSACTION_PATTERNS, low):
        return Route("transaction", "guide_only", "write action requested", cards=known)

    # 6. Anything about *this customer's* records needs authentication, unless
    #    the customer is asking what the rules would do rather than what their
    #    account currently says.
    if re.search(ACCOUNT_PATTERNS, low) and not asks_policy:
        return Route("account", "api_lookup", "customer-specific data", cards=known)

    return Route("knowledge", "answer", "general product question", cards=known)
