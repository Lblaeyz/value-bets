"""
Predictions routes — paginated list and single-record detail.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.db.supabase_client import supabase
from app.utils.logger import logger

router = APIRouter(prefix="/predictions", tags=["predictions"])

_VALID_STATUSES = {"PENDING", "RECOMMENDED", "REJECTED", "WON", "LOST", "VOID"}
_VALID_MARKETS  = {"1X2", "BTTS", "OU", "AOU", "DNB", "DC"}


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@router.get("", summary="List predictions with filtering and pagination")
async def list_predictions(
    league_id:      Optional[int]   = Query(None, description="Filter by internal league ID"),
    market:         Optional[str]   = Query(None, description="Market type: 1X2, BTTS, OU, AOU, DNB, DC"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum confidence score (0–1)"),
    date_from:      Optional[str]   = Query(None, description="Include predictions for fixtures from this date (YYYY-MM-DD)"),
    date_to:        Optional[str]   = Query(None, description="Include predictions for fixtures up to this date (YYYY-MM-DD)"),
    status:         Optional[str]   = Query(None, description="Status: PENDING, RECOMMENDED, REJECTED, WON, LOST, VOID"),
    page:           int             = Query(1,    ge=1),
    page_size:      int             = Query(50,   ge=1, le=200),
) -> dict[str, Any]:
    """
    Return a paginated list of predictions.

    All filter parameters are optional and combinable.
    Results are ordered by value_edge descending (best value first).
    """
    logger.info(
        "GET /predictions league=%s market=%s min_conf=%s status=%s page=%d",
        league_id, market, min_confidence, status, page,
    )

    # Validate enum-style params early
    if status and status.upper() not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status {status!r}. Valid values: {sorted(_VALID_STATUSES)}",
        )
    if market and market.upper() not in _VALID_MARKETS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid market {market!r}. Valid values: {sorted(_VALID_MARKETS)}",
        )

    offset = (page - 1) * page_size

    try:
        # Build query with fixture join so we can filter by league and kickoff date
        query = supabase.table("predictions").select(
            "*, fixture:fixtures!fixture_id("
            "  id, kickoff_utc, status, home_goals, away_goals,"
            "  home_team:teams!home_team_id(id, name),"
            "  away_team:teams!away_team_id(id, name),"
            "  league:leagues(id, name, country)"
            ")",
            count="exact",
        )

        if status:
            query = query.eq("status", status.upper())
        if market:
            query = query.eq("market", market.upper())
        if min_confidence is not None:
            query = query.gte("confidence_score", min_confidence)

        # Date range filters work via the fixture join kickoff_utc
        if date_from:
            query = query.gte("fixture.kickoff_utc", f"{date_from}T00:00:00+00:00")
        if date_to:
            query = query.lte("fixture.kickoff_utc", f"{date_to}T23:59:59+00:00")

        if league_id:
            query = query.eq("fixture.league_id", league_id)

        response = (
            query
            .order("value_edge", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )

    except Exception as exc:
        logger.error("GET /predictions: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    rows  = response.data or []
    total = response.count or 0

    logger.info("GET /predictions: %d rows (total=%d page=%d)", len(rows), total, page)

    return {
        "data": rows,
        "pagination": {
            "page":       page,
            "page_size":  page_size,
            "total":      total,
            "pages":      max(1, -(-total // page_size)),  # ceiling division
        },
        "filters": {
            "league_id":      league_id,
            "market":         market,
            "min_confidence": min_confidence,
            "date_from":      date_from,
            "date_to":        date_to,
            "status":         status,
        },
    }


@router.get("/recommended", summary="Shortcut — RECOMMENDED predictions sorted by value edge")
async def recommended_predictions(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """
    Return only RECOMMENDED predictions, highest value edge first.
    Includes full fixture context.
    """
    logger.info("GET /predictions/recommended limit=%d", limit)

    try:
        rows = (
            supabase.table("predictions")
            .select(
                "*, fixture:fixtures!fixture_id("
                "  id, kickoff_utc,"
                "  home_team:teams!home_team_id(name),"
                "  away_team:teams!away_team_id(name),"
                "  league:leagues(name, country)"
                ")"
            )
            .eq("status", "RECOMMENDED")
            .order("value_edge", desc=True)
            .limit(limit)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("GET /predictions/recommended: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    logger.info("GET /predictions/recommended: returning %d records", len(rows))
    return {"data": rows, "count": len(rows)}


@router.get("/{prediction_id}", summary="Single prediction with full detail")
async def get_prediction(prediction_id: int) -> dict[str, Any]:
    """
    Return a single prediction by ID, joined with:
    - Fixture (kickoff, teams, league)
    - All odds for that fixture
    - Any settled result record
    """
    logger.info("GET /predictions/%d", prediction_id)

    try:
        prediction = (
            supabase.table("predictions")
            .select(
                "*, fixture:fixtures!fixture_id("
                "  id, kickoff_utc, status, home_goals, away_goals, data_quality_score,"
                "  home_team:teams!home_team_id(id, name, elo_rating),"
                "  away_team:teams!away_team_id(id, name, elo_rating),"
                "  league:leagues(id, name, country, trust_score)"
                ")"
            )
            .eq("id", prediction_id)
            .maybe_single()
            .execute()
        ).data
    except Exception as exc:
        logger.error("GET /predictions/%d: DB error — %s", prediction_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not prediction:
        raise HTTPException(status_code=404, detail=f"Prediction {prediction_id} not found")

    fixture_id = prediction.get("fixture_id")

    # Fetch supporting data
    try:
        odds = (
            supabase.table("odds")
            .select("bookmaker, market, selection, odds_decimal, implied_probability, recorded_at")
            .eq("fixture_id", fixture_id)
            .eq("market", prediction.get("market", ""))
            .order("bookmaker")
            .execute()
        ).data or []

        result_row = (
            supabase.table("results")
            .select("*")
            .eq("prediction_id", prediction_id)
            .maybe_single()
            .execute()
        ).data

    except Exception as exc:
        logger.error("GET /predictions/%d: enrichment error — %s", prediction_id, exc)
        odds       = []
        result_row = None

    logger.info("GET /predictions/%d: found, odds=%d, settled=%s", prediction_id, len(odds), result_row is not None)

    return {
        "prediction": prediction,
        "market_odds": odds,
        "result":      result_row,
    }
