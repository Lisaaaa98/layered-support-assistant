"""Dense retrieval, for what the glossary cannot enumerate.

BM25 plus the term glossary handles the closed set of banking vocabulary well,
but customers do not stay inside it: "卡丢了怎么补办" carries no glossary term
and scores zero lexically. A multilingual encoder maps that phrasing onto the
English passage directly, which is the one thing lexical matching cannot do
across languages.

Model choice is constrained by the target machine (8GB, M1): multilingual-e5-small
is 118M parameters and shares an embedding space across 100+ languages, which is
the property that matters here. Larger multilingual encoders would not fit
alongside the 3B generator.
"""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CHUNKS = ROOT / "data" / "processed" / "chunks.jsonl"
CACHE = ROOT / "data" / "processed" / "embeddings.npy"
MODEL_ID = "intfloat/multilingual-e5-small"


class DenseIndex:
    def __init__(self, model_id=MODEL_ID):
        self.model_id = model_id
        self._model = None
        self.chunks = [json.loads(l) for l in CHUNKS.open(encoding="utf-8")]
        self.vectors = None

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_id)

    def _encode(self, texts, kind):
        # e5 requires these prefixes; omitting them measurably degrades the
        # asymmetric query-to-passage match this whole component exists for.
        self._ensure_model()
        prefixed = [f"{kind}: {t}" for t in texts]
        return self._model.encode(prefixed, normalize_embeddings=True,
                                  show_progress_bar=False, batch_size=16)

    def build(self, force=False):
        if CACHE.exists() and not force:
            cached = np.load(CACHE)
            if len(cached) == len(self.chunks):
                self.vectors = cached
                return self
        texts = [f"{c['section']} {c['text']}" for c in self.chunks]
        self.vectors = np.asarray(self._encode(texts, "passage"), dtype=np.float32)
        np.save(CACHE, self.vectors)
        return self

    def search(self, query, k=5):
        if self.vectors is None:
            self.build()
        q = np.asarray(self._encode([query], "query"), dtype=np.float32)[0]
        scores = self.vectors @ q  # both sides are L2-normalised, so this is cosine
        top = np.argsort(-scores)[:k]
        return [dict(self.chunks[i], score=round(float(scores[i]), 4)) for i in top]
