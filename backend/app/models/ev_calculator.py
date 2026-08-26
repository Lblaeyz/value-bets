"""
Expected value calculator and recommendation engine.
Consumes Poisson probability output and live odds to find value bets.
All thresholds are read from environment variables at import time.
"""
from __future__ import annotations

import os
from typing import Optional

from app.utils.logger import logger

# ------------------------------------------------------------------ #
# Thresholds from environment
# ------------------------------------------------------------------ #

MIN_VALUE_EDGE: float  = float(os.getenv("MIN_VALUE_EDGE",    "5"))  / 100   # e.g. 5 → 0.05
MIN_CONFIDENCE: float  = float(os.getenv("MIN_CONFIDENCE",    "70")) / 100   # e.g. 70 → 0.70
MIN_DATA_QUALITY: float = float(os.getenv("MIN_DATA_QUALITY", "60")) / 100   # e.g. 60 → 0.60
MIN_MODEL_PROB: float  = 0.55                                                 # skip markets < 55%

# ------------------------------------------------------------------ #
# Market key → Poisson probability dict key mapping
# ------------------------------------------------------------------ #

# Maps (market_label, selection) from the odds table to the key in the
# probability dict returned by calculate_match_probabilities().
_MARKET_PROB_MAP: dict[tuple[str, str], str] = {
    # 1X2
    ("1X2",  "home"):            "home_win_prob",
    ("1X2",  "draw"):            "draw_prob",
    ("1X2",  "away"):            "away_win_prob",
    # BTTS
    ("BTTS", "yes"):             "btts_yes_prob",
    ("BTTS", "no"):              "btts_no_prob",
    # Draw No Bet
    ("DNB",  "home"):            "draw_no_bet_home",
    ("DNB",  "away"):            "draw_no_bet_away",
    # Double Chance
    ("DC",   "1x"):              "double_chance_1x",
    ("DC",   "12"):              "double_chance_12",
    ("DC",   "x2"):              "double_chance_x2",
    # Over/Under (standard .5 lines)
    ("OU",   "over_0.5"):        "over_05_prob",
    ("OU",   "under_0.5"):       "under_05_prob",
    ("OU",   "over_1.5"):        "over_15_prob",
    ("OU",   "under_1.5"):       "under_15_prob",
    ("OU",   "over_2.5"):        "over_25_prob",
    ("OU",   "under_2.5"):       "under_25_prob",
    ("OU",   "over_3.5"):        "over_35_prob",
    ("OU",   "under_3.5"):       "under_35_prob",
    # Asian totals
    ("AOU",  "over_0.75"):       "asian_total_0_75",
    ("AOU",  "over_1.0"):        "asian_total_1_00",
    ("AOU",  "over_1.25"):       "asian_total_1_25",
    ("AOU",  "over_1.5"):        "asian_total_1_50",
    ("AOU",  "over_1.75"):       "asian_total_1_75",
    ("AOU",  "over_2.0"):        "asian_total_2_00",
    ("AOU",  "over_2.25"):       "asian_total_2_25",
    ("AOU",  "over_2.5"):        "asian_total_2_50",
    ("AOU",  "over_2.75"):       "asian_total_2_75",
    ("AOU",  "over_3.0"):        "asian_total_3_00",
    ("AOU",  "over_3.25"):       "asian_total_3_25",
    ("AOU",  "over_3.5"):        "asian_total_3_50",
}


# ------------------------------------------------------------------ #
# Core calculations
# ------------------------------------------------------------------ #

def calculate_value_edge(
    model_probability: float,
    bookmaker_odds: float,
) -> dict:
    """
    Compute value edge and Kelly stake for a single selection.

    Args:
        model_probability: Our Poisson model's probability (0–1).
        bookmaker_odds:    Decimal odds offered by the bookmaker (> 1.0).

    Returns:
        Dict with:
          bookmaker_probability — 1 / bookmaker_odds
          value_edge            — model_prob - bookmaker_prob (positive = value)
          kelly_stake           — raw Kelly fraction
          fractional_kelly      — quarter-Kelly (recommended stake fraction)
    """
    if bookmaker_odds <= 1.0:
        logger.debug("calculate_value_edge: invalid odds %.4f — returning zero edge", bookmaker_odds)
        return {
            "bookmaker_probability": 0.0,
            "value_edge":            0.0,
            "kelly_stake":           0.0,
            "fractional_kelly":      0.0,
        }

    bookmaker_probability = round(1.0 / bookmaker_odds, 6)
    value_edge            = round(model_probability - bookmaker_probability, 6)

    # Kelly criterion: f* = (bp - q) / b  where b = odds-1, p = model_prob, q = 1-p
    b = bookmaker_odds - 1.0
    if b <= 0:
        kelly_stake = 0.0
    else:
        kelly_stake = max(0.0, round((b * model_probability - (1 - model_probability)) / b, 6))

    fractional_kelly = round(kelly_stake * 0.25, 6)

    return {
        "bookmaker_probability": bookmaker_probability,
        "value_edge":            value_edge,
        "kelly_stake":           kelly_stake,
        "fractional_kelly":      fractional_kelly,
    }


def calculate_confidence_score(
    data_quality: float,
    value_edge: float,
    matches_used: int,
) -> float:
    """
    Compute a 0–1 composite confidence score for a prediction.

    Weighting:
      50% — data quality (0–1 normalised score)
      30% — value edge magnitude (clamped to 0–0.30 range, then normalised)
      20% — matches used (clamped to 0–10, then normalised)

    Args:
        data_quality:  Normalised data quality (0–1).
        value_edge:    Raw value edge (can be negative; negative → 0 contribution).
        matches_used:  Total historical matches feeding the model.

    Returns:
        Confidence score in [0.0, 1.0].
    """
    # Component 1: data quality (already 0–1)
    dq_component = max(0.0, min(1.0, data_quality)) * 0.50

    # Component 2: value edge — clamp to [0, 0.30], then scale to [0, 1]
    edge_clamped = max(0.0, min(value_edge, 0.30))
    edge_component = (edge_clamped / 0.30) * 0.30

    # Component 3: matches used — clamp to [0, 10], scale to [0, 1]
    matches_clamped = max(0, min(matches_used, 10))
    matches_component = (matches_clamped / 10) * 0.20

    score = round(dq_component + edge_component + matches_component, 6)
    logger.debug(
        "calculate_confidence_score: dq=%.3f edge=%.4f matches=%d → score=%.4f",
        data_quality, value_edge, matches_used, score,
    )
    return score


# ------------------------------------------------------------------ #
# Market matching
# ------------------------------------------------------------------ #

def _lookup_model_prob(
    market: str,
    selection: str,
    probabilities: dict,
) -> Optional[float]:
    """
    Return the model probability for a (market, selection) pair.
    Returns None if there is no mapping or the key is absent.
    """
    # Normalise selection: lowercase, strip whitespace
    sel = selection.lower().strip()
    key = _MARKET_PROB_MAP.get((market, sel))
    if key is None:
        return None
    return probabilities.get(key)


def find_best_market(
    probabilities: dict,
    odds_data: list[dict],
) -> Optional[dict]:
    """
    Scan all (bookmaker, market, selection) combinations in *odds_data*
    and return the single bet with the highest value edge.

    Filters applied:
      • Model probability must be ≥ 55% (MIN_MODEL_PROB)
      • Value edge must be > MIN_VALUE_EDGE
      • Unknown markets (no probability mapping) are silently skipped

    Args:
        probabilities: Output of calculate_match_probabilities().
        odds_data:     List of odds row dicts from the Supabase odds table,
                       each with keys: bookmaker, market, selection,
                       odds_decimal, implied_probability.

    Returns:
        Dict describing the best bet, or None if no qualifying market found.
    """
    best: Optional[dict] = None
    best_edge: float = MIN_VALUE_EDGE  # must beat this to qualify

    for row in odds_data:
        market:    str   = row.get("market", "")
        selection: str   = row.get("selection", "")
        odds_dec:  float = float(row.get("odds_decimal") or 0.0)
        bookmaker: str   = row.get("bookmaker", "")

        if odds_dec <= 1.0:
            continue

        model_prob = _lookup_model_prob(market, selection, probabilities)
        if model_prob is None:
            continue

        # Gate: model must be confident enough on this outcome
        if model_prob < MIN_MODEL_PROB:
            continue

        ev = calculate_value_edge(model_prob, odds_dec)
        edge = ev["value_edge"]

        if edge > best_edge:
            best_edge = edge
            best = {
                "market":                market,
                "selection":             selection,
                "bookmaker":             bookmaker,
                "odds_decimal":          odds_dec,
                "model_probability":     round(model_prob, 6),
                "bookmaker_probability": ev["bookmaker_probability"],
                "value_edge":            round(edge, 6),
                "kelly_stake":           ev["kelly_stake"],
                "fractional_kelly":      ev["fractional_kelly"],
            }

    if best:
        logger.debug(
            "find_best_market: best=%s/%s @ %.3f edge=%.4f via %s",
            best["market"], best["selection"],
            best["odds_decimal"], best["value_edge"], best["bookmaker"],
        )
    return best


# ------------------------------------------------------------------ #
# Recommendation engine
# ------------------------------------------------------------------ #

def generate_recommendations(
    fixtures: list[dict],
    probabilities: list[dict],
    odds: dict[int, list[dict]],
) -> list[dict]:
    """
    Generate final value-bet recommendations for a pipeline run.

    For each fixture that has both Poisson probabilities and odds data:
      1. Find the best market (find_best_market).
      2. Calculate confidence score.
      3. Apply MIN_VALUE_EDGE, MIN_CONFIDENCE, MIN_DATA_QUALITY filters.
      4. Build a predictions-table-ready record.

    Fixtures are sorted by value_edge descending before returning.

    Args:
        fixtures:      List of fixture dicts (must contain 'id' or 'fixture_id').
        probabilities: List of probability dicts from run_poisson_for_all_fixtures().
        odds:          Dict of fixture_id → list of odds row dicts.

    Returns:
        List of recommendation dicts, each ready to upsert into the
        predictions table.
    """
    logger.info(
        "generate_recommendations: %d fixtures, %d probability sets, %d odds sets",
        len(fixtures), len(probabilities), len(odds),
    )

    # Index probability dicts by fixture_id for O(1) lookup
    prob_index: dict[int, dict] = {}
    for p in probabilities:
        fid = p.get("fixture_id")
        if fid:
            prob_index[fid] = p

    recommendations: list[dict] = []
    skip_counts: dict[str, int] = {
        "no_probabilities":   0,
        "no_odds":            0,
        "no_value_market":    0,
        "low_value_edge":     0,
        "low_confidence":     0,
        "low_data_quality":   0,
    }

    for fixture in fixtures:
        fixture_id: int = fixture.get("id") or fixture.get("fixture_id") or 0

        # ── Probability data ──────────────────────────────────────── #
        probs = prob_index.get(fixture_id)
        if not probs:
            skip_counts["no_probabilities"] += 1
            logger.debug("generate_recommendations: fixture=%d — no probabilities, skipping", fixture_id)
            continue

        # ── Odds data ─────────────────────────────────────────────── #
        fixture_odds = odds.get(fixture_id, [])
        if not fixture_odds:
            skip_counts["no_odds"] += 1
            logger.debug("generate_recommendations: fixture=%d — no odds, skipping", fixture_id)
            continue

        # ── Data quality gate ─────────────────────────────────────── #
        dq_raw: float = float(probs.get("data_quality_score") or 0.0)
        # data_quality_score comes in as 0–100 from the Poisson model
        dq_norm: float = dq_raw / 100.0 if dq_raw > 1 else dq_raw

        if dq_norm < MIN_DATA_QUALITY:
            skip_counts["low_data_quality"] += 1
            logger.info(
                "generate_recommendations: fixture=%d SKIPPED — "
                "data_quality=%.1f%% < %.0f%%",
                fixture_id, dq_norm * 100, MIN_DATA_QUALITY * 100,
            )
            continue

        # ── Best market ───────────────────────────────────────────── #
        best = find_best_market(probs, fixture_odds)
        if best is None:
            skip_counts["no_value_market"] += 1
            logger.debug(
                "generate_recommendations: fixture=%d — no market clears edge threshold",
                fixture_id,
            )
            continue

        # ── Value edge gate ───────────────────────────────────────── #
        if best["value_edge"] < MIN_VALUE_EDGE:
            skip_counts["low_value_edge"] += 1
            logger.info(
                "generate_recommendations: fixture=%d SKIPPED — "
                "value_edge=%.4f < %.4f",
                fixture_id, best["value_edge"], MIN_VALUE_EDGE,
            )
            continue

        # ── Confidence score ──────────────────────────────────────── #
        matches_used: int = int(probs.get("matches_used") or 0)
        confidence = calculate_confidence_score(dq_norm, best["value_edge"], matches_used)

        if confidence < MIN_CONFIDENCE:
            skip_counts["low_confidence"] += 1
            logger.info(
                "generate_recommendations: fixture=%d SKIPPED — "
                "confidence=%.4f < %.4f",
                fixture_id, confidence, MIN_CONFIDENCE,
            )
            continue

        # ── Build prediction record ───────────────────────────────── #
        reasoning = (
            f"Poisson model: {best['model_probability']:.1%} vs "
            f"bookmaker {best['bookmaker_probability']:.1%} implied. "
            f"Edge: {best['value_edge']:+.2%}. "
            f"λ={probs.get('expected_home_goals', '?'):.2f} "
            f"μ={probs.get('expected_away_goals', '?'):.2f}. "
            f"Based on {matches_used} matches."
        )

        record = {
            "fixture_id":            fixture_id,
            "market":                best["market"],
            "selection":             best["selection"],
            "model_probability":     best["model_probability"],
            "bookmaker_probability": best["bookmaker_probability"],
            "value_edge":            best["value_edge"],
            "confidence_score":      round(confidence, 6),
            "risk_score":            round(1.0 - confidence, 6),
            "data_quality_score":    round(dq_norm, 6),
            "recommended_odds":      best["odds_decimal"],
            "recommended_bookmaker": best["bookmaker"],
            "reasoning":             reasoning,
            "kelly_stake":           best["fractional_kelly"],
            "status":                "RECOMMENDED",
        }
        recommendations.append(record)

        logger.info(
            "generate_recommendations: fixture=%d ✓ %s/%s edge=%.3f conf=%.3f kelly=%.4f",
            fixture_id,
            best["market"], best["selection"],
            best["value_edge"], confidence, best["fractional_kelly"],
        )

    # ── Sort by value edge descending ─────────────────────────────── #
    recommendations.sort(key=lambda r: r["value_edge"], reverse=True)

    logger.info(
        "generate_recommendations: DONE — %d recommendations | skips: %s",
        len(recommendations), skip_counts,
    )
    return recommendations
