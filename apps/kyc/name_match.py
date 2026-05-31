"""
Name matching utility for KYC ↔ bank account verification.

Algorithm (settled with Richard):
  A name "matches" if BOTH:
    - The shorter name's tokens are a subset of the longer name's tokens
    - At least N tokens overlap (default 2, configurable via KYC_NAME_MATCH_MIN_SHARED_TOKENS)

This handles:
  ✓ "UZOR RICHARD CHUKWUEMEKA" vs "RICHARD UZOR"        (order, missing middle)
  ✓ "ADEYEMI JOHN" vs "JOHN ADEYEMI"                    (reordered)
  ✓ Case differences ("uzor" vs "UZOR")
  ✓ Punctuation, double spaces, common titles
  ✗ "UZOR RICHARD" vs "ADEBAYO RICHARD"                 (different surname)
  ✗ "UZOR" vs "UZOR RICHARD"                            (single-token match too weak)
"""
import re

from django.conf import settings


# Common honorifics/titles to strip before comparison
_TITLES = {
    'mr', 'mrs', 'ms', 'miss', 'dr', 'prof', 'chief',
    'alhaji', 'alhaja', 'pastor', 'rev', 'sir', 'mallam',
    'engr', 'barr', 'hon',
}


def _min_shared_tokens() -> int:
    return int(getattr(settings, 'KYC_NAME_MATCH_MIN_SHARED_TOKENS', 2))


def _tokenize(name: str) -> set:
    """
    Convert a name string into a set of comparable tokens.

      "Mr. Uzor, Richard CHUKWUEMEKA  Jr."
      → {'uzor', 'richard', 'chukwuemeka', 'jr'}
    """
    if not name:
        return set()
    # Strip punctuation, lowercase, collapse whitespace
    cleaned = re.sub(r'[^\w\s]', ' ', name.lower())
    tokens = cleaned.split()
    # Drop tokens that are titles, single chars, or empty
    return {t for t in tokens if t and len(t) >= 2 and t not in _TITLES}


def names_match(name_a: str, name_b: str) -> bool:
    """
    True if two names refer to the same person under the matching rules.

    Both names are normalized (lowercased, stripped of punctuation/titles)
    and tokenized into sets. Comparison is order- and case-insensitive.

    >>> names_match("UZOR RICHARD CHUKWUEMEKA", "Richard Uzor")
    True
    >>> names_match("UZOR RICHARD", "Adebayo Richard")
    False
    """
    tokens_a = _tokenize(name_a)
    tokens_b = _tokenize(name_b)

    if not tokens_a or not tokens_b:
        return False

    shared = tokens_a & tokens_b
    if len(shared) < _min_shared_tokens():
        return False

    # Shorter token set must be a subset of the longer one
    shorter, longer = sorted([tokens_a, tokens_b], key=len)
    return shorter.issubset(longer)


def names_match_with_score(name_a: str, name_b: str) -> tuple[bool, float]:
    """
    Variant that returns (matched, similarity_score).
    Useful for admin/audit dashboards.

    Score = |shared tokens| / |shorter token set|, clamped to [0, 1].
    """
    tokens_a = _tokenize(name_a)
    tokens_b = _tokenize(name_b)
    if not tokens_a or not tokens_b:
        return False, 0.0
    shared = tokens_a & tokens_b
    shorter, longer = sorted([tokens_a, tokens_b], key=len)
    score = len(shared) / len(shorter) if shorter else 0.0
    matched = (
        len(shared) >= _min_shared_tokens()
        and shorter.issubset(longer)
    )
    return matched, round(score, 2)