"""Transaction scopes: the axis that separates a general/special-case pair
from a genuine contradiction.

Two figures are only comparable when they govern the same kind of transaction.
27.8% retail against 28.5% cash advance is not a disagreement, and neither is
a roaming pass price against a local plan price. Getting these boundaries
right is what keeps the conflict detector from flagging correct documents.

The patterns themselves live in the active domain's config, because they are
the one part of this idea that does not transfer between industries.
"""
import domain


def txn_patterns():
    return domain.txn_patterns()
