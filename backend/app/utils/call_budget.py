"""
API call budget manager.

Tracks daily (API-Football) and monthly (Odds API) call counts against
limits stored in the api_call_budget Supabase table.

Usage
-----
    from app.utils.call_budget import can_call_api_football, record_api_call

    if await can_call_api_football():
        await record_api_call("api_football")
        # ... make the call
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional

from app.db.supabase_client import supabase
from app.utils.logger import logger

# ------------------------------------------------------------------ #
# Config — read once at import time
# ------------------------------------------------------------------ #
API_FOOTBALL_DAILY_LIMIT: int = int(os.getenv("API_FOOTBALL_DAILY_LIMIT", "100"))
ODDS_API_MONTHLY_LIMIT: int = int(os.getenv("ODDS_API_MONTHLY_LIMIT", "500"))

# ------------------------------------------------------------------ #
# Period helpers
# ------------------------------------------------------------------ #

def _today_key() -> str:
    """ISO date string for today (UTC), e.g. '2024-11-15'."""
    return date.today().isoformat()


def _month_key() -> str:
    """First day of the current UTC month as an ISO date, e.g. '2024-11-01'.
    Stored as a full date string so the api_call_budget date column (type date) accepts it.
    """
    now = datetime.now(tz=timezone.utc)
    return f"{now.year}-{now.month:02d}-01"


def _period_key(api_name: str) -> str:
    """Return the period key appropriate for *api_name*."""
    if api_name == "odds_api":
        return _month_key()
    return _today_key()


def _limit_for(api_name: str) -> int:
    if api_name == "api_football":
        return API_FOOTBALL_DAILY_LIMIT
    if api_name == "odds_api":
        return ODDS_API_MONTHLY_LIMIT
    return 0  # 0 = unlimited


# ------------------------------------------------------------------ #
# Low-level Supabase helpers (sync wrappers — Supabase Python SDK is sync)
# ------------------------------------------------------------------ #

def _fetch_row(api_name: str, period: str) -> Optional[dict]:
    """Return the budget row for *api_name* + *period*, or None.

    Note: supabase-py's maybe_single() returns None (not a response object)
    when no row is found, so we guard against that here.
    """
    response = (
        supabase.table("api_call_budget")
        .select("*")
        .eq("api_name", api_name)
        .eq("date", period)
        .maybe_single()
        .execute()
    )
    if response is None:
        return None
    return response.data


def _upsert_row(api_name: str, period: str, calls_used: int, calls_limit: int) -> None:
    supabase.table("api_call_budget").upsert(
        {
            "api_name": api_name,
            "date": period,
            "calls_used": calls_used,
            "calls_limit": calls_limit,
            "last_updated": datetime.now(tz=timezone.utc).isoformat(),
        },
        on_conflict="api_name,date",
    ).execute()


# ------------------------------------------------------------------ #
# Public async interface
# ------------------------------------------------------------------ #

async def can_call_api_football() -> bool:
    """
    Return True if API-Football daily budget has not been exhausted.
    Resets automatically because each day gets its own row keyed by today's date.
    """
    period = _today_key()
    row = _fetch_row("api_football", period)
    if row is None:
        logger.debug("can_call_api_football: no budget row yet — allowing call")
        return True
    remaining = API_FOOTBALL_DAILY_LIMIT - row["calls_used"]
    allowed = remaining > 0
    if not allowed:
        logger.warning(
            "API-Football daily limit reached: %d/%d",
            row["calls_used"],
            API_FOOTBALL_DAILY_LIMIT,
        )
    return allowed


async def can_call_odds_api() -> bool:
    """
    Return True if The Odds API monthly budget has not been exhausted.
    Resets automatically because each month gets its own row keyed by '2024-11'.
    """
    period = _month_key()
    row = _fetch_row("odds_api", period)
    if row is None:
        logger.debug("can_call_odds_api: no budget row yet — allowing call")
        return True
    remaining = ODDS_API_MONTHLY_LIMIT - row["calls_used"]
    allowed = remaining > 0
    if not allowed:
        logger.warning(
            "Odds API monthly limit reached: %d/%d",
            row["calls_used"],
            ODDS_API_MONTHLY_LIMIT,
        )
    return allowed


async def record_api_call(api_name: str) -> None:
    """
    Increment the call counter for *api_name* in the database.
    Creates the row if it does not exist yet.
    """
    period = _period_key(api_name)
    limit = _limit_for(api_name)

    row = _fetch_row(api_name, period)
    new_count = (row["calls_used"] + 1) if row else 1
    _upsert_row(api_name, period, new_count, limit)
    logger.debug("record_api_call: %s → %d/%d", api_name, new_count, limit)


async def get_remaining_calls(api_name: str) -> int:
    """
    Return the number of calls remaining for *api_name* in the current period.
    Returns -1 if the API has no configured limit (unlimited).
    """
    limit = _limit_for(api_name)
    if limit == 0:
        return -1  # unlimited

    period = _period_key(api_name)
    row = _fetch_row(api_name, period)
    used = row["calls_used"] if row else 0
    remaining = max(limit - used, 0)
    logger.debug("get_remaining_calls: %s → %d remaining", api_name, remaining)
    return remaining


# ------------------------------------------------------------------ #
# Fixture prioritisation
# ------------------------------------------------------------------ #

def prioritize_fixtures(fixtures: list[dict]) -> list[dict]:
    """
    Sort *fixtures* by a composite priority score and return the top 30.

    Priority rules (highest → lowest):
        1. Fixtures where odds are already cached   (+40 pts)
        2. Higher league trust_score                (+0–30 pts, scaled)
        3. Kickoff is more than 3 hours away        (+20 pts)
        4. H2H data is already cached               (+10 pts)

    Args:
        fixtures: List of fixture dicts.  Each dict may contain:
            - odds_cached        (bool)   — set by the caller
            - league_trust_score (float)  — 0.0–1.0
            - kickoff_utc        (str)    — ISO-8601 UTC timestamp
            - h2h_cached         (bool)   — set by the caller

    Returns:
        Sorted list of up to 30 fixtures with an added 'priority_score' key.
    """
    now_utc = datetime.now(tz=timezone.utc)

    def _score(f: dict) -> float:
        score = 0.0

        # 1. Odds already cached — most valuable (avoids an Odds API call)
        if f.get("odds_cached"):
            score += 40.0

        # 2. League trust score — scale 0–1 to 0–30 points
        trust = float(f.get("league_trust_score") or f.get("trust_score") or 0.5)
        score += trust * 30.0

        # 3. Kickoff > 3 h away — more time to refine predictions before lock
        kickoff_raw = f.get("kickoff_utc")
        if kickoff_raw:
            try:
                if isinstance(kickoff_raw, str):
                    kickoff = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00"))
                else:
                    kickoff = kickoff_raw
                hours_until = (kickoff - now_utc).total_seconds() / 3600
                if hours_until > 3:
                    score += 20.0
            except (ValueError, TypeError):
                logger.debug("prioritize_fixtures: could not parse kickoff_utc %r", kickoff_raw)

        # 4. H2H already cached — saves a separate API call
        if f.get("h2h_cached"):
            score += 10.0

        return score

    scored = [dict(f, priority_score=round(_score(f), 2)) for f in fixtures]
    scored.sort(key=lambda f: f["priority_score"], reverse=True)

    top = scored[:30]
    logger.info(
        "prioritize_fixtures: %d fixtures in → %d returned (top score=%.1f, bottom=%.1f)",
        len(fixtures),
        len(top),
        top[0]["priority_score"] if top else 0,
        top[-1]["priority_score"] if top else 0,
    )
    return top
