"""Retrieval over the ingested chunks.

Lexical BM25 only at this stage. Banking content is full of exact tokens
(fee names, card names, rates), which is precisely where dense-only retrieval
tends to slip, so BM25 is the half that gets built and measured first.
The dense half plugs into `search()` later.
"""
import json
import re
from pathlib import Path

import domain
from rank_bm25 import BM25Okapi

from query import expand

ROOT = Path(__file__).resolve().parent.parent


# Below this, BM25 is matching stopwords rather than substance. Treated as a
# retrieval miss so the caller can refuse instead of improvising.
MIN_SCORE = 1.0


def tokenize(text):
    """Lowercase word tokens, keeping decimals and currency amounts intact."""
    text = text.lower().replace("s$", "sgd ")
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?%?", text)


class Retriever:
    def __init__(self, path=None):
        path = path or domain.chunks_path()
        self.chunks = [json.loads(line) for line in path.open(encoding="utf-8")]
        # Index the section heading alongside the body: headings carry the
        # topic word ("late payment charge") that the body often omits.
        corpus = [tokenize(f"{c['section']} {c['text']}") for c in self.chunks]
        self.bm25 = BM25Okapi(corpus)

    @staticmethod
    def _scope_filter(hits, cards):
        """Drop chunks belonging to a card the customer did not ask about.

        Cross-card contamination was the largest single source of forbidden
        values surviving into context: asking about Altitude annual fee kept
        Vantage's S$599.50 in view. Chunks scoped to "all" (agreements, the
        fee schedule) always survive.
        """
        if not cards:
            return hits
        wanted = set(cards)
        return [h for h in hits
                if h["product_scope"] == ["all"] or set(h["product_scope"]) & wanted]

    def search(self, query, k=5, min_score=MIN_SCORE, cards=None):
        """Return the top-k chunks, or nothing at all.

        Returning the head of the index when every score is zero is worse than
        returning nothing: the caller cannot tell a bad match from a total
        miss, and the model downstream will answer from whatever it is handed.
        """
        expanded, matched = expand(query)
        scores = self.bm25.get_scores(tokenize(expanded))
        # Over-fetch so scope filtering still leaves k results.
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:k * 3 if cards else k]
        hits = [dict(self.chunks[i], score=round(float(scores[i]), 3))
                for i in ranked if scores[i] >= min_score]
        hits = self._scope_filter(hits, cards)[:k]
        for h in hits:
            h["glossary_terms"] = matched
        return hits


if __name__ == "__main__":
    r = Retriever()
    print(f"索引 {len(r.chunks)} chunks\n")
    for q in [
        "What is the interest rate on my credit card?",
        "How much is the late payment charge?",
        "What is the annual fee for the DBS Vantage card?",
        "minimum income to apply",
    ]:
        print("=" * 74)
        print("Q:", q)
        for h in r.search(q, k=3):
            date = h["effective_date"] or "无日期"
            print(f"  [{h['score']:>6}] {h['chunk_id']:<26} {date:<14} {h['section'][:38]}")
            print(f"           {re.sub(r'chunks=', '', h['text'][:130])}...")
        print()


# Cosine floor for the dense side. Observed separation on this corpus is
# narrow (correct hits ~0.82+, off-topic ~0.77), so dense alone is not
# trusted to declare a miss; see HybridRetriever.search.
DENSE_FLOOR = 0.82

RRF_K = 60  # standard reciprocal-rank-fusion constant


class HybridRetriever:
    """Lexical and dense retrieval fused by reciprocal rank.

    The two halves fail differently, which is the reason to run both: BM25
    scores zero on all-Chinese phrasing outside the glossary, while the dense
    encoder drifts onto topically-adjacent clauses. Rank fusion needs no score
    calibration between them, which matters because their scales are unrelated.

    Crucially, fusion must not erase the miss signal. A dense index always
    returns its nearest neighbours, so letting it vote unconditionally would
    turn every out-of-scope question into a confident-looking retrieval.
    """

    def __init__(self, lexical=None, dense=None):
        from dense import DenseIndex

        self.lexical = lexical or Retriever()
        self.dense = dense or DenseIndex().build()
        self.by_id = {c["chunk_id"]: c for c in self.lexical.chunks}

    def search(self, query, k=5, dense_floor=DENSE_FLOOR, cards=None, min_score=MIN_SCORE):
        lex_hits = self.lexical.search(query, k=k * 2, cards=cards, min_score=min_score)
        dense_hits = Retriever._scope_filter(
            [h for h in self.dense.search(query, k=k * 3 if cards else k * 2)
             if h["score"] >= dense_floor], cards)

        # Neither side found anything it is willing to stand behind.
        if not lex_hits and not dense_hits:
            return []

        fused = {}
        for rank, h in enumerate(lex_hits):
            fused[h["chunk_id"]] = fused.get(h["chunk_id"], 0) + 1 / (RRF_K + rank)
        for rank, h in enumerate(dense_hits):
            fused[h["chunk_id"]] = fused.get(h["chunk_id"], 0) + 1 / (RRF_K + rank)

        ranked = sorted(fused, key=lambda cid: -fused[cid])[:k]
        return [dict(self.by_id[cid], score=round(fused[cid], 5)) for cid in ranked]
