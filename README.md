# Layered Support Assistant

One customer-support pipeline, two industries, opposite operating points.
Everything runs on an 8GB M1 laptop: product documents come from the providers'
public websites, customer records are synthetic, the model is local. Questions
can be asked in English, Chinese, or a mix of both.

**Banking** optimises for never stating a wrong figure about money, and pays for
it in handoffs. **Telco** cannot afford those handoffs, because its questions
repeat and the cost of an error is lower. Same code, different configuration,
measured at both ends rather than argued about.

## The problem it was built around

A major Singapore bank publishes two credit card agreements that contradict each
other on three figures, and the stale one carries no date anywhere:

| Document | Date stated | Finance charge | Cash advance fee |
|---|---|---|---|
| `dbs-card-agreement.pdf` | none | 25.90% p.a. | 6% |
| `dbs-agreement-2019.pdf` | 30 June 2026 | **27.8% p.a.** | **8%** |

Retrieval scores them 0.096 apart, so ranking cannot separate them. The hard
problem in banking RAG is not recall — it is deciding which of two retrieved
figures to trust, and being able to show why afterwards.

## Results

Same pipeline, same local Qwen2.5-3B, two labelled sets:

| | Banking (146 cases) | Telco (92 cases) |
|---|---|---|
| Confidently wrong on a critical fact | **0** | **0** |
| Answers factually correct | 79% | 89% |
| Model answers carrying a citation | 100% | 100% |
| Escalated to a human | 18 answerable questions | 16% of all questions |
| Requests never reaching a model | 47% | **76%** |

Telco scores better on facts not because the system is better there, but because
that industry allows most facts to be moved out of the model altogether: plan
pricing is structured product data, while a bank's terms are legal text that has
to be quoted. Three quarters of telco questions are answered by routing, the
account record or the product table, and never reach a model at all. Where a
domain can be structured, it should be.

### Caution against containment

The trade-off, measured by running identical code at four settings:

| Setting | Handled without a human | Escalated | Critical errors | Answerable but escalated |
|---|---|---|---|---|
| Banking settings | 47% | 38% | 0 | 16 |
| Softer refusal only | 57% | 28% | 1 | 10 |
| Telco settings | 72% | 17% | 1 | 3 |
| All guardrails off | 73% | 17% | 1 | 3 |

Two things fall out of this. Banking's settings applied to telco escalate 38% of
questions, 16 of them answerable from the sources, which is not a viable service.
And turning every guardrail off buys one percentage point: figure verification
and conflict escalation cost almost no containment, because what they withhold
was not going to become an answer anyway.

[`docs/design.md`](docs/design.md) explains the reasoning; the day-by-day log,
including the approaches that were built, measured and abandoned, is in
[`docs/findings.md`](docs/findings.md).

## How it works

```
question
   │
   ├─ Routing & entity check ──────── risk · advice · write action · account · not-in-scope
   │      (rules, no model)                        └─ ends here for 47% of requests
   │
   ├─ Product table ──────────────── plan pricing and fees answered from fields
   │      (telco only)
   │
   ├─ Retrieval ──────────────────── BM25 + glossary + multilingual dense, scope filtered
   │
   ├─ Conflict adjudication ──────── stale figures withheld before generation
   │      (rules, no model)
   │
   ├─ Local model ────────────────── answers only from the surviving sources
   │
   └─ Figure grounding check ─────── every amount and percentage traced to a source
```

Judgements about money are made in code, not in prompts, so each one can be
tested and cited in an audit. The model is the last and least trusted component.

## What porting to a second industry cost

361 of 2,489 lines of `src`, about 15%, most of it path plumbing and one new
parser. What genuinely differs between the two industries turned out to be small
and specific, and now lives in `domains/*.json`: the product catalogue,
competitors, out-of-scope products, transaction scopes, the Chinese-English
glossary, the nouns that mean "this customer's own record", and the rule for
what counts as advice the assistant must not give.

That last one is the sharpest difference. A bank must not tell a customer
whether to take a cash advance; a telco recommending a plan is doing its job.
The two test sets encode that as opposite expectations for the same shape of
question.

## Run

Requires [uv](https://docs.astral.sh/uv/) and Apple Silicon (the local model uses MLX).

```bash
uv sync
uv run python scripts/fetch_sources.py    # download the provider's public documents
uv run python src/ingest.py               # parse them into retrievable chunks
uv run python app.py                      # demo UI at http://127.0.0.1:7860
```

Set `ASSISTANT_DOMAIN=telco` on any of those to work on the telco side; the
default is `bank`. The telco build has one extra step, which turns the plan
comparison grid into fields:

```bash
ASSISTANT_DOMAIN=telco uv run python scripts/extract_plans.py
```

Source documents are fetched rather than committed — they are the providers'
published material, and a checked-in copy would go stale silently. The first
start also downloads Qwen2.5-3B-Instruct-4bit (~1.7GB) and multilingual-e5-small.

## Evaluate

```bash
uv run python eval/build_testset.py          # bank set: regenerate and validate
uv run python eval/run_retrieval_eval.py     # retrieval layer only, no model
uv run python eval/run_e2e_eval.py           # full pipeline, ~15 minutes

ASSISTANT_DOMAIN=telco uv run python eval/build_telco_testset.py
ASSISTANT_DOMAIN=telco uv run python eval/run_policy_sweep.py   # the trade-off curve
```

## Layout

| Path | Role |
|---|---|
| `src/domain.py` `domains/*.json` | What differs between industries, and nothing else |
| `src/router.py` | Rule-based routing: risk, advice, out-of-scope, transactions, account |
| `src/retrieve.py` `src/dense.py` `src/query.py` | BM25 + glossary + multilingual dense, scope filtering |
| `src/conflict.py` | Detects contradictory figures across documents and withholds stale ones |
| `src/plans.py` `scripts/extract_plans.py` | Product table: plan facts and recommendations, no model |
| `src/accounts.py` | Synthetic accounts, template-rendered answers |
| `src/verify.py` `src/extract.py` | Figure grounding check; extractive reference on refusal |
| `src/pipeline.py` | Orchestration, policy settings, per-layer trace |
| `src/llm.py` | Local MLX / hosted backends behind one interface |
| `app.py` | Demo UI showing each answer's path |
| `eval/` | Two labelled sets, shared authoring kit, retrieval / end-to-end / policy sweep |

## Licence

MIT, covering the code, evaluation sets and documentation. The providers'
published documents are downloaded at run time, are not redistributed here, and
are not covered by it. This project is not affiliated with DBS Bank or Singtel.
