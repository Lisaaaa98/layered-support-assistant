# Design notes

A customer-support assistant for DBS credit cards, running entirely on an 8GB
M1 laptop. Product terms are DBS's published documents; customer accounts are
synthetic. This document explains what the system does, why each layer exists,
what the measurements say, and where it falls short.

The day-by-day working log, including dead ends in more detail, is in
[`findings.md`](findings.md) (written in Chinese).

---

## 1. The problem this was built around

DBS publishes two credit card agreements. They contradict each other on three
figures, and the filenames point the wrong way:

| Document | Date it states | Finance charge | Cash advance fee | Cash advance rate |
|---|---|---|---|---|
| `dbs-card-agreement.pdf` | none | **25.90% p.a.** | **6%** | **28%** |
| `dbs-agreement-2019.pdf` | Last updated 30 June 2026 | **27.8% p.a.** | **8%** | **28.5%** |

The file named `2019` is the current one. The one with the generic name is
stale and says so nowhere. The bank's own live rates page agrees with the
`2019` file, so the stale document is simply still sitting on the website.

Both are retrieved for the question "what is my interest rate". BM25 scores
them **14.508 and 14.412** — a gap of 0.096. Ranking cannot separate them, and
a top-k pipeline hands both to the model, which then has no basis for choosing.
Quoting 25.90% to a customer is not a fluency problem; it is a wrong number
about their money.

So the problem being solved is not recall. It is **deciding which of two
retrieved figures to trust, and being able to show why afterwards.**

---

## 2. Shape of the system

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
   └─ Figure grounding check ─────── every amount and percentage traced back to a source
```

Two decisions run through the whole design.

**Judgements about money are made in code, not in prompts.** Conflict
adjudication, routing, refusal, and figure checking are all deterministic. This
is not a stylistic preference: a 3B model's decisions were measured flipping on
trivial prompt rewordings, and a bank has to be able to explain why a given
message was escalated. Rules can be read, tested, and cited in an audit.

**Each layer only decides what it can decide reliably.** The model is the last
and least trusted component. By the time it is called, the context contains one
value per fact and nothing it must not see.

---

## 3. Getting the documents in

Three parsers, because the sources differ:

- **PDF agreements** — clean text layer, split on numbered clauses.
- **Static support pages** — content is in the HTML.
- **Next.js product and rates pages** — the static HTML yields 1,538 characters
  and none of the figures. The content lives in the `__NEXT_DATA__` JSON blob,
  whose string fields are themselves rich text. Parsing that JSON is faster and
  more reproducible than driving a headless browser.

Three chunking problems mattered more than the parser choice:

**Splitting by length destroys fee tables.** The first pass produced two
6,000-character chunks holding late payment, cash advance and foreign currency
amounts together — a question about one fee retrieves a block containing four
others. Splitting on the `h2`/`h3` headings inside the rich text gives one
charge per chunk; the late-payment chunk is 128 characters.

**Chunks lose their subject.** The Vantage fee block says "this card"
throughout and never contains the word "Vantage". Asked about the Vantage annual
fee, the model answered `INSUFFICIENT_CONTEXT` — which was *correct reasoning
on bad data*. Document titles are now injected into every chunk, so the subject
travels with the text.

**`<br>` inside table cells is dropped by `get_text()`**, gluing
"Supplementary Card: Free" onto the following sentence and producing
"Supplementary Card: Free 12,500 DBS Points". Converted to a separator before
extraction.

183 chunks, each carrying source URL, document authority, section, and a dated
provenance record.

---

## 4. Bilingual queries

Singapore customers mix languages, and the three forms fail differently:

| Form | Example | BM25 |
|---|---|---|
| English | annual fee waiver | works |
| Code-switched | 我的 annual fee 可以 waive 吗 | works — the English terms carry it |
| All-Chinese | 年费可以豁免吗 | **scores exactly zero** |

The all-Chinese failure was silent and total: the tokeniser matched no Han
characters, every BM25 score was 0.0, and the system returned the first three
chunks in index order — a clause about PINs — with no signal that anything had
gone wrong. The most dangerous failure mode was also the quietest one.

Three fixes, in order of how much they carry:

1. **Retrieval-miss detection.** Below a score floor, return nothing, so the
   caller can refuse instead of improvising.
2. **A glossary.** Banking vocabulary is a closed set of a few dozen terms;
   mapping them to the wording used in the documents is deterministic,
   auditable and free. This took Chinese source recall from 0% to 73%.
3. **Multilingual dense retrieval** for the phrasings a glossary cannot
   enumerate ("卡丢了怎么补办" carries no glossary term and BM25 scores it zero;
   the encoder lands it on the loss-of-card clause).

Dense retrieval earned less than expected: English recall 98% → 100%, Chinese
unchanged, and one extra leak from retrieving more material. On this corpus,
BM25 with a maintained glossary is close to the ceiling. It is kept because its
failure mode is different from BM25's, not because it moved the headline number.

---

## 5. Conflict adjudication

Not every disagreement is a contradiction, and treating them alike destroys
usability. Four cases, only two of which are real:

- **Different scope** — Vantage S$599.50 vs Altitude S$196.20. Both correct;
  the query simply lacked a card. Handled by scope filtering.
- **General vs specific** — 27.8% retail vs 28.5% cash advance. Both correct at
  different granularity. Handled by transaction-scope tagging.
- **Superseded version** — 25.90% vs 27.8%. One is wrong.
- **Genuinely inconsistent live documents** — rarest, hardest.

Detection is numeric and deterministic. Two figures describe the same fact only
if they share a unit, share a transaction scope, come from *different documents*
(two numbers inside one document are two facts — a fee table listing 1.00%
network + 2.25% bank + 3.25% total is arithmetic, not disagreement), carry the
same qualifiers (`additional`, `total`, `minimum`), and share at least two of
the content words immediately preceding the number.

Adjudication is fixed precedence, recorded every time:

1. **Date confidence** — a stated date beats an inferred one beats none.
2. **Stated date** — newer wins.
3. **Document authority** — live rates page > agreement > product page.
4. Otherwise: refuse and escalate.

Currency deliberately outranks authority: an authoritative but undated document
is the more dangerous of the two, because it looks official while being stale.

**Suppression happens before generation.** The stale chunk is removed from the
context rather than the answer being checked afterwards — the model cannot quote
a number it was never shown. A full-corpus scan also writes a conflict register
for whoever maintains the knowledge base, because runtime adjudication is a
backstop and fixing the library is the actual repair.

Across 183 chunks and 262 numeric claims: **3 conflicts, no false positives**,
scanned in 0.12s. All three trace to the same undated agreement.

---

## 6. Guardrails, including one that failed

**Figure grounding (shipped).** Every amount and percentage in an answer must
appear in the sources the model was shown. On the no-retrieval control, the
fabricated "2% of the outstanding amount or S$50" is caught. Zero false blocks
across 54 answered cases.

Its limit is honest: it proves a figure *exists*, not that it is attached to the
right fact. "10,000 DBS Points for 10,000 miles" passes, because both numbers
appear in the conversion table, even though the rate is wrong by a factor of two.

**Field pairing (built, measured, abandoned).** The attempt to close that gap
compared each figure against source claims naming the same fact. It blocked 2 of
54 answers — both correct — and still missed the error it was built for. The
reason is structural: the candidate set was assembled by the same fragile word
matching the check was meant to police. A correct S$98.10 supplementary fee was
rejected because the source phrase carried too few adjacent words to be
collected, while the principal card's S$196.20 had more and was, leaving only
the wrong value to compare against.

The function is kept in the repository with the full reason in its docstring.
**A guard cannot be built on the same signal as the thing it guards.**

---

## 7. Refusing, and what to do with the evidence

The local model declines 18 of 146 answerable questions, sometimes while naming
the source it claims not to have:

> `INSUFFICIENT_CONTEXT [1], [2], [3] both mention an 8% cash advance fee`

Retrieved evidence exists in most of those cases. Two ways to use it were
measured:

| Approach | Result |
|---|---|
| Extract *which figure* answers the question, quote that sentence | fired on 4 of 18, wrong sentence in 2 |
| Quote the most relevant passage | fired on 14 of 18, 7 right, 7 wrong |

The second is better but still unusable as an answer: one case quoted the
under-55 income threshold to a 60-year-old. Promoting it would have ended the
run of zero confidently-wrong answers, which is the one guarantee this system
makes.

So the passage is attached to the handoff as **reference material, explicitly
unverified**, rather than asserted. It does not count as answering, the evidence
is not wasted, and a mis-selected passage is visible to the reader in a way an
invented figure is not. It is what a human agent does when unsure: quote the
clause and pass it on.

---

## 8. Evaluation

146 labelled cases: 92 English, 38 Chinese, 16 code-switched; 87 knowledge, 31
risk, 16 account, 12 transaction; 110 critical, 36 normal.

Three schema decisions came out of a first pass that would have needed
relabelling:

- **Accepted behaviours are a set, not a value.** Asked an ambiguous question,
  both clarifying and enumerating all three cards are good answers. A test set
  must not score the better behaviour as failure.
- **Key facts declare all/any, and are written as the source writes them.**
  Otherwise `"100"` matches inside `"100,000"`.
- **Severity.** A wrong interest rate and a wrong restaurant discount are not
  the same failure, and 146 cases averaged together hide the ones that matter.

Because 75% of cases are critical, the headline metric is not a percentage but
a **zero-tolerance count**: confidently wrong on a critical fact must be 0.

### Results — 146 cases, local Qwen2.5-3B

| Metric | Result |
|---|---|
| Confidently wrong, critical | **0** |
| Confidently wrong, all severities | 1 |
| Answers carrying a citation | 54/54 = 100% |
| Answers factually correct | 31/39 = 79% |
| Behaviour matches expectation | 118/146 = 81% |
| Answerable but refused | 18 |
| Requests never reaching a model | 69/146 = 47% |

The claim this supports, and no more than this:

> Across 146 labelled cases, the system never stated a known-wrong figure on a
> critical fact. The cost is 18 answerable questions handed to an agent. The
> refusal rate is too high for production, and the cause is the 3B model, not
> retrieval or architecture — the backend interface supports a larger model,
> which this 8GB machine cannot host and which therefore remains unverified.

### Two things the numbers do not say on their own

**The behaviour rate fell from 94% to 81% without the system getting worse.**
Earlier, a model refusal was labelled `answer` because it had taken the
knowledge path — not answering, but scored as correct behaviour. Labelling those
honestly as escalations cost 13 points, while the system improved in the same
period (20 of them now carry the source clause). A metric can worsen with no
regression and improve with no progress; read the definition before the number.

**The headline metric has a blind spot.** It only covers the 42 cases with
labelled forbidden values. Three factual errors sit outside it — the points
conversion, a claim that the welcome gift survives reapplication, and a claim
that an offshore SGD transaction is free. Forbidden values can express a wrong
number; they cannot express a wrong statement. That is why factual accuracy is
reported alongside, and why "0 confidently wrong" is never quoted alone.

### The evaluation tooling had bugs of its own

Three, each of which scored a correct system as wrong:

1. Substring matching flagged `0.6%` in a foreign-currency clause as the stale
   `6%` cash advance fee.
2. `27.8%` and `27.80% per annum` were treated as different facts.
3. Labels were internally inconsistent — one case recorded `27.8%`, another
   `27.8`, so the same correct answer passed one and failed the other.

Fixing these moved factual accuracy from 64% to 79%. **Thirteen of those points
came from correcting labels, not from improving the system**, which is exactly
why it is stated here. The annotation validator now rejects a forbidden value
that appears in its own gold answer — it caught one on the first run.

---

## 9. Limitations

- **The 3B model is the binding constraint.** Answer quality is unstable across
  prompt rewordings and refuses too often. Architecture and retrieval are not
  the bottleneck.
- **Conflict detection is numeric only.** A document saying a fee is waivable
  against one saying it is not cannot be detected by any of this.
- **Figure checking proves existence, not attachment.** See §6.
- **Routing is rule-based**, so new phrasings need new rules. 95% agreement with
  expected behaviour; the residual is mostly out-of-scope questions caught later
  by retrieval-miss detection instead.
- **Sources are live.** Promotional copy on card pages changes between fetches;
  fees and rates have been stable, but the evaluation should be re-run after
  fetching if the numbers matter.
- **The cloud backend is unverified.** No API key was used; the interface exists
  and fails loudly rather than silently falling back.

## 10. What I would do next

1. Run the same evaluation against a larger model to separate architecture from
   model capacity. Everything in §7 and §9 predicts the refusal rate collapses
   and the headline metric holds; that is a prediction, not a result.
2. Move conflict repair upstream — mark superseded chunks at ingest from the
   conflict register, so adjudication is a backstop rather than the mechanism.
3. Extend the test set to semantic contradictions, which currently no metric
   covers.
