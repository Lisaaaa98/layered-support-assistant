# DBS Credit Card Assistant — on-device, layered

A customer-support assistant for DBS credit cards that runs entirely on an 8GB
M1 laptop. Product terms are DBS's own published documents; customer accounts
are synthetic. Questions can be asked in English, Chinese, or a mix of both.

**The design is built around one finding.** DBS publishes two credit card
agreements that contradict each other on three figures, and the stale one
carries no date anywhere:

| Document | Date stated | Finance charge | Cash advance fee |
|---|---|---|---|
| `dbs-card-agreement.pdf` | none | 25.90% p.a. | 6% |
| `dbs-agreement-2019.pdf` | 30 June 2026 | **27.8% p.a.** | **8%** |

Retrieval scores them 0.096 apart, so ranking cannot separate them. The hard
problem in banking RAG is not recall — it is deciding which of two retrieved
figures to trust, and being able to show why afterwards.

## Results

146 labelled cases, local Qwen2.5-3B:

| Metric | Result |
|---|---|
| **Confidently wrong on a critical fact** | **0** |
| Answers carrying a citation | 54/54 = 100% |
| Answers factually correct | 31/39 = 79% |
| Behaviour matches expectation | 118/146 = 81% |
| Answerable but handed to an agent | 18 |
| Requests never reaching a model | 69/146 = 47% |

The system never stated a known-wrong figure on a critical fact. The cost is 18
answerable questions escalated instead. That refusal rate is too high for
production, and the cause is the 3B model rather than retrieval or
architecture — the backend interface supports a larger model, which this machine
cannot host and which therefore remains unverified.

[`docs/design.md`](docs/design.md) explains every decision behind those numbers,
including two guardrails that were built, measured, and abandoned, and three
bugs found in the evaluation tooling itself.

## How it works

```
question
   │
   ├─ Routing & entity check ──────── risk · advice · write action · account · not-in-scope
   │      (rules, no model)                        └─ ends here for 47% of requests
   │
   ├─ Retrieval ──────────────────── BM25 + glossary + multilingual dense, filtered by card
   │
   ├─ Conflict adjudication ──────── stale figures withheld before generation
   │      (rules, no model)
   │
   ├─ Local model ────────────────── answers only from the surviving sources
   │
   └─ Figure grounding check ─────── every amount and percentage traced to a source
```

Judgements about money are made in code, not in prompts: conflict adjudication,
routing, refusal and figure checking are all deterministic, so each one can be
tested and cited in an audit. The model is the last and least trusted component.

## Run

Requires [uv](https://docs.astral.sh/uv/) and Apple Silicon (the local model uses MLX).

```bash
uv sync
uv run python scripts/fetch_sources.py   # download DBS's published documents
uv run python src/ingest.py              # parse them into retrievable chunks
uv run python app.py                     # demo UI at http://127.0.0.1:7860
```

The demo shows each answer beside the path that produced it: which layers ran,
what each decided, and which retrieved chunks were withheld and why. A toggle
runs the same model with no sources alongside, which is the clearest way to see
what grounding is doing.

Source documents are fetched rather than committed — they are DBS's own
published material, and a checked-in copy would go stale silently. The first
start also downloads Qwen2.5-3B-Instruct-4bit (~1.7GB) and
multilingual-e5-small, then builds the vector index.

Because the sources are live, promotional copy on the card pages does change
between fetches. The figures that matter — fees, rates, thresholds — have been
stable, but re-run the evaluation after fetching if you intend to rely on the
numbers.

## Evaluate

```bash
uv run python eval/build_testset.py        # regenerate and validate the 146-case set
uv run python eval/run_retrieval_eval.py   # retrieval layer only, no model
uv run python eval/run_e2e_eval.py         # full pipeline, ~15 minutes on M1
```

## Layout

| Path | Role |
|---|---|
| `src/router.py` | Rule-based routing: risk, advice, out-of-scope, transactions, account |
| `src/retrieve.py` `src/dense.py` `src/query.py` | BM25 + glossary + multilingual dense, card-scope filtering |
| `src/conflict.py` | Detects contradictory figures across documents and withholds stale ones |
| `src/pipeline.py` | Orchestration and the per-layer trace the UI renders |
| `src/verify.py` `src/extract.py` | Figure grounding check; extractive reference on refusal |
| `src/accounts.py` | Synthetic accounts, template-rendered answers |
| `src/llm.py` | Local MLX / hosted backends behind one interface |
| `app.py` | Gradio UI showing each answer's path |
| `eval/` | Test set, matching, retrieval and end-to-end evaluation |
| `scripts/fetch_sources.py` | Downloads the public source documents |

## Licence

MIT, covering the code, evaluation set and documentation. DBS's published
documents are downloaded at run time, are not redistributed here, and are not
covered by it. This project is not affiliated with DBS Bank.

## Documentation

- [`docs/design.md`](docs/design.md) — the reasoning: problem, architecture,
  measurements, failures, limitations.
- [`docs/findings.md`](docs/findings.md) — the day-by-day working log, in
  Chinese, with the dead ends in more detail.
