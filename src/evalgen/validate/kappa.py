"""Confusion-matrix building over label pairs + the Landis–Koch reading aid.

The κ arithmetic itself lives in ``contracts/agreement.py`` (``kappa_from_confusion``
— the self-validating report must recompute it, and contracts imports no sibling);
this module owns the step from *label sequences* to the integer matrix that function
consumes, and the descriptive band printed next to defined κ values.
"""

from __future__ import annotations

from collections.abc import Sequence


def confusion_matrix(
    human: Sequence[str], judge: Sequence[str], class_order: Sequence[str]
) -> tuple[tuple[int, ...], ...]:
    """Count paired labels into a k×k matrix — rows = human, cols = judge.

    ``class_order`` is the canonical axis order (enum declaration order); every
    class gets its row/column even at zero support — absent classes are reported,
    never silently dropped. Raises ``ValueError`` on unpaired sequences or a label
    outside ``class_order`` (an out-of-taxonomy label cannot be counted into a κ).
    """
    if len(human) != len(judge):
        raise ValueError(
            f"unpaired label sequences: {len(human)} human vs {len(judge)} judge — "
            "kappa is computed over matched pairs only"
        )
    order = list(class_order)
    if len(set(order)) != len(order):
        raise ValueError(f"class_order has duplicates: {order!r}")
    index_of = {name: i for i, name in enumerate(order)}
    k = len(order)
    counts = [[0] * k for _ in range(k)]
    for pair_no, (h, j) in enumerate(zip(human, judge, strict=True)):
        if h not in index_of:
            raise ValueError(f"pair {pair_no}: human label {h!r} not in class_order {order!r}")
        if j not in index_of:
            raise ValueError(f"pair {pair_no}: judge label {j!r} not in class_order {order!r}")
        counts[index_of[h]][index_of[j]] += 1
    return tuple(tuple(row) for row in counts)


def landis_koch_band(kappa: float) -> str:
    """The Landis & Koch (1977) descriptive band for a DEFINED κ.

    <0 "poor"; [0, 0.20] "slight"; (0.20, 0.40] "fair"; (0.40, 0.60] "moderate";
    (0.60, 0.80] "substantial"; (0.80, 1] "almost perfect". A reading aid, not a
    test — nothing gates on a band (ADR-0004 options §6). Callers pass the rounded
    report value; boundary pins (0.60 → "moderate", 0.61 → "substantial") are tested.
    """
    if not -1.0 <= kappa <= 1.0:
        raise ValueError(f"kappa must lie in [-1, 1], got {kappa!r}")
    if kappa < 0.0:
        return "poor"
    if kappa <= 0.20:
        return "slight"
    if kappa <= 0.40:
        return "fair"
    if kappa <= 0.60:
        return "moderate"
    if kappa <= 0.80:
        return "substantial"
    return "almost perfect"
