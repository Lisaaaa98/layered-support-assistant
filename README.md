# DBS Credit Card Assistant — on-device, layered

A customer-support assistant for DBS credit cards that runs entirely on a laptop
(M1, 8GB). Product terms come from DBS's public website; customer accounts are
synthetic. Design notes, measurements and the reasoning behind each decision are
in [`docs/findings.md`](docs/findings.md).

## Run

Requires [uv](https://docs.astral.sh/uv/) and Apple Silicon (the local model uses MLX).

```bash
uv sync
uv run python scripts/fetch_sources.py   # download DBS's published documents
uv run python src/ingest.py              # parse them into retrievable chunks
uv run python app.py                     # demo UI at http://127.0.0.1:7860
```

The source documents are fetched rather than committed: they are DBS's own
published material, and a checked-in copy would quietly go stale. The first
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
| `src/conflict.py` | Detects contradictory figures across documents and suppresses stale ones |
| `src/pipeline.py` | Orchestration, per-layer trace |
| `src/verify.py` `src/extract.py` | Figure grounding check; extractive reference on refusal |
| `src/accounts.py` | Synthetic accounts, template-rendered answers |
| `src/llm.py` | Local MLX / hosted backends behind one interface |
| `app.py` | Gradio UI showing each answer's path |
| `eval/` | Test set, matching, retrieval and end-to-end evaluation |
| `scripts/fetch_sources.py` | Downloads the public source documents |
