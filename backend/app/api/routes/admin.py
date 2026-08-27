"""
Admin-only endpoints.
Protect with ADMIN_API_KEY environment variable before going live.
All mutating endpoints require X-Admin-Key header.
"""
from __future__ import annotations

import os
from secrets import compare_digest
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from app.db.supabase_client import supabase
from app.db.queries import get_all_leagues
from app.scheduler.jobs import scheduler
from app.utils.logger import logger

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")


# ------------------------------------------------------------------ #
# Auth helper
# ------------------------------------------------------------------ #

def _require_admin(key: str) -> None:
    if not _ADMIN_KEY or not key or not compare_digest(key, _ADMIN_KEY):
        raise HTTPException(status_code=403, detail="Forbidden — invalid admin key")


# ------------------------------------------------------------------ #
# Request/response models
# ------------------------------------------------------------------ #

class ResultRecord(BaseModel):
    fixture_id:   int
    prediction_id: int
    outcome:      str = Field(..., description="WIN | LOSS | VOID | PUSH")
    closing_odds: float | None = None
    profit_loss:  float        = 0.0
    notes:        str | None   = None


class UpdateResultsRequest(BaseModel):
    results: list[ResultRecord]


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@router.get("/budget", summary="Today's API call budget status")
async def get_budget_status(x_admin_key: str = Header(default="")) -> list[dict]:
    """Return today's and this month's API call counts per provider."""
    _require_admin(x_admin_key)
    today = date.today().isoformat()
    month = f"{date.today().year}-{date.today().month:02d}"

    # odds_api uses first day of month as its date key (valid date type in DB)
    first_of_month = f"{date.today().year}-{date.today().month:02d}-01"
    try:
        response = (
            supabase.table("api_call_budget")
            .select("*")
            .in_("date", [today, first_of_month])
            .order("api_name")
            .order("date", desc=True)
            .execute()
        )
        return response.data or []
    except Exception as exc:
        logger.error("GET /admin/budget: DB error — %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")


@router.get("/leagues", summary="List all configured leagues")
async def list_leagues(x_admin_key: str = Header(default="")) -> list[dict]:
    _require_admin(x_admin_key)
    try:
        return get_all_leagues()
    except Exception as exc:
        logger.error("GET /admin/leagues: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")


@router.get("/scheduler/jobs", summary="List all scheduled jobs and next run times")
async def list_jobs(x_admin_key: str = Header(default="")) -> list[dict]:
    _require_admin(x_admin_key)
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id":            job.id,
            "name":          job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger":       str(job.trigger),
        })
    logger.info("GET /admin/scheduler/jobs: %d jobs", len(jobs))
    return jobs


@router.post("/scheduler/run/{job_id}", summary="Manually trigger a scheduled job")
async def run_job(job_id: str, x_admin_key: str = Header(default="")) -> dict:
    """Fire a specific scheduler job immediately by its ID."""
    _require_admin(x_admin_key)
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    logger.info("POST /admin/scheduler/run/%s: manual trigger", job_id)
    scheduler.modify_job(job_id, next_run_time=None)
    job.func()
    return {"triggered": job_id, "status": "fired"}


@router.post("/run-pipeline", summary="Manually trigger the full daily pipeline")
async def run_pipeline(x_admin_key: str = Header(default="")) -> dict[str, Any]:
    """
    Execute the complete daily pipeline for today and return its summary.
    Times out after 300 seconds.
    """
    _require_admin(x_admin_key)
    logger.info("POST /admin/run-pipeline: manual trigger")

    import asyncio
    from app.scheduler.jobs import daily_pipeline

    try:
        summary = await asyncio.wait_for(daily_pipeline(), timeout=300)
        logger.info("POST /admin/run-pipeline: completed successfully")
        return {"status": "completed", "summary": summary}
    except asyncio.TimeoutError:
        logger.error("POST /admin/run-pipeline: timed out after 300s")
        raise HTTPException(status_code=504, detail="Pipeline timed out after 300 seconds")
    except Exception as exc:
        logger.error("POST /admin/run-pipeline: %s — %s", type(exc).__name__, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


class BackfillRequest(BaseModel):
    date: str = Field(..., description="ISO date to backfill, e.g. '2026-05-10'")


# ------------------------------------------------------------------ #
# Seed DB — insert leagues + pull teams from standings
# ------------------------------------------------------------------ #

# Master list of leagues the pipeline supports
_LEAGUES_SEED = [
    # football-data.org competitions
    {"name": "Premier League",   "country": "England", "football_data_code": "PL",  "openligadb_code": None},
    {"name": "La Liga",          "country": "Spain",   "football_data_code": "PD",  "openligadb_code": None},
    {"name": "Serie A",          "country": "Italy",   "football_data_code": "SA",  "openligadb_code": None},
    {"name": "Bundesliga",       "country": "Germany", "football_data_code": "BL1", "openligadb_code": "bl1"},
    {"name": "Primeira Liga",    "country": "Portugal","football_data_code": "PPL", "openligadb_code": None},
    {"name": "Brasileirao",      "country": "Brazil",  "football_data_code": "BSA", "openligadb_code": None},
    # OpenLigaDB-only
    {"name": "2. Bundesliga",    "country": "Germany", "football_data_code": None,  "openligadb_code": "bl2"},
    {"name": "3. Liga",          "country": "Germany", "football_data_code": None,  "openligadb_code": "bl3"},
]


@router.post("/seed-db", summary="Seed leagues table and pull teams from standings API")
async def seed_db(x_admin_key: str = Header(default="")) -> dict[str, Any]:
    """
    One-time setup: insert all supported leagues and pull teams from
    football-data.org standings.  Safe to re-run — uses upsert logic.
    """
    _require_admin(x_admin_key)
    import asyncio
    from app.ingestion.football_data import fetch_standings

    result: dict[str, Any] = {
        "leagues_upserted": 0,
        "leagues_errors": [],
        "teams_inserted": 0,
        "teams_skipped": 0,
        "teams_errors": [],
        "standings_fetched": [],
        "standings_failed": [],
    }

    # ── Step 1: Insert leagues (skip if already present) ──────────── #
    for league in _LEAGUES_SEED:
        try:
            # Check existence by name (cheapest unique-enough query)
            existing = (
                supabase.table("leagues")
                .select("id")
                .eq("name", league["name"])
                .maybe_single()
                .execute()
            )
            if existing and existing.data:
                result["leagues_upserted"] += 1  # already there
                continue
            # Build row — omit None values except explicit nulls we want to store
            row: dict = {"name": league["name"], "country": league["country"]}
            if league.get("football_data_code"):
                row["football_data_code"] = league["football_data_code"]
            if league.get("openligadb_code"):
                row["openligadb_code"] = league["openligadb_code"]
            resp = supabase.table("leagues").insert(row).execute()
            if resp.data:
                result["leagues_upserted"] += 1
        except Exception as exc:
            result["leagues_errors"].append(f"{league['name']}: {exc}")
            logger.error("seed-db: insert league '%s' failed — %s", league["name"], exc)

    # ── Step 2: Pull teams via standings for fd-backed leagues ──────── #
    fd_codes = [lg["football_data_code"] for lg in _LEAGUES_SEED if lg["football_data_code"]]

    async def _seed_league_teams(fd_code: str) -> None:
        try:
            standings = await fetch_standings(fd_code)
            if not standings:
                result["standings_failed"].append(f"{fd_code}: empty standings")
                return

            # Resolve league_id
            lr = (
                supabase.table("leagues")
                .select("id")
                .eq("football_data_code", fd_code)
                .maybe_single()
                .execute()
            )
            league_id = (lr.data or {}).get("id") if lr else None
            if not league_id:
                result["standings_failed"].append(f"{fd_code}: league not in DB after upsert")
                return

            for entry in standings:
                fd_team_id = entry.get("team_fd_id")
                team_name  = entry.get("team_name", "")
                if not team_name:
                    continue
                try:
                    # Check if team exists
                    existing = (
                        supabase.table("teams")
                        .select("id")
                        .eq("football_data_id", fd_team_id)
                        .maybe_single()
                        .execute()
                    )
                    if existing and existing.data:
                        result["teams_skipped"] += 1
                        continue
                    # Insert new team
                    supabase.table("teams").insert({
                        "name": team_name,
                        "league_id": league_id,
                        "football_data_id": fd_team_id,
                        "elo_rating": 1500.0,
                    }).execute()
                    result["teams_inserted"] += 1
                except Exception as exc:
                    result["teams_errors"].append(f"{fd_code}/{team_name}: {exc}")

            result["standings_fetched"].append(fd_code)
        except Exception as exc:
            result["standings_failed"].append(f"{fd_code}: {exc}")
            logger.error("seed-db: standings fetch failed for %s — %s", fd_code, exc)

    await asyncio.gather(*[_seed_league_teams(c) for c in fd_codes])

    logger.info(
        "seed-db: done — %d leagues, %d teams inserted, %d skipped, %d errors",
        result["leagues_upserted"], result["teams_inserted"],
        result["teams_skipped"], len(result["teams_errors"]),
    )
    return result


@router.post("/backfill", summary="Run the full pipeline for a historical date")
async def backfill(
    body: BackfillRequest,
    x_admin_key: str = Header(default=""),
) -> dict[str, Any]:
    """
    Fetch fixtures, run the Poisson model, and generate recommendations for
    a specific historical date.  Useful for testing the engine end-to-end
    when today has no live fixtures (e.g. summer break).

    This is a long-running operation — budget 30–120 s.
    Times out after 300 seconds.
    """
    _require_admin(x_admin_key)

    try:
        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid date format — use YYYY-MM-DD, e.g. '2026-05-10'",
        )

    logger.info("POST /admin/backfill: date=%s", body.date)

    import asyncio
    from app.scheduler.jobs import daily_pipeline

    try:
        summary = await asyncio.wait_for(
            daily_pipeline(target_date=body.date), timeout=300
        )
        logger.info("POST /admin/backfill: completed for %s", body.date)
        return {"status": "completed", "date": body.date, "summary": summary}
    except asyncio.TimeoutError:
        logger.error("POST /admin/backfill: timed out after 300s for date=%s", body.date)
        raise HTTPException(status_code=504, detail="Backfill timed out after 300 seconds")
    except Exception as exc:
        logger.error(
            "POST /admin/backfill: %s — %s", type(exc).__name__, exc, exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/update-results", summary="Record outcomes and settle predictions")
async def update_results(
    body: UpdateResultsRequest,
    x_admin_key: str = Header(default=""),
) -> dict[str, Any]:
    """
    Record final outcomes for a batch of predictions.

    For each record:
    1. Validates outcome value.
    2. Inserts a row into the results table with profit/loss.
    3. Computes CLV = recommended_odds - closing_odds (positive = beat closing line).
    4. Updates the parent prediction status (WON / LOST / VOID).

    Args (per result record):
        fixture_id:    Internal fixtures.id.
        prediction_id: Internal predictions.id.
        outcome:       WIN | LOSS | VOID | PUSH
        closing_odds:  Final market odds at kick-off (for CLV calculation).
        profit_loss:   Realised P&L in stake units (positive = profit).
    """
    _require_admin(x_admin_key)

    valid_outcomes = {"WIN", "LOSS", "VOID", "PUSH"}
    now = datetime.now(tz=timezone.utc).isoformat()

    settled: list[dict] = []
    errors:  list[dict] = []

    for rec in body.results:
        outcome = rec.outcome.upper()
        if outcome not in valid_outcomes:
            errors.append({
                "prediction_id": rec.prediction_id,
                "error": f"Invalid outcome {rec.outcome!r}. Must be one of {sorted(valid_outcomes)}",
            })
            continue

        # Fetch the prediction to get recommended_odds for CLV
        try:
            pred_row = (
                supabase.table("predictions")
                .select("id, recommended_odds, fixture_id")
                .eq("id", rec.prediction_id)
                .maybe_single()
                .execute()
            ).data
        except Exception as exc:
            errors.append({"prediction_id": rec.prediction_id, "error": str(exc)})
            continue

        if not pred_row:
            errors.append({
                "prediction_id": rec.prediction_id,
                "error": "Prediction not found",
            })
            continue

        # CLV = recommended_odds - closing_odds  (positive means we got better price)
        clv: float | None = None
        if rec.closing_odds and pred_row.get("recommended_odds"):
            clv = round(float(pred_row["recommended_odds"]) - float(rec.closing_odds), 4)

        # Brier contribution placeholder (requires model_probability — stored on prediction)
        brier: float | None = None
        try:
            p_row = (
                supabase.table("predictions")
                .select("model_probability")
                .eq("id", rec.prediction_id)
                .maybe_single()
                .execute()
            ).data
            if p_row:
                p = float(p_row.get("model_probability") or 0)
                actual = 1.0 if outcome == "WIN" else 0.0
                brier = round((p - actual) ** 2, 6)
        except Exception:
            pass

        # Insert results row
        try:
            result_insert = (
                supabase.table("results")
                .insert({
                    "prediction_id":    rec.prediction_id,
                    "fixture_id":       rec.fixture_id,
                    "outcome":          outcome,
                    "profit_loss":      rec.profit_loss,
                    "closing_odds":     rec.closing_odds,
                    "clv":              clv,
                    "brier_contribution": brier,
                    "recorded_at":      now,
                })
                .execute()
            )
        except Exception as exc:
            errors.append({"prediction_id": rec.prediction_id, "error": f"Insert failed: {exc}"})
            continue

        # Update prediction status
        status_map = {"WIN": "WON", "LOSS": "LOST", "VOID": "VOID", "PUSH": "VOID"}
        try:
            supabase.table("predictions").update(
                {"status": status_map[outcome]}
            ).eq("id", rec.prediction_id).execute()
        except Exception as exc:
            logger.warning(
                "update_results: prediction %d status update failed — %s",
                rec.prediction_id, exc,
            )

        settled.append({
            "prediction_id": rec.prediction_id,
            "outcome":       outcome,
            "profit_loss":   rec.profit_loss,
            "clv":           clv,
            "brier":         brier,
        })

        logger.info(
            "update_results: prediction=%d outcome=%s pl=%.3f clv=%s",
            rec.prediction_id, outcome, rec.profit_loss, clv,
        )

    logger.info(
        "POST /admin/update-results: settled=%d errors=%d",
        len(settled), len(errors),
    )

    return {
        "settled": len(settled),
        "errors":  len(errors),
        "detail":  {
            "settled_records": settled,
            "error_records":   errors,
        },
    }
