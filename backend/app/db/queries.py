"""
Central query helpers — thin wrappers around the Supabase client.
All database access should go through this module.
"""
from __future__ import annotations

from typing import Any, Optional
from app.db.supabase_client import supabase
from app.utils.logger import logger


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

def get_upcoming_fixtures(limit: int = 50) -> list[dict]:
    response = (
        supabase.table("fixtures")
        .select("*, home_team:teams!home_team_id(name), away_team:teams!away_team_id(name), league:leagues(name, country)")
        .eq("status", "SCHEDULED")
        .order("kickoff_utc", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def get_fixture_by_id(fixture_id: int) -> Optional[dict]:
    response = (
        supabase.table("fixtures")
        .select("*")
        .eq("id", fixture_id)
        .maybe_single()
        .execute()
    )
    return response.data


# ------------------------------------------------------------------ #
# Predictions
# ------------------------------------------------------------------ #

def get_predictions(
    status: Optional[str] = None,
    fixture_id: Optional[int] = None,
    limit: int = 100,
) -> list[dict]:
    query = supabase.table("predictions").select("*")
    if status:
        query = query.eq("status", status)
    if fixture_id:
        query = query.eq("fixture_id", fixture_id)
    response = query.order("created_at", desc=True).limit(limit).execute()
    return response.data or []


def upsert_prediction(payload: dict) -> dict:
    """
    Insert a prediction, skipping if (fixture_id, market, selection) already exists.
    Uses filter-before-insert because PostgREST can't resolve partial unique indexes
    via on_conflict= or ignore_duplicates=True.
    """
    fixture_id = payload.get("fixture_id")
    market = payload.get("market")
    selection = payload.get("selection")

    if fixture_id and market and selection:
        existing = (
            supabase.table("predictions")
            .select("id")
            .eq("fixture_id", fixture_id)
            .eq("market", market)
            .eq("selection", selection)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            # Already exists — return the existing row id without re-inserting
            return existing.data

    response = supabase.table("predictions").insert(payload).execute()
    return response.data[0] if response.data else {}


# ------------------------------------------------------------------ #
# Performance
# ------------------------------------------------------------------ #

def get_performance_summary(
    period: Optional[str] = None,
    league_id: Optional[int] = None,
) -> list[dict]:
    query = supabase.table("performance_summary").select("*")
    if period:
        query = query.eq("period", period)
    if league_id:
        query = query.eq("league_id", league_id)
    response = query.order("period", desc=True).execute()
    return response.data or []


# ------------------------------------------------------------------ #
# API call budget
# ------------------------------------------------------------------ #

def get_budget(api_name: str) -> Optional[dict]:
    from datetime import date
    response = (
        supabase.table("api_call_budget")
        .select("*")
        .eq("api_name", api_name)
        .eq("date", date.today().isoformat())
        .maybe_single()
        .execute()
    )
    return response.data


def increment_budget(api_name: str, calls_limit: int = 0) -> None:
    from datetime import date
    today = date.today().isoformat()
    existing = get_budget(api_name)
    if existing:
        supabase.table("api_call_budget").update(
            {"calls_used": existing["calls_used"] + 1, "last_updated": "now()"}
        ).eq("id", existing["id"]).execute()
    else:
        supabase.table("api_call_budget").insert(
            {"api_name": api_name, "date": today, "calls_used": 1, "calls_limit": calls_limit}
        ).execute()
    logger.debug("Budget incremented for %s", api_name)


# ------------------------------------------------------------------ #
# Odds
# ------------------------------------------------------------------ #

def insert_odds(records: list[dict]) -> None:
    if not records:
        return
    supabase.table("odds").insert(records).execute()
    logger.debug("Inserted %d odds records", len(records))


# ------------------------------------------------------------------ #
# Leagues
# ------------------------------------------------------------------ #

def get_all_leagues() -> list[dict]:
    response = supabase.table("leagues").select("*").order("name").execute()
    return response.data or []
