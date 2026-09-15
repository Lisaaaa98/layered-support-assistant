"""End-to-end answering: retrieve, adjudicate, ground, generate.

The model is the last and least trusted component. Everything upstream exists
to ensure that by the time it is called, the context contains exactly one
value for any fact it might quote, and nothing it must not see.
"""
from dataclasses import dataclass, field, replace  # noqa: F401

from conflict import resolve_context
from router import route
from extract import extractive_answer
from verify import verify_figures

SYSTEM = """You are a DBS credit card support assistant in Singapore.

Rules you must follow:
1. Answer ONLY from the numbered sources provided. Never use prior knowledge \
about banks, cards, rates or fees.
2. Every figure you state must appear verbatim in the sources. Never round, \
convert, recalculate or estimate a number.
3. Cite the source number for each fact, like [2].
4. If the sources do not contain the answer, reply exactly: \
INSUFFICIENT_CONTEXT
5. Reply in the language the customer used. Keep figures in their original \
currency and format.
6. Never give financial, investment or borrowing advice.

Format: write the answer as a complete sentence that contains the figure, then put the source number in brackets at the end. A bare citation with no sentence is not an acceptable answer.

Good: The replacement card fee is S$123.45 [1].
Bad: [1]

Before deciding you cannot answer, check each source title for the product the customer named. Reply INSUFFICIENT_CONTEXT only when no source covers it.
A strict refusal rule makes a small model refuse questions it can answer, so the check comes first and the refusal second."""

NO_CONTEXT_REPLY = "INSUFFICIENT_CONTEXT"

# The softer variant drops the hard refusal instruction. On a 3B model the
# strict wording produced refusals on questions the sources answered, which is
# the right trade where a wrong figure costs a customer money and the wrong one
# where the same behaviour would send one in eight answerable questions to an
# agent.
SOFT_REFUSAL_CLAUSE = """4. If the sources genuinely do not cover the question, reply \
INSUFFICIENT_CONTEXT. Prefer answering from the sources where they do cover it."""

STRICT_REFUSAL_CLAUSE = """4. If the sources do not contain the answer, reply exactly: \
INSUFFICIENT_CONTEXT"""


@dataclass(frozen=True)
class Policy:
    """How cautious to be. The pipeline is the same in every industry; this is
    what differs, and making it a parameter is what lets the difference be
    measured instead of argued about."""

    name: str = "banking"
    min_score: float = 1.0
    dense_floor: float = 0.82
    strict_refusal: bool = True
    # Promote a quoted passage from reference material to an answer.
    promote_fallback: bool = False
    verify_figures: bool = True
    escalate_unresolved_conflict: bool = True


BANKING = Policy()
TELCO = Policy(name="telco", min_score=0.6, dense_floor=0.78,
               strict_refusal=False, promote_fallback=True)


def system_prompt(policy):
    clause = STRICT_REFUSAL_CLAUSE if policy.strict_refusal else SOFT_REFUSAL_CLAUSE
    return SYSTEM.replace(STRICT_REFUSAL_CLAUSE, clause)


@dataclass
class Answer:
    text: str
    sources: list = field(default_factory=list)
    suppressed: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    retrieved: int = 0
    escalate: bool = False
    seconds: float = 0.0
    backend: str = ""
    route: str = ""
    action: str = ""
    reason: str = ""
    cards: list = field(default_factory=list)
    trace: list = field(default_factory=list)


def format_context(chunks):
    blocks = []
    for i, c in enumerate(chunks, 1):
        date = c["effective_date"] or "date not stated"
        blocks.append(f"[{i}] {c.get('doc_title', c['doc_id'])} (effective {date})\n{c['text']}")
    return "\n\n".join(blocks)


REFUSAL_TEMPLATES = {
    "another provider's product": "抱歉,我只能回答本服务范围内的产品问题。",
    "out of scope for this service": "抱歉,这不在本服务的范围内,需要其他部门协助。",
    "card not in knowledge base": "抱歉,我的资料中没有这个产品的信息,请查阅官网或转接人工。",
    "financial advice requested": "抱歉,我无法提供个人财务建议。我可以说明相关费用与条款,具体决定建议咨询持牌人员。",
    "third-party account data": "抱歉,我无法提供他人账户的信息。",
}

HANDOFF = {
    "escalate": "这属于需要人工处理的情况,正在为你转接客服专员。",
    "api_lookup": "这需要查询你的账户,请先完成身份验证。",
    "guide_only": "这项操作需要通过 DBS 官方渠道办理,我可以说明所需条件和入口,但无法代为执行。",
}

# Fixed replies follow the customer's language, as account answers already do.
# A customer who writes in English and is told in Chinese that they are being
# transferred has been failed by the one message that had no model in it.
REFUSAL_TEMPLATES_EN = {
    "another provider's product": "Sorry, I can only help with products from this provider.",
    "out of scope for this service": "Sorry, that is outside what this service covers. Another team can help.",
    "card not in knowledge base": "Sorry, I don't have information on that product. Please check the website or ask to speak to an agent.",
    "financial advice requested": "Sorry, I can't give personal financial advice. I can explain the fees and terms involved; for the decision itself, please speak to a licensed representative.",
    "third-party account data": "Sorry, I can't share information about someone else's account.",
}

HANDOFF_EN = {
    "escalate": "This needs a member of our team. I'm transferring you to an agent now.",
    "api_lookup": "I'll need to look up your account. Please verify your identity first.",
    "guide_only": "This has to be done through an official DBS channel. I can explain what's needed and where to go, but I can't do it for you.",
}

REFERENCE_PREFIX = {"zh": "以下为检索到的相关条款原文,未经核对,仅供参考:",
                    "en": "Relevant terms found, not verified, for reference only:"}
BLOCKED_NOTE = {"zh": "(未覆盖: {})", "en": " (not covered: {})"}


def _lang(text):
    from query import has_han

    return "zh" if has_han(text) else "en"


def _handoff(action, lang):
    return (HANDOFF if lang == "zh" else HANDOFF_EN)[action]


def _refusal(reason, lang):
    table = REFUSAL_TEMPLATES if lang == "zh" else REFUSAL_TEMPLATES_EN
    default = "抱歉,我无法回答这个问题。" if lang == "zh" else "Sorry, I can't answer that."
    return table.get(reason, default)


def _from_product_table(question):
    """Structured answer, when the active domain has a product table."""
    try:
        import plans
    except ImportError:
        return None
    if not plans.available():
        return None
    return plans.answer(question)


def _step(trace, stage, outcome, detail="", **data):
    """Record one layer's decision. The trace is what makes the pipeline
    explainable after the fact: which layer ended the request, and why."""
    trace.append({"stage": stage, "outcome": outcome, "detail": detail, **data})


def _chunk_view(c):
    return {"chunk_id": c["chunk_id"], "title": c.get("doc_title", c["doc_id"]),
            "effective": c["effective_date"] or "not stated",
            "confidence": c["date_confidence"], "section": c["section"],
            "preview": c["text"][:220]}


def answer(question, retriever, backend, k=6, max_tokens=350, customer=None,
           policy=BANKING):
    """Answer one question.

    `customer` stands in for an authenticated session. Without it, account
    questions stop at the authentication prompt; with it, they are served from
    templates rather than the model, so no figure about someone's money is
    ever restated by a probabilistic system.

    Every layer appends to `Answer.trace`, including the layer that ended the
    request. Behaviour is unchanged by tracing; it only records it.
    """
    trace = []
    lang = _lang(question)

    # Routing runs before retrieval so that risk, write actions, account
    # lookups and out-of-scope questions never reach the model at all. That
    # is a deliberate reversal: an earlier design left refusal to a prompt
    # rule, and the model was observed answering a question about a card it
    # had no source for using a different card's fee.
    decision = route(question)
    _step(trace, "router", decision.action, decision.reason,
          layer=decision.layer, cards=decision.cards,
          blocked_entity=decision.blocked_entity)

    def done(**kw):
        kw.setdefault("route", decision.layer)
        kw.setdefault("reason", decision.reason)
        return Answer(trace=trace, **kw)

    if decision.action == "api_lookup" and customer is not None:
        from accounts import lookup

        rendered = lookup(customer, question, card_hint=decision.cards)
        if rendered is None:
            # A recognised account intent with no matching field is a gap in
            # coverage, not licence to guess.
            _step(trace, "accounts", "no field", "account intent recognised but no matching field; handed to an agent")
            return done(text=_handoff("escalate", lang), route="account", action="escalate",
                        reason="account field not covered", escalate=True, backend="router")
        _step(trace, "accounts", "rendered", f"customer {customer.customer_id} · rendered from templates, no model involved")
        return done(text=rendered, route="account", action="api_lookup",
                    reason="served from account record", backend="accounts",
                    cards=decision.cards)

    if decision.action in HANDOFF:
        return done(text=_handoff(decision.action, lang), action=decision.action,
                    escalate=decision.action == "escalate", backend="router")
    if decision.action == "refuse":
        text = _refusal(decision.reason, lang)
        if decision.blocked_entity:
            text += BLOCKED_NOTE[lang].format(decision.blocked_entity)
        return done(text=text, action="refuse", backend="router")

    # A figure that can be looked up exactly should not be restated by a
    # probabilistic system. The account layer already works this way; plan data
    # qualifies once parsed into fields, and the measurements said it should be:
    # the model reads the flattened comparison grid correctly about half the
    # time, and its mistakes are wrong prices and wrong denials.
    served = _from_product_table(question)
    if served:
        _step(trace, "product table", "answered", "rendered from parsed fields; no model call")
        return done(text=served, action="answer", backend="plans",
                    reason="served from the product table", cards=decision.cards)

    search_kw = {"cards": decision.cards, "min_score": policy.min_score}
    if hasattr(retriever, "dense"):
        search_kw["dense_floor"] = policy.dense_floor
    hits = retriever.search(question, k=k, **search_kw)
    _step(trace, "retrieval", f"{len(hits)} chunks",
          f"card scope: {', '.join(decision.cards) if decision.cards else 'none (general question)'}",
          chunks=[_chunk_view(h) for h in hits])

    resolved = resolve_context(hits)
    context = resolved["context"]
    _step(trace, "conflict", f"{len(resolved['conflicts'])} conflicts",
          f"{len(resolved['suppressed'])} chunk withheld" if len(resolved['suppressed']) == 1
          else f"{len(resolved['suppressed'])} chunks withheld",
          suppressed=resolved["suppressed"],
          conflicts=[{"values": c["values"], "scope": c["scope"],
                      "winner": c["verdict"].get("winner_value"),
                      "rule": c["verdict"].get("rule"),
                      "resolved": c["verdict"]["resolved"]}
                     for c in resolved["conflicts"]])

    # Refusing on an empty context is handled here rather than by the model:
    # a model given no sources still tends to produce a fluent answer.
    if not context:
        return done(text=NO_CONTEXT_REPLY, action="refuse", reason="retrieval miss",
                    escalate=resolved["escalate"], backend="retriever")

    user = (f"Customer question:\n{question}\n\n"
            f"Sources:\n{format_context(context)}")
    reply = backend.complete(system_prompt(policy), user, max_tokens=max_tokens)
    _step(trace, "model", reply.backend, f"{reply.model} · {reply.seconds}s · {reply.tokens} tokens",
          raw=reply.text)

    common = dict(sources=[c["chunk_id"] for c in context], retrieved=len(context),
                  suppressed=resolved["suppressed"], conflicts=resolved["conflicts"],
                  seconds=reply.seconds, cards=decision.cards)

    # The model declines 18 of 146 answerable questions, sometimes while naming
    # the source it says it lacks. Retrieved evidence exists in most of those
    # cases, so rather than discard it, the handoff carries it.
    #
    # It is attached as reference material, not as an answer, and that
    # distinction is the whole point. Promoting the extracted passage to an
    # answer was measured at 7 right and 7 wrong out of 18, including a case
    # where a 60-year-old's income threshold was quoted from the under-55 row.
    # One such answer would end the run of zero confidently-wrong responses,
    # which is the guarantee this system is built to make. Handing a human the
    # clause alongside the handoff keeps the evidence useful without asserting
    # anything, and mirrors what an agent does when unsure: quote the terms and
    # escalate.
    if NO_CONTEXT_REPLY in reply.text:
        reference = extractive_answer(question, context)
        _step(trace, "fallback", "reference" if reference else "none",
              "model declined; clause attached to the handoff" if reference else "model declined; no quotable passage")
        if reference and policy.promote_fallback:
            # Quoting the clause as the answer instead of attaching it to a
            # handoff. Measured at 7 right out of 14 on the bank set, which is
            # why banking leaves this off.
            return done(text=reference, action="answer", backend="extractive",
                        reason="model declined; quoted passage", **common)
        text = _handoff("escalate", lang)
        if reference:
            text += f"\n{REFERENCE_PREFIX[lang]}\n{reference}"
        return done(text=text, action="escalate", escalate=True, backend="extractive",
                    reason="model declined; reference attached" if reference
                    else "model declined", **common)

    # Last gate: every amount and percentage stated must exist in the sources
    # the model was shown. A fabricated figure fails here even when the answer
    # is otherwise well-formed and correctly cited.
    unsupported = verify_figures(reply.text, context) if policy.verify_figures else []
    _step(trace, "verifier", "blocked" if unsupported else "passed",
          "figures with no source: " + ", ".join(u["text"] for u in unsupported)
          if unsupported else "every amount and percentage traces back to a source")
    if unsupported:
        return done(text=_handoff("escalate", lang), action="escalate", escalate=True,
                    backend="verifier", reason="unverifiable figure: "
                    + ", ".join(u["text"] for u in unsupported), **common)

    if resolved["escalate"] and policy.escalate_unresolved_conflict:
        return done(text=_handoff("escalate", lang), action="escalate", escalate=True,
                    backend="conflict", reason="unresolved conflict", **common)

    return done(text=reply.text, action="answer", escalate=False,
                backend=reply.backend, **common)


def answer_without_retrieval(question, backend, max_tokens=350):
    """Control condition: the same model, same question, no sources.

    This is the baseline the whole system is measured against, and it is not
    a straw man: it is what a bank gets by putting a chat model in front of
    customers without grounding it.
    """
    system = ("You are a DBS credit card support assistant in Singapore. "
              "Answer the customer's question directly and concisely.")
    reply = backend.complete(system, question, max_tokens=max_tokens)
    return Answer(text=reply.text, seconds=reply.seconds, backend=reply.backend)
