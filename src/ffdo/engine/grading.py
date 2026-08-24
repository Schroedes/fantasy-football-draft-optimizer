"""Grades how good a draft pick was, given what else was on the board."""

from __future__ import annotations

from collections.abc import Sequence

AUCTION_GREAT_RATIO = 0.70
AUCTION_GOOD_RATIO = 0.95
AUCTION_FAIR_RATIO = 1.10

SNAKE_GREAT_PERCENTILE = 0.05
SNAKE_GOOD_PERCENTILE = 0.20
SNAKE_FAIR_PERCENTILE = 0.50


def grade_auction_pick(baseline: float, amount: int) -> str:
    """GREAT/GOOD/FAIR/POOR, from what was paid vs. fair-market baseline.

    A zero or negative baseline carries no fair-value signal to grade
    against, so it grades FAIR rather than fabricating a verdict.
    """
    if baseline <= 0:
        return "FAIR"
    ratio = amount / baseline
    if ratio <= AUCTION_GREAT_RATIO:
        return "GREAT"
    if ratio <= AUCTION_GOOD_RATIO:
        return "GOOD"
    if ratio <= AUCTION_FAIR_RATIO:
        return "FAIR"
    return "POOR"


def grade_snake_pick(picked_vor: float, alternative_vors: Sequence[float]) -> str:
    """GREAT/GOOD/FAIR/POOR, from how many still-available players beat it.

    `alternative_vors` is the VOR of every other player who was still
    undrafted immediately before this pick and had positive VOR (i.e. was
    still fantasy-relevant) -- the picked player itself is excluded. An
    empty pool (nothing of value was left) grades FAIR: there was no
    meaningfully better option to have reached past.
    """
    pool = list(alternative_vors)
    if not pool:
        return "FAIR"
    beat_by = sum(1 for v in pool if v > picked_vor)
    percentile = beat_by / len(pool)
    if percentile <= SNAKE_GREAT_PERCENTILE:
        return "GREAT"
    if percentile <= SNAKE_GOOD_PERCENTILE:
        return "GOOD"
    if percentile <= SNAKE_FAIR_PERCENTILE:
        return "FAIR"
    return "POOR"
