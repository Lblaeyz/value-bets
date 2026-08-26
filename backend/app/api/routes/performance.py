"""
Performance analytics routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db.supabase_client import supabase
from app.utils.logger import logger

router = APIRouter(prefix="/performance", tags=["performance"])

_INSUFFICIENT_DATA_THRESHOLD = 50


def _compute_metrics(results: list[dict]) -> dict[str, Any]:
    """Aggregate a list of results rows into a standard metrics dict."""
    total = len(results)
    wins  = sum(1 for r in results if r.get("outcome") == "WIN")
    losses = sum(1 for r in results if r.get("outcome") == "LOSS")
    voids  = sum(1 for r in results if r.get("outcome") in ("VOID", "PUSH"))

    pl_values  = [float(r.get("profit_loss") or 0) for r in results]
    clv_values = [float(r.get("clv") or 0) for r in results if r.get("clv") is not None]

    total_pl     = sum(pl_values)
    settled      = wins + losses
    roi          = round(total_pl / settled, 4)      if settled else 0.0
    yield_pct    = round(total_pl / total,   4)      if total   else 0.0
    avg_clv      = round(sum(clv_values) / len(clv_values), 4) if clv_values else None

    return {
        "total_predictions": total,
        "wins":              wins,
        "losses":            losses,
        "voids":             voids,
        "pending":           total - wins - losses - voids,
        "roi":               roi,
        "yield_pct":         yield_pct,
        "avg_clv":           avg_clv,
        "total_profit_loss": round(total_pl, 4),
        "insufficient_data": total < _INSUFFICIENT_DATA_THRESHOLD,
    }


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@router.get("/summary", summary="Overall performance summary")
async def performance_summary() -> dict[str, Any]:
    """
    Return aggregate performance stats across all predictions.
    Includes insufficient_data: true if fewer than 50 settled predictions exist.
    """
    logger.info("GET /performance/summary")
    try:
        results = (
            supabase.table("results")
            .select("outcome, profit_loss, clv")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("GET /performance/summary: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    metrics = _compute_metrics(results)
    logger.info(
        "GET /performance/summary: total=%d wins=%d losses=%d roi=%.3f",
        metrics["total_predictions"], metrics["wins"], metrics["losses"], metrics["roi"],
    )
    return metrics


@router.get("/by-league", summary="Performance metrics broken down by league")
async def performance_by_league() -> dict[str, Any]:
    """
    Return win/loss/ROI metrics for each league individually.
    Leagues with zero results are omitted.
    """
    logger.info("GET /performance/by-league")
    try:
        # Join results → predictions → fixtures → leagues
        rows = (
            supabase.table("results")
            .select(
                "outcome, profit_loss, clv,"
                "prediction:predictions!prediction_id("
                "  fixture:fixtures!fixture_id("
                "    league:leagues(id, name, country)"
                "  )"
                ")"
            )
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("GET /performance/by-league: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Group by league
    grouped: dict[str, list[dict]] = {}
    league_meta: dict[str, dict] = {}

    for row in rows:
        try:
            league = (
                row.get("prediction", {})
                   .get("fixture", {})
                   .get("league", {})
            ) or {}
            league_id   = str(league.get("id") or "unknown")
            league_name = league.get("name") or "Unknown"
            country     = league.get("country") or ""
        except (AttributeError, TypeError):
            league_id, league_name, country = "unknown", "Unknown", ""

        league_meta[league_id] = {"id": league_id, "name": league_name, "country": country}
        grouped.setdefault(league_id, []).append(row)

    breakdown = []
    for lid, res in grouped.items():
        metrics = _compute_metrics(res)
        breakdown.append({**league_meta[lid], **metrics})

    breakdown.sort(key=lambda x: x.get("roi", 0), reverse=True)

    logger.info("GET /performance/by-league: %d leagues", len(breakdown))
    return {"data": breakdown, "total_leagues": len(breakdown)}


@router.get("/by-market", summary="Performance metrics broken down by market type")
async def performance_by_market() -> dict[str, Any]:
    """
    Return win/loss/ROI metrics grouped by betting market (1X2, BTTS, OU, etc.).
    """
    logger.info("GET /performance/by-market")
    try:
        rows = (
            supabase.table("results")
            .select(
                "outcome, profit_loss, clv,"
                "prediction:predictions!prediction_id(market, selection)"
            )
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("GET /performance/by-market: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        market = (row.get("prediction") or {}).get("market") or "UNKNOWN"
        grouped.setdefault(market, []).append(row)

    breakdown = []
    for market, res in grouped.items():
        metrics = _compute_metrics(res)
        breakdown.append({"market": market, **metrics})

    breakdown.sort(key=lambda x: x.get("roi", 0), reverse=True)

    logger.info("GET /performance/by-market: %d markets", len(breakdown))
    return {"data": breakdown, "total_markets": len(breakdown)}


@router.get("/clv", summary="CLV trend over time for charting")
async def clv_trend(
    days: int = Query(30, ge=7, le=365, description="Number of days of history to return"),
) -> dict[str, Any]:
    """
    Return daily average CLV (Closing Line Value) for the last *days* days.
    Each data point: { date: YYYY-MM-DD, avg_clv: float, count: int }
    Suitable for a time-series chart.
    CLV > 0 means the model consistently beat the closing line — a strong
    indicator of long-run edge.
    """
    logger.info("GET /performance/clv days=%d", days)
    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        rows = (
            supabase.table("results")
            .select("clv, recorded_at")
            .not_.is_("clv", "null")
            .gte("recorded_at", f"{cutoff}T00:00:00+00:00")
            .order("recorded_at")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("GET /performance/clv: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Group by date
    daily: dict[str, list[float]] = {}
    for row in rows:
        recorded = (row.get("recorded_at") or "")[:10]  # YYYY-MM-DD prefix
        if recorded:
            daily.setdefault(recorded, []).append(float(row.get("clv") or 0))

    trend = [
        {
            "date":    d,
            "avg_clv": round(sum(vals) / len(vals), 4),
            "count":   len(vals),
        }
        for d, vals in sorted(daily.items())
    ]

    overall_avg = (
        round(sum(p["avg_clv"] for p in trend) / len(trend), 4) if trend else None
    )

    logger.info("GET /performance/clv: %d data points over %d days", len(trend), days)
    return {
        "data":        trend,
        "days":        days,
        "overall_avg_clv": overall_avg,
        "interpretation": (
            "Positive CLV means the model beat the closing line on average — "
            "a reliable indicator of genuine edge."
        ),
    }
