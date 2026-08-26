"""
Core probability engine — Dixon-Coles Poisson model.
No LLMs. No external APIs. Pure mathematics only.

Derives match outcome probabilities from team attack/defence strength
estimates, builds an 8×8 scoreline matrix, and extracts probabilities
for every market the platform trades.

Dependencies: scipy, numpy (both in requirements.txt)
"""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson  # type: ignore
from typing import Optional

from app.db.supabase_client import supabase
from app.utils.logger import logger

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

MAX_GOALS = 7          # 8×8 matrix: 0..7 goals per team
MIN_STRENGTH = 0.10    # Floor for attack/defence to avoid degenerate distributions
MIN_MATCHES_FOR_QA = 4 # Fewer matches → low data quality score
MIN_DATA_QUALITY = 40  # Skip fixture if data quality score < 40

# European average goals per match per side (home / away)
# Used when league-level average cannot be computed from DB.
_LEAGUE_AVG_HOME = 1.36
_LEAGUE_AVG_AWAY = 1.06


# ------------------------------------------------------------------ #
# Internal helpers
# ------------------------------------------------------------------ #

def _safe_divide(numerator: float, denominator: float, fallback: float = 1.0) -> float:
    """Division with zero-guard and minimum-value clamp."""
    if denominator == 0:
        return fallback
    return max(MIN_STRENGTH, numerator / denominator)


def _fetch_team_recent_matches(team_id: int, venue: str, limit: int = 10) -> list[dict]:
    """
    Load the last *limit* FINISHED fixtures for *team_id* at *venue*.
    venue must be 'home' or 'away'.
    """
    if venue == "home":
        col = "home_team_id"
    else:
        col = "away_team_id"

    response = (
        supabase.table("fixtures")
        .select("id, home_team_id, away_team_id, home_goals, away_goals")
        .eq(col, team_id)
        .eq("status", "FINISHED")
        .not_.is_("home_goals", "null")
        .not_.is_("away_goals", "null")
        .order("kickoff_utc", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def _league_avg_goals(league_id: int) -> tuple[float, float]:
    """
    Compute the mean home and away goals per match for a league
    from the fixtures table. Falls back to European averages if
    there is insufficient data (< 20 matches).
    """
    response = (
        supabase.table("fixtures")
        .select("home_goals, away_goals")
        .eq("league_id", league_id)
        .eq("status", "FINISHED")
        .not_.is_("home_goals", "null")
        .not_.is_("away_goals", "null")
        .limit(500)
        .execute()
    )
    rows: list[dict] = response.data or []

    if len(rows) < 20:
        logger.debug(
            "_league_avg_goals: only %d matches for league %d — using European defaults",
            len(rows), league_id,
        )
        return _LEAGUE_AVG_HOME, _LEAGUE_AVG_AWAY

    home_avg = np.mean([r["home_goals"] for r in rows])
    away_avg = np.mean([r["away_goals"] for r in rows])
    logger.debug(
        "_league_avg_goals: league=%d n=%d home_avg=%.3f away_avg=%.3f",
        league_id, len(rows), home_avg, away_avg,
    )
    return float(home_avg), float(away_avg)


def _build_score_matrix(lam: float, mu: float) -> np.ndarray:
    """
    Build an (MAX_GOALS+1) × (MAX_GOALS+1) Poisson score-probability matrix.
    matrix[i, j] = P(home scores i) × P(away scores j).
    """
    home_probs = np.array([poisson.pmf(i, lam) for i in range(MAX_GOALS + 1)])
    away_probs = np.array([poisson.pmf(j, mu)  for j in range(MAX_GOALS + 1)])

    # Outer product → full joint distribution
    matrix = np.outer(home_probs, away_probs)

    # Normalise to correct for truncation at MAX_GOALS
    total = matrix.sum()
    if total > 0:
        matrix /= total

    return matrix


def _total_goal_probs(matrix: np.ndarray) -> np.ndarray:
    """
    Return a 1-D array where index k = P(home + away == k).
    Length = 2 * MAX_GOALS + 1.
    """
    size = (MAX_GOALS + 1) * 2 - 1
    totals = np.zeros(size)
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            totals[i + j] += matrix[i, j]
    return totals


def _asian_over_prob(total_probs: np.ndarray, line: float) -> float:
    """
    Compute the 'fair probability' of Over *line* goals for Asian
    quarter-ball markets.

    Simplified model per platform spec:
      • Whole line  (X.00): push at n     → 0.5 × P(n) + P(≥ n+1)
      • Half line   (X.50): no push       → P(≥ n+1)
      • .25 quarter (X.25): push at n     → 0.5 × P(n) + P(≥ n+1)
      • .75 quarter (X.75): push at n+1   → 0.5 × P(n+1) + P(≥ n+2)

    Args:
        total_probs: 1-D array from _total_goal_probs().
        line:        Asian total line value (e.g. 1.25, 2.50).
    """
    n = int(line)
    decimal = round(line - n, 2)
    max_k = len(total_probs) - 1

    def p(k: int) -> float:
        return float(total_probs[k]) if 0 <= k <= max_k else 0.0

    def p_ge(k: int) -> float:
        return float(total_probs[k:].sum()) if k <= max_k else 0.0

    if decimal in (0.0, 0.25):          # push at n
        return round(0.5 * p(n) + p_ge(n + 1), 6)
    elif decimal == 0.50:               # no push
        return round(p_ge(n + 1), 6)
    elif decimal == 0.75:               # push at n+1
        return round(0.5 * p(n + 1) + p_ge(n + 2), 6)
    else:
        return round(p_ge(n + 1), 6)


# ------------------------------------------------------------------ #
# Public: calculate_team_strength
# ------------------------------------------------------------------ #

def calculate_team_strength(
    team_id: int,
    matches: list[dict],
    venue: str,
    league_avg_scored: float = _LEAGUE_AVG_HOME,
    league_avg_conceded: float = _LEAGUE_AVG_AWAY,
) -> dict:
    """
    Derive a team's attack strength and defensive weakness from recent results.

    Args:
        team_id:              Internal teams.id.
        matches:              List of finished fixture dicts
                              (must contain home_team_id, home_goals, away_goals).
        venue:                'home' or 'away'.
        league_avg_scored:    Mean goals scored per match league-wide
                              (same-side perspective as venue).
        league_avg_conceded:  Mean goals conceded per match league-wide.

    Returns:
        Dict with:
          attack_strength     — team's scoring rate ÷ league average
          defence_weakness    — team's conceding rate ÷ league average
          matches_used        — number of matches included
          data_quality_score  — (matches_used / 10) × 100  [0–100]
    """
    if venue not in ("home", "away"):
        raise ValueError(f"venue must be 'home' or 'away', got {venue!r}")

    if not matches:
        logger.debug("calculate_team_strength: no matches for team_id=%d venue=%s", team_id, venue)
        return {
            "attack_strength":    1.0,
            "defence_weakness":   1.0,
            "matches_used":       0,
            "data_quality_score": 0.0,
        }

    goals_scored:   list[float] = []
    goals_conceded: list[float] = []

    for m in matches:
        if m.get("home_goals") is None or m.get("away_goals") is None:
            continue
        if venue == "home" and m.get("home_team_id") == team_id:
            goals_scored.append(float(m["home_goals"]))
            goals_conceded.append(float(m["away_goals"]))
        elif venue == "away" and m.get("away_team_id") == team_id:
            goals_scored.append(float(m["away_goals"]))
            goals_conceded.append(float(m["home_goals"]))

    n = len(goals_scored)
    if n == 0:
        return {
            "attack_strength":    1.0,
            "defence_weakness":   1.0,
            "matches_used":       0,
            "data_quality_score": 0.0,
        }

    team_avg_scored   = float(np.mean(goals_scored))
    team_avg_conceded = float(np.mean(goals_conceded))

    attack_strength  = _safe_divide(team_avg_scored,   league_avg_scored,   1.0)
    defence_weakness = _safe_divide(team_avg_conceded, league_avg_conceded, 1.0)
    data_quality     = round((n / 10) * 100, 2)

    result = {
        "attack_strength":    round(attack_strength,  4),
        "defence_weakness":   round(defence_weakness, 4),
        "matches_used":       n,
        "data_quality_score": data_quality,
    }
    logger.debug(
        "calculate_team_strength: team=%d venue=%s n=%d "
        "attack=%.3f defence=%.3f dq=%.1f",
        team_id, venue, n,
        attack_strength, defence_weakness, data_quality,
    )
    return result


# ------------------------------------------------------------------ #
# Public: calculate_match_probabilities
# ------------------------------------------------------------------ #

def calculate_match_probabilities(
    home_team_id: int,
    away_team_id: int,
    fixture_id: int,
) -> Optional[dict]:
    """
    Compute match outcome and market probabilities for a single fixture
    using a Poisson model.

    Steps:
      1. Load last 10 home fixtures for home team.
      2. Load last 10 away fixtures for away team.
      3. Derive attack/defence strengths.
      4. Compute expected goals (λ, μ).
      5. Build 8×8 Poisson score matrix.
      6. Extract 1X2, O/U, BTTS, Asian, DNB, DC probabilities.

    Returns:
        Dict of all market probabilities, or None if data_quality_score < 40.
    """
    logger.info(
        "calculate_match_probabilities: fixture=%d home=%d away=%d",
        fixture_id, home_team_id, away_team_id,
    )

    # ── 1 & 2. Fetch recent matches ───────────────────────────────── #
    home_matches = _fetch_team_recent_matches(home_team_id, "home", limit=10)
    away_matches = _fetch_team_recent_matches(away_team_id, "away", limit=10)

    logger.debug(
        "calculate_match_probabilities: fixture=%d home_matches=%d away_matches=%d",
        fixture_id, len(home_matches), len(away_matches),
    )

    # ── 3. League averages ────────────────────────────────────────── #
    # Resolve league_id from fixture
    fixture_row = (
        supabase.table("fixtures")
        .select("league_id")
        .eq("id", fixture_id)
        .maybe_single()
        .execute()
    ).data
    league_id = fixture_row["league_id"] if fixture_row else 0

    league_avg_home, league_avg_away = _league_avg_goals(league_id)

    # ── 4. Team strengths ─────────────────────────────────────────── #
    home_strength = calculate_team_strength(
        home_team_id, home_matches, "home",
        league_avg_scored=league_avg_home,
        league_avg_conceded=league_avg_away,
    )
    away_strength = calculate_team_strength(
        away_team_id, away_matches, "away",
        league_avg_scored=league_avg_away,
        league_avg_conceded=league_avg_home,
    )

    # Combined data quality = average of both teams, weighted by matches
    total_matches = home_strength["matches_used"] + away_strength["matches_used"]
    if total_matches == 0:
        combined_dq = 0.0
    else:
        combined_dq = (
            home_strength["data_quality_score"] * home_strength["matches_used"]
            + away_strength["data_quality_score"] * away_strength["matches_used"]
        ) / total_matches

    if combined_dq < MIN_DATA_QUALITY:
        logger.warning(
            "calculate_match_probabilities: fixture=%d SKIPPED — "
            "data_quality_score=%.1f < %d (home_matches=%d away_matches=%d)",
            fixture_id, combined_dq, MIN_DATA_QUALITY,
            home_strength["matches_used"], away_strength["matches_used"],
        )
        return None

    # ── 5. Expected goals ─────────────────────────────────────────── #
    lam = max(MIN_STRENGTH, (
        home_strength["attack_strength"]
        * away_strength["defence_weakness"]
        * league_avg_home
    ))
    mu = max(MIN_STRENGTH, (
        away_strength["attack_strength"]
        * home_strength["defence_weakness"]
        * league_avg_away
    ))

    logger.info(
        "calculate_match_probabilities: fixture=%d λ=%.3f μ=%.3f dq=%.1f",
        fixture_id, lam, mu, combined_dq,
    )

    # ── 6. Score matrix ───────────────────────────────────────────── #
    matrix = _build_score_matrix(lam, mu)
    totals = _total_goal_probs(matrix)

    # ── 7. Market probabilities ───────────────────────────────────── #

    # 1X2
    home_win_prob = float(np.sum(np.tril(matrix, -1)))   # home > away
    away_win_prob = float(np.sum(np.triu(matrix,  1)))   # away > home
    draw_prob     = float(np.trace(matrix))

    # Normalise rounding error
    _total_1x2 = home_win_prob + draw_prob + away_win_prob
    home_win_prob /= _total_1x2
    draw_prob     /= _total_1x2
    away_win_prob /= _total_1x2

    # Over/Under (standard half-ball lines)
    def _over(goals: int) -> float:
        """P(total > goals)  →  standard over-N.5 probability."""
        return round(float(totals[goals + 1:].sum()), 6)

    def _under(goals: int) -> float:
        return round(1.0 - _over(goals), 6)

    # BTTS
    p_home_blanks = float(poisson.pmf(0, lam))
    p_away_blanks = float(poisson.pmf(0, mu))
    btts_yes = round((1 - p_home_blanks) * (1 - p_away_blanks), 6)
    btts_no  = round(1 - btts_yes, 6)

    # DNB (draw-no-bet)
    dnb_home = round(home_win_prob / (home_win_prob + away_win_prob), 6)
    dnb_away = round(away_win_prob / (home_win_prob + away_win_prob), 6)

    # Double chance
    dc_1x = round(home_win_prob + draw_prob, 6)
    dc_12 = round(home_win_prob + away_win_prob, 6)
    dc_x2 = round(draw_prob     + away_win_prob, 6)

    # Asian totals (all quarter-ball lines the platform trades)
    asian_lines = [0.75, 1.00, 1.25, 1.50, 1.75,
                   2.00, 2.25, 2.50, 2.75,
                   3.00, 3.25, 3.50]
    asian_totals = {
        f"asian_total_{str(line).replace('.', '_')}": _asian_over_prob(totals, line)
        for line in asian_lines
    }

    result = {
        # Identifiers
        "fixture_id":          fixture_id,
        "home_team_id":        home_team_id,
        "away_team_id":        away_team_id,
        "expected_home_goals": round(lam, 4),
        "expected_away_goals": round(mu,  4),

        # Data quality
        "home_matches_used":   home_strength["matches_used"],
        "away_matches_used":   away_strength["matches_used"],
        "matches_used":        total_matches,
        "data_quality_score":  round(combined_dq, 2),

        # 1X2
        "home_win_prob":  round(home_win_prob, 6),
        "draw_prob":      round(draw_prob,     6),
        "away_win_prob":  round(away_win_prob, 6),

        # Over/Under (standard .5 lines)
        "over_05_prob":  _over(0),   "under_05_prob":  _under(0),
        "over_15_prob":  _over(1),   "under_15_prob":  _under(1),
        "over_25_prob":  _over(2),   "under_25_prob":  _under(2),
        "over_35_prob":  _over(3),   "under_35_prob":  _under(3),

        # BTTS
        "btts_yes_prob": btts_yes,
        "btts_no_prob":  btts_no,

        # Asian totals
        **asian_totals,

        # Draw No Bet
        "draw_no_bet_home": dnb_home,
        "draw_no_bet_away": dnb_away,

        # Double chance
        "double_chance_1x": dc_1x,
        "double_chance_12": dc_12,
        "double_chance_x2": dc_x2,
    }

    logger.info(
        "calculate_match_probabilities: fixture=%d → "
        "home=%.3f draw=%.3f away=%.3f | o25=%.3f btts=%.3f",
        fixture_id,
        home_win_prob, draw_prob, away_win_prob,
        result["over_25_prob"], btts_yes,
    )
    return result


# ------------------------------------------------------------------ #
# Public: run_poisson_for_all_fixtures
# ------------------------------------------------------------------ #

def run_poisson_for_all_fixtures(fixtures: list[dict]) -> list[dict]:
    """
    Run calculate_match_probabilities over a list of fixtures.
    Fixtures with insufficient historical data (quality < 40) are silently
    skipped.  Each fixture dict must contain at minimum:
      id (or fixture_id), home_team_id, away_team_id.

    Args:
        fixtures: List of fixture dicts (from Supabase or ingestion layer).

    Returns:
        List of probability dicts (one per successfully processed fixture),
        in the same order as the input list (skipped fixtures omitted).
    """
    logger.info("run_poisson_for_all_fixtures: processing %d fixtures", len(fixtures))

    results:     list[dict] = []
    skipped:     list[int]  = []
    errored:     list[int]  = []

    for f in fixtures:
        fixture_id   = f.get("id") or f.get("fixture_id") or 0
        home_team_id = f.get("home_team_id")
        away_team_id = f.get("away_team_id")

        if not fixture_id or not home_team_id or not away_team_id:
            logger.warning(
                "run_poisson_for_all_fixtures: fixture missing required fields — %s",
                {k: f.get(k) for k in ("id", "fixture_id", "home_team_id", "away_team_id")},
            )
            skipped.append(fixture_id)
            continue

        try:
            probs = calculate_match_probabilities(home_team_id, away_team_id, fixture_id)
        except Exception as exc:
            logger.error(
                "run_poisson_for_all_fixtures: fixture=%d raised %s: %s",
                fixture_id, type(exc).__name__, exc,
                exc_info=True,
            )
            errored.append(fixture_id)
            continue

        if probs is None:
            # Returned None → data quality check failed inside the function
            skipped.append(fixture_id)
        else:
            results.append(probs)

    logger.info(
        "run_poisson_for_all_fixtures: DONE — "
        "processed=%d  skipped(low_dq)=%d  errors=%d  total_in=%d",
        len(results), len(skipped), len(errored), len(fixtures),
    )
    if skipped:
        logger.debug("run_poisson_for_all_fixtures: skipped fixture IDs = %s", skipped)
    if errored:
        logger.warning("run_poisson_for_all_fixtures: errored fixture IDs = %s", errored)

    return results
