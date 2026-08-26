"""
Matches routes — fixture details enriched with predictions, odds, injuries, H2H.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.db.supabase_client import supabase
from app.utils.logger import logger

router = APIRouter(prefix="/matches", tags=["matches"])


# ------------------------------------------------------------------ #
# Internal DB helpers
# ------------------------------------------------------------------ #

def _fixture_base_query():
    return (
        supabase.table("fixtures")
        .select(
            "id, kickoff_utc, status, home_goals, away_goals, data_quality_score, "
            "home_team:teams!home_team_id(id, name, elo_rating), "
            "away_team:teams!away_team_id(id, name, elo_rating), "
            "league:leagues(id, name, country, trust_score)"
        )
    )


def _attach_predictions(fixture_ids: list[int]) -> dict[int, list[dict]]:
    """Return a map of fixture_id → list of RECOMMENDED predictions."""
    if not fixture_ids:
        return {}
    rows = (
        supabase.table("predictions")
        .select("*")
        .in_("fixture_id", fixture_ids)
        .eq("status", "RECOMMENDED")
        .order("value_edge", desc=True)
        .execute()
    ).data or []
    result: dict[int, list[dict]] = {}
    for r in rows:
        result.setdefault(r["fixture_id"], []).append(r)
    return result


def _attach_odds(fixture_id: int) -> list[dict]:
    return (
        supabase.table("odds")
        .select("bookmaker, market, selection, odds_decimal, implied_probability, recorded_at")
        .eq("fixture_id", fixture_id)
        .order("market")
        .order("bookmaker")
        .execute()
    ).data or []


def _attach_injuries(fixture_id: int) -> dict[str, list[dict]]:
    rows = (
        supabase.table("injuries")
        .select("player_name, injury_type, status, team_id")
        .eq("fixture_id", fixture_id)
        .execute()
    ).data or []
    result: dict[str, list[dict]] = {"home": [], "away": []}
    for r in rows:
        result.setdefault(str(r.get("team_id")), []).append(r)
    return rows   # caller can split by team_id if needed


def _attach_h2h(home_team_id: int, away_team_id: int) -> dict | None:
    row = (
        supabase.table("h2h_cache")
        .select("data, cached_at")
        .eq("home_team_id", home_team_id)
        .eq("away_team_id", away_team_id)
        .maybe_single()
        .execute()
    ).data
    return row


def _enrich_fixtures(fixtures: list[dict]) -> list[dict]:
    """Attach predictions to a list of fixtures."""
    ids = [f["id"] for f in fixtures if f.get("id")]
    preds_map = _attach_predictions(ids)
    enriched = []
    for f in fixtures:
        enriched.append({**f, "predictions": preds_map.get(f["id"], [])})
    return enriched


def _count_analyzed(target_date: str) -> int:
    """Count how many fixtures were processed for a given date."""
    try:
        rows = (
            supabase.table("fixtures")
            .select("id", count="exact")
            .gte("kickoff_utc", f"{target_date}T00:00:00+00:00")
            .lte("kickoff_utc", f"{target_date}T23:59:59+00:00")
            .execute()
        )
        return rows.count or 0
    except Exception:
        return 0


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@router.get("/today", summary="Today's value bets with fixture details")
async def get_today(request_id: str = Query(default="")) -> dict[str, Any]:
    """
    Return all RECOMMENDED predictions for today's fixtures, joined with
    fixture, team, and league data.

    Returns an empty list with an explanatory message when no value bets
    were identified.
    """
    today = date.today().isoformat()
    logger.info("GET /matches/today date=%s", today)

    try:
        fixtures = (
            _fixture_base_query()
            .gte("kickoff_utc", f"{today}T00:00:00+00:00")
            .lte("kickoff_utc", f"{today}T23:59:59+00:00")
            .order("kickoff_utc")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("GET /matches/today: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    enriched = _enrich_fixtures(fixtures)

    # Filter to fixtures that actually have recommendations
    with_bets = [f for f in enriched if f["predictions"]]

    if not with_bets:
        analyzed = _count_analyzed(today)
        return {
            "date":    today,
            "message": f"No value bets identified today. System analyzed {analyzed} matches.",
            "data":    [],
            "meta":    {"total_fixtures": len(fixtures), "value_bets": 0},
        }

    logger.info("GET /matches/today: %d fixtures with recommendations", len(with_bets))
    return {
        "date": today,
        "data": with_bets,
        "meta": {
            "total_fixtures": len(fixtures),
            "value_bets":     len(with_bets),
        },
    }


@router.get("/date/{target_date}", summary="Value bets for a specific date (YYYY-MM-DD)")
async def get_by_date(target_date: str) -> dict[str, Any]:
    """
    Return RECOMMENDED predictions for a specific calendar date.
    Date must be in YYYY-MM-DD format.
    """
    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid date format. Use YYYY-MM-DD (e.g. 2024-11-15).",
        )

    logger.info("GET /matches/date/%s", target_date)

    try:
        fixtures = (
            _fixture_base_query()
            .gte("kickoff_utc", f"{target_date}T00:00:00+00:00")
            .lte("kickoff_utc", f"{target_date}T23:59:59+00:00")
            .order("kickoff_utc")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("GET /matches/date/%s: DB error — %s", target_date, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    enriched = _enrich_fixtures(fixtures)
    with_bets = [f for f in enriched if f["predictions"]]

    if not with_bets:
        analyzed = _count_analyzed(target_date)
        return {
            "date":    target_date,
            "message": f"No value bets identified today. System analyzed {analyzed} matches.",
            "data":    [],
            "meta":    {"total_fixtures": len(fixtures), "value_bets": 0},
        }

    return {
        "date": target_date,
        "data": with_bets,
        "meta": {"total_fixtures": len(fixtures), "value_bets": len(with_bets)},
    }


@router.get("/{fixture_id}", summary="Full match detail with odds, injuries, H2H and reasoning")
async def get_match_detail(fixture_id: int) -> dict[str, Any]:
    """
    Return complete data for a single fixture:
    - Fixture + team + league info
    - All available odds (every bookmaker and market)
    - Injuries for both teams
    - H2H cache summary
    - Full prediction reasoning text
    """
    logger.info("GET /matches/%d", fixture_id)

    try:
        fixture = (
            _fixture_base_query()
            .eq("id", fixture_id)
            .maybe_single()
            .execute()
        ).data
    except Exception as exc:
        logger.error("GET /matches/%d: DB error — %s", fixture_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not fixture:
        raise HTTPException(status_code=404, detail=f"Fixture {fixture_id} not found")

    home_id = (fixture.get("home_team") or {}).get("id")
    away_id = (fixture.get("away_team") or {}).get("id")

    # Parallel DB fetches (supabase-py is sync; run sequentially but fast)
    try:
        predictions = (
            supabase.table("predictions")
            .select("*")
            .eq("fixture_id", fixture_id)
            .order("value_edge", desc=True)
            .execute()
        ).data or []

        odds     = _attach_odds(fixture_id)
        injuries = _attach_injuries(fixture_id)

        # Split injuries by team
        injuries_home = [i for i in injuries if i.get("team_id") == home_id]
        injuries_away = [i for i in injuries if i.get("team_id") == away_id]

        h2h_raw  = _attach_h2h(home_id, away_id) if home_id and away_id else None
        h2h_data = (h2h_raw or {}).get("data") or []

    except Exception as exc:
        logger.error("GET /matches/%d: enrichment error — %s", fixture_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    # H2H summary stats
    h2h_summary: dict[str, Any] = {"matches": []}
    if h2h_data:
        home_wins  = sum(1 for m in h2h_data if (m.get("home_goals") or 0) > (m.get("away_goals") or 0))
        away_wins  = sum(1 for m in h2h_data if (m.get("away_goals") or 0) > (m.get("home_goals") or 0))
        draws      = len(h2h_data) - home_wins - away_wins
        avg_total  = (
            sum((m.get("home_goals") or 0) + (m.get("away_goals") or 0) for m in h2h_data)
            / len(h2h_data)
        )
        h2h_summary = {
            "matches_played": len(h2h_data),
            "home_wins":      home_wins,
            "away_wins":      away_wins,
            "draws":          draws,
            "avg_total_goals": round(avg_total, 2),
            "cached_at":      (h2h_raw or {}).get("cached_at"),
            "recent_matches": h2h_data[:5],
        }

    logger.info(
        "GET /matches/%d: returning %d predictions, %d odds rows, %d injuries",
        fixture_id, len(predictions), len(odds), len(injuries),
    )

    return {
        "fixture":    fixture,
        "predictions": predictions,
        "odds":       odds,
        "injuries":   {"home": injuries_home, "away": injuries_away},
        "h2h":        h2h_summary,
    }
