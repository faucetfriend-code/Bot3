"""Identity-derived seeding for Optuna searches.

An Optuna sampler draws its startup trials from its seed alone. Two
searches that share a seed therefore open on the identical parameter
vectors, and any agreement between their results is partly an artifact
of that shared draw rather than evidence about the market. Two classes
of defect follow, and both have been observed in this repo:

* a CONSTANT seed (``optuna_runner._build_sampler`` used ``42`` for every
  study ever created), which makes every strategy, symbol, regime and
  objective open identically; and
* a POSITION-DERIVED seed (``base + fold_no`` in the composite tuner),
  which gives the same calendar window a different search depending on
  how many folds happened to be in the run.

The cure for both is the same: derive the seed from the search's
IDENTITY - the things that make it a different experiment - so it is
stable across runs and processes while distinct experiments stay
independent. ``derive_seed`` is the one place that hashing lives.

Use ``hashlib`` and never the builtin ``hash()``: ``hash()`` of a str is
salted per process by ``PYTHONHASHSEED``, so it would make the seed
differ between two invocations of the same command and turn a
reproducibility bug into a total one. SHA-256 is stable across
processes, interpreter versions and platforms.
"""

import hashlib
from typing import Iterable

#: Separator between canonical-string components. Chosen because it
#: cannot occur in a strategy key, a symbol, a regime value, an
#: objective name or an ISO date, so the encoding is unambiguous.
SEED_FIELD_SEPARATOR = "|"

#: Width of the derived seed. Optuna's samplers accept a non-negative
#: 32-bit integer.
SEED_MASK = 0xFFFFFFFF


def derive_seed(namespace: str, base_seed: int, parts: Iterable[str]) -> int:
    """Derive a stable 32-bit seed from a namespaced identity.

    The derivation is recomputable by hand::

        canonical = "|".join([namespace, str(int(base_seed)), *parts])
        seed = int.from_bytes(
            hashlib.sha256(canonical.encode("utf-8")).digest()[:4], "big"
        ) & 0xFFFFFFFF

    Args:
        namespace: Versioned tag identifying the derivation scheme, e.g.
            ``"composite-fold-seed/v1"``. Callers own their own
            namespace so two schemes can never collide, and a future
            change to a scheme is an explicit, greppable version bump
            rather than a silent shift in every result derived under it.
        base_seed: Run-level seed. Changing it moves every derived seed
            in the run together, so a global reproduction knob still
            works.
        parts: The identity components, already canonicalised by the
            caller (sorted, joined, normalised - whatever makes the
            identity order-insensitive where it should be).

    Returns:
        A non-negative seed in ``[0, 0xFFFFFFFF]``, identical for the
        same inputs in any process, on any platform.
    """
    canonical = SEED_FIELD_SEPARATOR.join([namespace, str(int(base_seed)), *parts])
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & SEED_MASK
