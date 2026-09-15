"""Demo UI: every answer shown together with the path that produced it.

The point of the interface is not the chat box. It is the column beside it,
which shows which layer handled a question and what each layer decided: that
a fraud report never reached a model, that a stale 25.90% was withheld before
generation, that a figure was checked back to its source. A layered system is
only credible if the layers can be seen.

Run:  uv run python app.py   ->  http://127.0.0.1:7860
"""
import html
import json
import os
import sys
from pathlib import Path

import gradio as gr

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import domain  # noqa: E402
from accounts import generate  # noqa: E402
from llm import get_backend  # noqa: E402
from pipeline import answer, answer_without_retrieval  # noqa: E402
from retrieve import HybridRetriever  # noqa: E402

RETRIEVER = HybridRetriever()
BACKEND = get_backend("local")
CUSTOMER = generate()["C100000"]

UI = domain.config().get("ui", {})


def _has_product_table():
    try:
        import plans

        return plans.available()
    except ImportError:
        return False


HAS_PRODUCT_TABLE = _has_product_table()

STAGES = [
    ("router", "Routing & entity check",
     "Rules decide the layer; risk, transactions, account and out-of-scope questions stop here"),
    ("accounts", "Account record",
     "Rendered from templates once authenticated, never through the model"),
    ("product table", "Product table",
     "Plan pricing, allowances and fees answered from parsed fields, never through the model"),
    ("retrieval", "Retrieval",
     "BM25 + glossary + multilingual dense, filtered to the product in question"),
    ("conflict", "Conflict adjudication",
     "When sources disagree on a figure, decide by currency and authority; withhold the stale one"),
    ("model", "Local model", "Qwen2.5-3B · MLX · answers only from the sources given"),
    ("fallback", "Refusal fallback",
     "When the model declines, the clause it found is attached to the handoff"),
    ("verifier", "Figure grounding check",
     "Every amount and percentage stated must appear in the sources"),
]

ACTION_LABEL = {
    "answer": ("Answered", "ok"),
    "escalate": ("Handed to an agent", "warn"),
    "refuse": ("Declined", "muted"),
    "api_lookup": ("Account lookup", "info"),
    "guide_only": ("Guidance only", "info"),
}

# Internal codes stay in the pipeline, where they are stable and testable; the
# page translates them, so a reader sees "recency of the document" rather than
# an identifier.
RULE_LABEL = {"recency_confidence": "recency of the document",
              "authority": "document authority",
              "unanimous": "sources agree",
              "no_discriminator": "nothing to decide on"}
LAYER_LABEL = {"knowledge": "knowledge", "account": "account",
               "transaction": "transaction", "risk": "risk"}
SCOPE_LABEL = {"general": "general", "cash_advance": "cash advance",
               "foreign_currency": "foreign currency",
               "sgd_processed_outside": "SGD processed offshore",
               "balance_transfer": "balance transfer", "instalment": "instalment"}
RISK_LABEL = {"fraud": "fraud", "lost_card": "lost or stolen card", "scam": "scam",
              "dispute": "dispute or complaint", "hardship": "payment hardship",
              "regulatory": "regulator mentioned", "card_blocked": "card blocked"}
REASON_LABEL = {
    "general product question": "general product question",
    "financial advice requested": "personal financial advice requested",
    "third-party account data": "someone else's account",
    "another bank's product": "another bank's product",
    "outside credit cards": "outside credit cards",
    "card not in knowledge base": "card not in the knowledge base",
    "write action requested": "asked to perform an action",
    "customer-specific data": "needs this customer's records",
    "served from account record": "served from the account record",
    "account field not covered": "account field not covered",
    "retrieval miss": "nothing retrieved",
    "model declined; reference attached": "model declined; clause attached",
    "model declined": "model declined",
}
CONFIDENCE_LABEL = {"stated": "states its own date", "inferred": "dated by retrieval",
                    "unknown": "no date stated"}


def phrase(reason):
    reason = reason or ""
    if reason.startswith("risk signal: "):
        kind = reason.split(": ", 1)[1]
        return f"risk signal: {RISK_LABEL.get(kind, kind)}"
    if reason.startswith("unverifiable figure: "):
        return "figure not traceable to a source: " + reason.split(": ", 1)[1]
    return REASON_LABEL.get(reason, reason)


def _latest_rows():
    """Rows from whichever evaluation this domain has run.

    The bank domain has an end-to-end run; telco was measured with the policy
    sweep, which stores the same per-case rows under the profile it used.
    """
    e2e = domain.eval_dir() / "e2e_results.json"
    if e2e.exists():
        return json.loads(e2e.read_text(encoding="utf-8")), None
    sweeps = sorted(domain.eval_dir().glob("policy_sweep*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    for path in sweeps:
        detail = json.loads(path.read_text(encoding="utf-8")).get("detail", {})
        for profile in ("telco", "banking"):
            if detail.get(profile):
                return detail[profile], profile
    return None, None


def load_metrics():
    """Headline numbers read from the latest evaluation run, never typed in."""
    rows, profile = _latest_rows()
    if not rows:
        return None
    answered = [r for r in rows if r["answered"]]
    scoreable = [r for r in answered if r["facts_ok"] is not None]
    # Layers that answer without a model: routing, the account record, the
    # product table, and a retrieval miss that ends in a refusal.
    without_model = ("router", "retriever", "accounts", "plans")
    no_model = sum(1 for r in rows if r.get("backend", "") in without_model)
    return {
        "profile": profile,
        "cases": len(rows),
        "critical_wrong": sum(1 for r in rows
                              if r["confidently_wrong"] and r["severity"] == "critical"),
        "all_wrong": sum(1 for r in rows if r["confidently_wrong"]),
        "facts": f"{sum(1 for r in scoreable if r['facts_ok'])}/{len(scoreable)}",
        # Citations are a requirement on answers the model composed from
        # sources. A figure rendered from the product table cites the field it
        # came from, not a passage, so counting it here would understate a
        # requirement it was never under.
        "cited": _cited(answered),
        "no_model": round(100 * no_model / len(rows)),
        "refused": sum(1 for r in rows if r.get("wrongly_refused")
                       or r.get("avoidable_handoff")),
    }


def _cited(answered):
    composed = [r for r in answered if r.get("backend") in (None, "local", "cloud", "extractive")]
    if not composed or not any("cited" in r for r in composed):
        return "—"
    return f"{sum(1 for r in composed if r.get('cited'))}/{len(composed)}"


def esc(text):
    return html.escape(str(text)).replace("\n", "<br>")


def metrics_html():
    m = load_metrics()
    if not m:
        return "<div class='strip'>No evaluation yet — run eval/run_e2e_eval.py</div>"
    tiles = [
        (m["critical_wrong"], "confidently wrong, critical", "headline metric, target 0"),
        (m["facts"], "answers factually right", "of answers with labelled facts"),
        (m["cited"], "model answers carrying a citation", "figures from fields cite the field"),
        (f"{m['no_model']}%", "requests never reached a model",
         "handled by routing or retrieval"),
        (m["refused"], "refused despite having evidence", "counter-metric; handed to an agent"),
    ]
    cells = "".join(
        f"<div class='tile'><div class='num'>{esc(v)}</div>"
        f"<div class='lbl'>{esc(l)}</div><div class='sub'>{esc(s)}</div></div>"
        for v, l, s in tiles)
    return (f"<div class='strip'>{cells}</div>"
            f"<div class='foot'>{m['cases']} evaluation cases · local 3B model · "
            f"{m['all_wrong']} confidently wrong overall"
            + (f" · policy profile: {m['profile']}" if m.get("profile") else "")
            + " · figures read from the evaluation output, not typed in</div>")


def stage_body(step, suppressed):
    stage = step["stage"]
    detail = phrase(step["detail"]) if stage == "router" else step.get("detail")
    parts = [f"<div class='detail'>{esc(detail)}</div>"] if detail else []

    if stage == "router":
        bits = [f"layer <b>{esc(LAYER_LABEL.get(step['layer'], step['layer']))}</b>"]
        if step.get("cards"):
            bits.append(f"card identified <b>{esc(', '.join(step['cards']))}</b>")
        if step.get("blocked_entity"):
            bits.append(f"not covered <b>{esc(step['blocked_entity'])}</b>")
        parts.append(f"<div class='kv'>{' · '.join(bits)}</div>")

    if stage == "retrieval":
        rows = []
        for c in step.get("chunks", []):
            gone = c["chunk_id"] in suppressed
            # An undated document would otherwise read "no date stated · not stated".
            conf = (CONFIDENCE_LABEL[c["confidence"]] if c["confidence"] == "unknown"
                    else f"{CONFIDENCE_LABEL[c['confidence']]} · {c['effective']}")
            flag = (f"<span class='pill bad'>withheld · stale value "
                    f"{esc(suppressed[c['chunk_id']])}</span>" if gone else "")
            rows.append(
                f"<div class='chunk{' gone' if gone else ''}'>"
                f"<div class='ctitle'>{esc(c['title'])} "
                f"<span class='sec'>{esc(c['section'][:48])}</span></div>"
                f"<div class='cmeta'><span class='pill {c['confidence']}'>{esc(conf)}"
                f"</span>{flag}</div></div>")
        parts.append("".join(rows) or "<div class='detail'>nothing retrieved</div>")

    if stage == "conflict":
        for c in step.get("conflicts", []):
            verdict = (f"kept <b>{esc(c['winner'])}</b> · on {esc(RULE_LABEL.get(c['rule'], c['rule']))}"
                       if c["resolved"] else "cannot decide → handed to an agent")
            scope = ", ".join(SCOPE_LABEL.get(x, x) for x in c["scope"])
            parts.append(f"<div class='kv'>{esc(' / '.join(c['values']))} "
                         f"<span class='sec'>{esc(scope)}</span> → {verdict}</div>")

    if stage == "model" and step.get("raw"):
        parts.append(f"<pre class='raw'>{esc(step['raw'][:600])}</pre>")

    return "".join(parts)


def trace_html(a):
    reached = {s["stage"]: s for s in a.trace}
    suppressed = next((s.get("suppressed", {}) for s in a.trace if s["stage"] == "conflict"), {})
    last = a.trace[-1]["stage"] if a.trace else None

    # Show only the branches that belong to this request's path, but keep
    # unreached main-line stages visible and dimmed: how much of the pipeline
    # a question skipped is itself the thing being demonstrated.
    visible = []
    for key, name, blurb in STAGES:
        if key == "accounts" and key not in reached:
            continue
        if key == "fallback" and key not in reached:
            continue
        if key == "verifier" and "fallback" in reached:
            continue
        # The product table only exists where the domain has one, and the
        # layers after it are not reached when it answers.
        if key == "product table" and not (HAS_PRODUCT_TABLE or key in reached):
            continue
        if key in ("retrieval", "conflict", "model", "verifier") and "accounts" in reached:
            continue
        if key in ("retrieval", "conflict", "model", "verifier") and "product table" in reached:
            continue
        visible.append((key, name, blurb))

    items = []
    for key, name, blurb in visible:
        step = reached.get(key)
        state = "end" if key == last else ("done" if step else "skip")
        outcome = esc(step["outcome"]) if step else "not reached"
        body = stage_body(step, suppressed) if step else ""
        items.append(
            f"<li class='stage {state}'><div class='dot'></div><div class='sbody'>"
            f"<div class='shead'><span class='sname'>{esc(name)}</span>"
            f"<span class='sout'>{outcome}</span>"
            f"{'<span class=pill end>stopped here</span>' if state == 'end' else ''}</div>"
            f"<div class='blurb'>{esc(blurb)}</div>{body}</div></li>")
    return f"<ol class='path'>{''.join(items)}</ol>"


def answer_html(a, control=None):
    label, tone = ACTION_LABEL.get(a.action, (a.action, "muted"))
    timing = f" · model {a.seconds}s" if a.seconds else " · no model call"
    card = (f"<div class='ans {tone}'><div class='ahead'><span class='pill {tone}'>{label}</span>"
            f"<span class='why'>{esc(phrase(a.reason))}{timing}</span></div>"
            f"<div class='atext'>{esc(a.text)}</div></div>")
    if control is not None:
        card += (f"<div class='ans control'><div class='ahead'>"
                 f"<span class='pill bad'>control · no retrieval</span>"
                 f"<span class='why'>same model, no sources · {control.seconds}s</span></div>"
                 f"<div class='atext'>{esc(control.text)}</div></div>")
    return card


def run(question, authenticated, with_control):
    question = (question or "").strip()
    if not question:
        return "<div class='hint'>Ask something, or pick an example on the left</div>", ""
    a = answer(question, RETRIEVER, BACKEND, customer=CUSTOMER if authenticated else None)
    control = answer_without_retrieval(question, BACKEND) if with_control else None
    return answer_html(a, control), trace_html(a)


CSS = """
.gradio-container{max-width:1180px!important}
.hero h1{font-size:22px;margin:0 0 4px;font-weight:650}
.hero p{margin:0;color:var(--body-text-color-subdued);font-size:14px;line-height:1.55}
.strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-top:14px}
.tile{border:1px solid var(--border-color-primary);border-radius:10px;padding:10px 12px;background:var(--block-background-fill)}
.tile .num{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .lbl{font-size:12.5px;margin-top:2px;line-height:1.35}
.tile .sub{font-size:11px;color:var(--body-text-color-subdued)}
.foot{font-size:11.5px;color:var(--body-text-color-subdued);margin-top:6px}
.hint{color:var(--body-text-color-subdued);padding:24px 4px}
.ans{border:1px solid var(--border-color-primary);border-left-width:4px;border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--block-background-fill)}
.ans.ok{border-left-color:#2f9e6b}.ans.warn{border-left-color:#d08a1f}.ans.info{border-left-color:#3b7dd8}
.ans.muted{border-left-color:#8a8f98}.ans.control{border-left-color:#c94a4a}
.ahead{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.why{font-size:12px;color:var(--body-text-color-subdued)}
.atext{font-size:15px;line-height:1.6}
.pill{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.pill.ok{color:#2f9e6b}.pill.warn{color:#c07c12}.pill.info{color:#3b7dd8}.pill.muted{color:#8a8f98}
.pill.bad{color:#c94a4a}.pill.end{color:#c07c12;margin-left:auto}
.pill.stated{color:#2f9e6b}.pill.inferred{color:#3b7dd8}.pill.unknown{color:#c94a4a}
.path{list-style:none;margin:0;padding:0}
.stage{display:flex;gap:12px;position:relative;padding-bottom:14px}
.stage:not(:last-child):before{content:"";position:absolute;left:6px;top:16px;bottom:0;width:2px;background:var(--border-color-primary)}
.dot{flex:0 0 14px;height:14px;border-radius:50%;margin-top:3px;border:2px solid var(--border-color-primary);background:var(--background-fill-primary)}
.stage.done .dot{background:#3b7dd8;border-color:#3b7dd8}
.stage.end .dot{background:#d08a1f;border-color:#d08a1f}
.stage.skip{opacity:.42}
.sbody{flex:1;min-width:0}
.shead{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.sname{font-weight:600;font-size:14px}
.sout{font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--body-text-color-subdued)}
.blurb{font-size:12px;color:var(--body-text-color-subdued);margin:1px 0 4px;line-height:1.45}
.detail,.kv{font-size:13px;margin:3px 0}
.sec{font-size:11.5px;color:var(--body-text-color-subdued)}
.chunk{border:1px solid var(--border-color-primary);border-radius:8px;padding:6px 9px;margin:5px 0}
.chunk.gone{border-color:#c94a4a;background:rgba(201,74,74,.06)}
.chunk.gone .ctitle{text-decoration:line-through;text-decoration-color:#c94a4a}
.ctitle{font-size:12.5px;overflow-wrap:anywhere}
.cmeta{display:flex;gap:6px;flex-wrap:wrap;margin-top:3px}
.raw{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--background-fill-secondary);border-radius:6px;padding:7px 9px;margin:4px 0 0}
@media (max-width:760px){.strip{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""

# Examples live in the domain config, so switching industries switches the
# demo without touching this file.
EXAMPLES = UI.get("examples", {})


with gr.Blocks(title=UI.get("title", "Layered Support Assistant")) as demo:
    gr.HTML(
        f"<div class='hero'><h1>{esc(UI.get('title', 'Layered support assistant'))}</h1>"
        f"<p>{esc(UI.get('blurb', ''))}</p></div>")
    gr.HTML(metrics_html())

    with gr.Row(equal_height=False):
        with gr.Column(scale=4, min_width=320):
            question = gr.Textbox(label="Customer question",
                                  placeholder="English, Chinese, or both in one sentence",
                                  lines=2)
            with gr.Row():
                authenticated = gr.Checkbox(
                    label=UI.get("customer_label", "Authenticated (test customer {cid})")
                    .format(cid=CUSTOMER.customer_id), value=False)
                with_control = gr.Checkbox(label="Also show the no-retrieval control", value=False)
            send = gr.Button("Ask", variant="primary")
            for group, items in EXAMPLES.items():
                gr.Examples(examples=[[q] for q in items], inputs=[question], label=group)
        with gr.Column(scale=6, min_width=360):
            answer_out = gr.HTML("<div class='hint'>Ask something, or pick an example "
                                 "on the left</div>")
            trace_out = gr.HTML()

    send.click(run, [question, authenticated, with_control], [answer_out, trace_out])
    question.submit(run, [question, authenticated, with_control], [answer_out, trace_out])

if __name__ == "__main__":
    # Port is configurable so the two domains can run side by side.
    demo.launch(server_name="127.0.0.1",
                server_port=int(os.environ.get("ASSISTANT_PORT", 7860)), css=CSS)
