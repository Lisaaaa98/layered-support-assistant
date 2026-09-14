"""Which industry the assistant is currently answering for.

The pipeline was built for credit cards and is being extended to telco. The
parts that turned out to be domain-specific are small and all of one kind:
the product names, the competitors whose products must be declined, the
transaction scopes that separate one fee from another, and the Chinese-to-
English glossary. Everything else — parsing, conflict adjudication, routing
logic, grounding checks, evaluation — is the same code.

Keeping the difference in a config file rather than a subclass is deliberate:
it makes the size of the difference visible. If porting to a third industry
needs more than a new JSON file and a parser, that is worth knowing rather
than hiding behind an abstraction.

Select with ASSISTANT_DOMAIN=telco, or domain.use("telco") in a script.
"""
import json
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOMAINS = ROOT / "domains"
DEFAULT = "bank"

_active = os.environ.get("ASSISTANT_DOMAIN", DEFAULT)


def use(domain_id):
    """Switch domains for this process."""
    global _active
    if not (DOMAINS / f"{domain_id}.json").exists():
        raise ValueError(f"unknown domain: {domain_id}")
    _active = domain_id
    config.cache_clear()
    return config()


def active():
    return _active


@lru_cache(maxsize=None)
def config(domain_id=None):
    path = DOMAINS / f"{domain_id or _active}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def data_dir():
    return ROOT / "data" / _active


def raw_dir():
    return data_dir() / "raw"


def processed_dir():
    return data_dir() / "processed"


def eval_dir():
    return ROOT / "eval" / _active


def sources_path():
    return data_dir() / "sources.json"


def chunks_path():
    return processed_dir() / "chunks.jsonl"


def embeddings_path():
    return processed_dir() / "embeddings.npy"


def known_products():
    return config()["known_products"]


def glossary():
    return config()["glossary"]


def txn_patterns():
    return [tuple(t) for t in config()["txn_patterns"]]


def product_patterns():
    """Patterns that pull a product name out of a question.

    Case-insensitivity is stored per pattern because the Chinese ones must not
    be compiled with IGNORECASE-dependent behaviour they never needed.
    """
    cfg = config()
    flags = cfg.get("product_pattern_flags") or [""] * len(cfg["product_patterns"])
    return [re.compile(p, re.I if "i" in f else 0)
            for p, f in zip(cfg["product_patterns"], flags)]
