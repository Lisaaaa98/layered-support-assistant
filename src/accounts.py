"""Simulated account data and template-rendered answers.

Two deliberate choices.

The data is synthetic while the knowledge base is real. Product terms are
public and can be verified by anyone reading the answer; customer records are
not, and putting real ones anywhere near this project would be indefensible.
The combination is also what a bank would actually build against in a
non-production environment.

Answers here are rendered from templates, never generated. The figures are
retrievable exactly, so passing them through a language model can only
introduce error: a restated balance can be rounded, reformatted or simply
wrong, and there is no upside to compensate for that risk.
"""
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

CARD_PRODUCTS = {
    "altitude": ("DBS Altitude Card", 196.20),
    "yuu": ("DBS yuu Card", 196.20),
    "vantage": ("DBS Vantage Visa Infinite Card", 599.50),
}

MERCHANTS = ["FairPrice", "Grab", "Shopee", "Cold Storage", "SP Group",
             "Singtel", "Din Tai Fung", "Uniqlo", "Watsons", "Shell"]


@dataclass
class Card:
    product: str
    name: str
    last4: str
    credit_limit: float
    statement_balance: float
    current_balance: float
    minimum_payment: float
    due_date: date
    statement_date: date
    points: int
    annual_fee: float
    expiry: str
    late_fee_charged: bool
    transactions: list = field(default_factory=list)


@dataclass
class Customer:
    customer_id: str
    name: str
    cards: list


def _round2(x):
    return round(x + 1e-9, 2)


def generate(seed=42, n=200, today=None):
    """Deterministic synthetic portfolio, so evaluation runs are reproducible."""
    rng = random.Random(seed)
    today = today or date(2026, 9, 9)
    customers = {}
    for i in range(n):
        cid = f"C{100000 + i}"
        held = rng.sample(list(CARD_PRODUCTS), rng.choice([1, 1, 2]))
        cards = []
        for product in held:
            name, fee = CARD_PRODUCTS[product]
            limit = rng.choice([5000, 8000, 10000, 15000, 20000, 30000])
            statement = _round2(rng.uniform(0, limit * 0.6))
            stmt_date = today - timedelta(days=rng.randint(1, 28))
            # Minimum payment mirrors the published rule: 3% of the statement
            # balance or S$50, whichever is greater.
            minimum = _round2(max(statement * 0.03, 50)) if statement > 0 else 0.0
            # Transactions fall inside the closed statement period and never
            # after today, which a naive offset from the statement date does.
            span = max((today - stmt_date).days, 1)
            txns = [{
                "date": (stmt_date + timedelta(days=rng.randint(0, span))).isoformat(),
                "merchant": rng.choice(MERCHANTS),
                "amount": _round2(rng.uniform(5, 400)),
            } for _ in range(rng.randint(4, 12))]
            txns.sort(key=lambda t: t["date"], reverse=True)
            cards.append(Card(
                product=product, name=name, last4=f"{rng.randint(1000, 9999)}",
                credit_limit=float(limit), statement_balance=statement,
                current_balance=_round2(statement + rng.uniform(0, 500)),
                minimum_payment=minimum,
                due_date=stmt_date + timedelta(days=21),
                statement_date=stmt_date,
                points=rng.randint(0, 60000), annual_fee=fee,
                expiry=f"{rng.randint(1,12):02d}/{rng.choice([27,28,29])}",
                late_fee_charged=rng.random() < 0.12,
                transactions=txns))
        customers[cid] = Customer(cid, f"Customer {i:03d}", cards)
    return customers


# Intent -> the field that answers it. Kept explicit so an account question
# can never be served by a value that merely looks related.
FIELD_PATTERNS = [
    ("statement_balance", r"outstanding|statement balance|账单(?:金额|余额)?|欠(?:了)?多少"),
    ("minimum_payment", r"minimum payment|minimum (?:amount|due)|最低还款"),
    ("due_date", r"due date|when is my payment|payment due|还款日|到期日|哪天"),
    ("credit_limit", r"credit limit|额度"),
    ("points", r"points|积分|里程"),
    ("expiry", r"expiry|expire|有效期|到期"),
    ("transactions", r"transactions|spent|last \d+|消费|刷了"),
    ("late_fee_charged", r"late fee|滞纳金|逾期费"),
    ("annual_fee", r"annual fee|年费"),
]

TEMPLATES_EN = {
    "statement_balance": "Your {name} (ending {last4}) has a statement balance of S${statement_balance:,.2f}. The minimum payment is S${minimum_payment:,.2f}, due {due_date}.",
    "minimum_payment": "The minimum payment on your {name} (ending {last4}) is S${minimum_payment:,.2f}, due {due_date}.",
    "due_date": "Your {name} (ending {last4}) is due on {due_date}. The statement was issued on {statement_date}.",
    "credit_limit": "Your {name} (ending {last4}) has a credit limit of S${credit_limit:,.2f}, of which S${current_balance:,.2f} is currently used.",
    "points": "Your {name} (ending {last4}) currently has {points:,} DBS Points.",
    "expiry": "Your {name} (ending {last4}) expires {expiry}.",
    "annual_fee": "The annual fee on your {name} (ending {last4}) is S${annual_fee:,.2f}.",
}

TEMPLATES = {
    "statement_balance": "你的{name}(尾号 {last4})本期账单金额为 S${statement_balance:,.2f},最低还款额 S${minimum_payment:,.2f},还款日 {due_date}。",
    "minimum_payment": "你的{name}(尾号 {last4})本期最低还款额为 S${minimum_payment:,.2f},还款日 {due_date}。",
    "due_date": "你的{name}(尾号 {last4})本期还款日为 {due_date},账单日为 {statement_date}。",
    "credit_limit": "你的{name}(尾号 {last4})信用额度为 S${credit_limit:,.2f},当前已使用 S${current_balance:,.2f}。",
    "points": "你的{name}(尾号 {last4})当前积分为 {points:,} DBS Points。",
    "expiry": "你的{name}(尾号 {last4})有效期至 {expiry}。",
    "annual_fee": "你的{name}(尾号 {last4})年费为 S${annual_fee:,.2f}。",
}


def _lang_of(text):
    """Reply in the language the customer wrote in."""
    from query import has_han

    return "zh" if has_han(text) else "en"


def lookup(customer, question, card_hint=None):
    """Resolve an account question against one customer's records.

    Returns None when no field matches, so the caller escalates rather than
    improvising an answer.
    """
    import re

    card = None
    if card_hint:
        card = next((c for c in customer.cards if c.product in card_hint), None)
    card = card or customer.cards[0]

    low = question.lower()
    field_name = next((f for f, pat in FIELD_PATTERNS if re.search(pat, low)), None)
    if field_name is None:
        return None

    data = {"name": card.name, "last4": card.last4,
            "statement_balance": card.statement_balance,
            "minimum_payment": card.minimum_payment,
            "credit_limit": card.credit_limit,
            "current_balance": card.current_balance,
            "points": card.points, "annual_fee": card.annual_fee,
            "expiry": card.expiry,
            "due_date": card.due_date.isoformat(),
            "statement_date": card.statement_date.isoformat()}

    lang = _lang_of(question)

    if field_name == "transactions":
        lines = [f"  {t['date']}  {t['merchant']:<14} S${t['amount']:,.2f}"
                 for t in card.transactions[:5]]
        head = (f"你的{card.name}(尾号 {card.last4})最近 5 笔交易:" if lang == "zh"
                else f"The last 5 transactions on your {card.name} (ending {card.last4}):")
        return head + "\n" + "\n".join(lines)

    if field_name == "late_fee_charged":
        due = card.due_date.isoformat()
        if card.late_fee_charged:
            return (f"你的{card.name}(尾号 {card.last4})本期收取了 S$100 滞纳金,"
                    f"因未在还款日 {due} 前收到最低还款。" if lang == "zh" else
                    f"A S$100 late payment charge was applied to your {card.name} "
                    f"(ending {card.last4}) because the minimum payment was not "
                    f"received by {due}.")
        return (f"你的{card.name}(尾号 {card.last4})本期未收取滞纳金。" if lang == "zh"
                else f"No late payment charge was applied to your {card.name} "
                     f"(ending {card.last4}) this cycle.")

    template = (TEMPLATES if lang == "zh" else TEMPLATES_EN).get(field_name)
    return template.format(**data) if template else None
