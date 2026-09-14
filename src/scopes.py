"""Transaction scopes: the axis that separates a general/special-case pair
from a genuine contradiction.

Two rates are only comparable when they govern the same kind of transaction.
27.8% retail against 28.5% cash advance is not a disagreement, and neither is
a 1.5% foreign-currency fee against the 1% levied on SGD transactions merely
processed offshore. Getting these boundaries right is what keeps the conflict
detector from flagging correct documents.

Shared by ingest (chunk level) and conflict (claim level) so the two can
never drift apart.
"""

TXN_PATTERNS = [
    ("cash_advance", r"cash advance|cash withdrawal"),
    # SGD billed but routed offshore: its own fee line, distinct from an
    # actual currency conversion.
    ("sgd_processed_outside", r"processed outside singapore|charged in singapore dollar"),
    ("foreign_currency", r"foreign currency|dynamic currency conversion|overseas"),
    ("balance_transfer", r"balance transfer"),
    ("instalment", r"instalment|installment"),
]
