"""
API-Football (api-sports.io) ingestion client — v3.
Base URL: https://v3.football.api-sports.io
Docs:     https://www.api-football.com/documentation-v3

BUDGET CRITICAL: 100 calls per day on the free tier.
Every public function MUST:
  1. Call can_call_api_football() before making any request.
  2. Call record_api_call("api_football") after every successful request.
  3. Log remaining calls so over-spend is visible in logs.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.db.supabase_client import supabase
from app.utils.call_budget import (
    can_call_api_football,
    get_remaining_calls,
    record_api_call,
)
from app.utils.logger import logger
from app.utils.rate_limiter import api_football_limiter

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

API_KEY: str = os.getenv("API_FOOTBALL_KEY", "")
BASE_URL: str = "https://v3.football.api-sports.io"

_H2H_CACHE_DAYS    = 30
_STATS_CACHE_DAYS  = 7
_BUDGET_WARNING_AT = 20   # warn when fewer than this many calls remain


# ------------------------------------------------------------------ #
# HTTP helper
# ------------------------------------------------------------------ #

def _headers() -> dict[str, str]:
    if not API_KEY:
        logger.warning("API_FOOTBALL_KEY is not set — all requests will be rejected with 401")
    return {
        "x-apisports-key": API_KEY,
        "Accept": "application/json",
    }


def _error_message(status_code: int, path: str) -> str:
    messages = {
        401: f"api-football 401 on {path}: Unauthorised. Check API_FOOTBALL_KEY.",
        403: f"api-football 403 on {path}: Forbidden. Endpoint may require a paid plan.",
        404: f"api-football 404 on {path}: Resource not found.",
        429: f"api-football 429 on {path}: Hard rate limit hit — check daily cap.",
        499: f"api-football: Budget exhausted before request to {path}.",
        500: f"api-football 500 on {path}: Server error. Retry later.",
    }
    return messages.get(status_code, f"api-football HTTP {status_code} on {path}.")


async def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    """
    Authenticated GET with rate limiter applied.
    Does NOT check the budget — callers must do that before calling _get().
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    url = f"{BASE_URL}{path}"
    await api_football_limiter.acquire()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, params=params, headers=_headers())
    except httpx.RequestError as exc:
        logger.error("api-football network error on %s: %s", path, exc)
        raise

    if response.status_code != 200:
        msg = _error_message(response.status_code, path)
        logger.error(msg)
        response.raise_for_status()

    logger.debug("api-football GET %s -> 200", path)
    return response.json()


async def _log_budget() -> None:
    """Log remaining daily calls; emit a warning when running low."""
    remaining = await get_remaining_calls("api_football")
    if remaining < 0:
        logger.debug("api-football budget: unlimited")
        return
    if remaining <= _BUDGET_WARNING_AT:
        logger.warning(
            "api-football budget LOW: %d calls remaining today", remaining
        )
    else:
        logger.info("api-football budget: %d calls remaining today", remaining)


async def _guard_budget(context: str) -> bool:
    """
    Check budget and log the outcome.
    Returns True if the call is allowed, False if the budget is exhausted.
    """
    allowed = await can_call_api_football()
    if not allowed:
        logger.error(
            "api-football budget EXHAUSTED — skipping %s. "
            "Budget resets at midnight UTC.",
            context,
        )
    return allowed


# ------------------------------------------------------------------ #
# 1. Injuries
# ------------------------------------------------------------------ #

def _parse_injury(raw: dict, fixture_id: int, team_id_internal: int | None) -> dict | None:
    """Map a raw api-football injury object to our injuries table schema."""
    try:
        player = raw.get("player") or {}
        injury = raw.get("type") or {}  # some responses nest the injury type here
        # v3 schema: player.name, player.type, player.reason, team.id
        player_name: str = player.get("name") or ""
        injury_type: str = (
            player.get("type")
            or (raw.get("injury") or {}).get("type")
            or ""
        )
        reason: str = (
            player.get("reason")
            or (raw.get("injury") or {}).get("reason")
            or ""
        )
        raw_status: str = player.get("status") or raw.get("status") or "Doubtful"

        # Normalise status to our enum
        status_map = {
            "out":      "OUT",
            "doubtful": "DOUBTFUL",
            "questionable": "DOUBTFUL",
            "available": "AVAILABLE",
        }
        status = status_map.get(raw_status.lower(), "DOUBTFUL")

        if not player_name:
            return None

        return {
            "fixture_id":  fixture_id,
            "team_id":     team_id_internal,
            "player_name": player_name,
            "injury_type": f"{injury_type} — {reason}".strip(" —") or None,
            "status":      status,
            "return_date": None,
            "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    except (KeyError, TypeError) as exc:
        logger.debug("_parse_injury: skipping row — %s", exc)
        return None


def _resolve_internal_team_id(api_team_id: int) -> int | None:
    """Look up our internal teams.id from an api-football team ID."""
    row = (
        supabase.table("teams")
        .select("id")
        .eq("api_football_id", api_team_id)
        .maybe_single()
        .execute()
    ).data
    return row["id"] if row else None


async def fetch_injuries(fixture_id: int, api_fixture_id: str) -> list[dict]:
    """
    Fetch injury report for a fixture from api-football.

    Args:
        fixture_id:     Our internal fixtures.id.
        api_fixture_id: The api-football fixture ID string.

    Returns:
        List of stored injury dicts, or [] if budget is exhausted.
    """
    logger.info("fetch_injuries: fixture_id=%d api_fixture_id=%s", fixture_id, api_fixture_id)

    if not await _guard_budget(f"fetch_injuries(fixture={api_fixture_id})"):
        return []

    raw = await _get("/injuries", params={"fixture": api_fixture_id})
    await record_api_call("api_football")
    await _log_budget()

    response_list: list[dict] = raw.get("response") or []
    logger.info(
        "fetch_injuries: fixture=%s → %d raw injury records", api_fixture_id, len(response_list)
    )

    parsed: list[dict] = []
    for entry in response_list:
        api_team_id: int | None = (entry.get("team") or {}).get("id")
        internal_team_id = _resolve_internal_team_id(api_team_id) if api_team_id else None

        injury = _parse_injury(entry, fixture_id, internal_team_id)
        if injury:
            parsed.append(injury)

    if not parsed:
        return []

    # Delete stale records for this fixture before re-inserting
    supabase.table("injuries").delete().eq("fixture_id", fixture_id).execute()
    result = supabase.table("injuries").insert(parsed).execute()
    stored: list[dict] = result.data or []

    logger.info(
        "fetch_injuries: stored %d injuries for fixture_id=%d", len(stored), fixture_id
    )
    return stored


# ------------------------------------------------------------------ #
# 2. Lineups
# ------------------------------------------------------------------ #

def _parse_lineup_players(
    players: list[dict],
    fixture_id: int,
    team_id: int,
    is_starter: bool,
) -> list[dict]:
    """Parse a list of lineup player objects into our lineups schema."""
    rows: list[dict] = []
    for entry in players:
        player = entry.get("player") or {}
        name: str = player.get("name") or ""
        if not name:
            continue
        rows.append({
            "fixture_id":   fixture_id,
            "team_id":      team_id,
            "player_name":  name,
            "position":     player.get("pos") or None,
            "is_starter":   is_starter,
            "recorded_at":  datetime.now(tz=timezone.utc).isoformat(),
        })
    return rows


async def fetch_lineups(fixture_id: int, api_fixture_id: str) -> list[dict]:
    """
    Fetch confirmed lineups (starters + subs) for a fixture.

    Args:
        fixture_id:     Our internal fixtures.id.
        api_fixture_id: The api-football fixture ID string.

    Returns:
        List of stored lineup dicts, or [] if budget is exhausted or lineups
        are not yet available.
    """
    logger.info("fetch_lineups: fixture_id=%d api_fixture_id=%s", fixture_id, api_fixture_id)

    if not await _guard_budget(f"fetch_lineups(fixture={api_fixture_id})"):
        return []

    raw = await _get("/fixtures/lineups", params={"fixture": api_fixture_id})
    await record_api_call("api_football")
    await _log_budget()

    response_list: list[dict] = raw.get("response") or []

    if not response_list:
        logger.info(
            "fetch_lineups: fixture=%s — lineups not yet available", api_fixture_id
        )
        return []

    all_rows: list[dict] = []
    for team_block in response_list:
        team_info = team_block.get("team") or {}
        api_team_id: int | None = team_info.get("id")
        internal_team_id = _resolve_internal_team_id(api_team_id) if api_team_id else None

        if not internal_team_id:
            logger.warning(
                "fetch_lineups: api_team_id=%s not found in DB — skipping", api_team_id
            )
            continue

        starters: list[dict] = team_block.get("startXI") or []
        subs:     list[dict] = team_block.get("substitutes") or []

        all_rows.extend(_parse_lineup_players(starters, fixture_id, internal_team_id, is_starter=True))
        all_rows.extend(_parse_lineup_players(subs,     fixture_id, internal_team_id, is_starter=False))

    logger.info(
        "fetch_lineups: fixture_id=%d → %d players parsed (%d starters, %d subs)",
        fixture_id,
        len(all_rows),
        sum(1 for r in all_rows if r["is_starter"]),
        sum(1 for r in all_rows if not r["is_starter"]),
    )

    if not all_rows:
        return []

    # Replace stale lineups for this fixture
    supabase.table("lineups").delete().eq("fixture_id", fixture_id).execute()
    result = supabase.table("lineups").insert(all_rows).execute()
    stored: list[dict] = result.data or []

    logger.info("fetch_lineups: stored %d lineup rows for fixture_id=%d", len(stored), fixture_id)
    return stored


# ------------------------------------------------------------------ #
# 3. Head-to-Head (with 30-day cache)
# ------------------------------------------------------------------ #

def _h2h_cache_hit(db_home_id: int, db_away_id: int) -> dict | None:
    """
    Return cached h2h data if it exists and was fetched within the last 30 days.
    Returns None on cache miss or expired cache.
    """
    now_utc = datetime.now(tz=timezone.utc)
    cutoff  = (now_utc - timedelta(days=_H2H_CACHE_DAYS)).isoformat()

    row = (
        supabase.table("h2h_cache")
        .select("*")
        .eq("home_team_id", db_home_id)
        .eq("away_team_id", db_away_id)
        .gt("cached_at", cutoff)
        .maybe_single()
        .execute()
    ).data

    if row:
        logger.info(
            "fetch_h2h: CACHE HIT for teams %d vs %d (cached_at=%s)",
            db_home_id, db_away_id, row.get("cached_at"),
        )
    return row


def _store_h2h_cache(db_home_id: int, db_away_id: int, data: list[dict]) -> None:
    """Upsert h2h_cache row with a 30-day expiry."""
    now_utc = datetime.now(tz=timezone.utc)
    expires = (now_utc + timedelta(days=_H2H_CACHE_DAYS)).isoformat()

    supabase.table("h2h_cache").upsert(
        {
            "home_team_id": db_home_id,
            "away_team_id": db_away_id,
            "data":         data,
            "cached_at":    now_utc.isoformat(),
            "expires_at":   expires,
        },
        on_conflict="home_team_id,away_team_id",
    ).execute()
    logger.info(
        "fetch_h2h: cached %d H2H matches for teams %d vs %d (expires %s)",
        len(data), db_home_id, db_away_id, expires[:10],
    )


def _parse_h2h_match(match: dict) -> dict:
    """Convert a raw api-football match object to a compact H2H entry."""
    score  = match.get("score") or {}
    ft     = score.get("fulltime") or {}
    teams  = match.get("teams") or {}
    home   = teams.get("home") or {}
    away   = teams.get("away") or {}

    return {
        "api_fixture_id": match.get("fixture", {}).get("id"),
        "date":           (match.get("fixture") or {}).get("date"),
        "home_team":      home.get("name") or "",
        "away_team":      away.get("name") or "",
        "home_goals":     ft.get("home"),
        "away_goals":     ft.get("away"),
        "status":         (match.get("fixture") or {}).get("status", {}).get("short"),
    }


async def fetch_h2h(
    home_team_id: str,
    away_team_id: str,
    db_home_id: int,
    db_away_id: int,
) -> list[dict]:
    """
    Return last 10 head-to-head matches between two teams.
    Serves from the 30-day h2h_cache table before touching the API.

    Args:
        home_team_id:  api-football team ID for the home side.
        away_team_id:  api-football team ID for the away side.
        db_home_id:    Our internal teams.id for the home side.
        db_away_id:    Our internal teams.id for the away side.

    Returns:
        List of H2H match dicts (up to 10), newest first.
        Returns cached data without an API call when cache is valid.
        Returns [] if budget is exhausted and no cache exists.
    """
    logger.info(
        "fetch_h2h: api_teams=%s-%s db_teams=%d-%d",
        home_team_id, away_team_id, db_home_id, db_away_id,
    )

    # ── Cache check (saves budget over time) ──────────────────────── #
    cached = _h2h_cache_hit(db_home_id, db_away_id)
    if cached:
        data = cached.get("data") or []
        logger.info("fetch_h2h: returning %d matches from cache", len(data))
        return data if isinstance(data, list) else []

    logger.info("fetch_h2h: CACHE MISS — checking budget for API call")

    # ── Budget guard ──────────────────────────────────────────────── #
    if not await _guard_budget(f"fetch_h2h({home_team_id}-{away_team_id})"):
        return []

    raw = await _get(
        "/fixtures/headtohead",
        params={"h2h": f"{home_team_id}-{away_team_id}", "last": 10},
    )
    await record_api_call("api_football")
    await _log_budget()

    response_list: list[dict] = raw.get("response") or []
    logger.info(
        "fetch_h2h: %d raw H2H matches from API for %s-%s",
        len(response_list), home_team_id, away_team_id,
    )

    parsed = [_parse_h2h_match(m) for m in response_list]

    # Store in cache regardless of result count
    _store_h2h_cache(db_home_id, db_away_id, parsed)

    return parsed


# ------------------------------------------------------------------ #
# 4. Team Statistics (with 7-day cache via h2h_cache pattern)
# ------------------------------------------------------------------ #

def _stats_cache_hit(api_team_id: str, api_league_id: str, season: int) -> dict | None:
    """
    Return cached team statistics if fetched within the last 7 days.
    Uses a synthetic cache key stored in h2h_cache as a single-team row
    (home_team_id = away_team_id = internal_team_id, data = stats blob).
    Falls back to a stats-specific lookup via a dedicated query.
    """
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=_STATS_CACHE_DAYS)).isoformat()

    # Use the h2h_cache table with a sentinel: home=away=0, data keyed by composite
    # We store the stats under a synthetic "team_stats_{api_team_id}_{api_league_id}_{season}"
    # marker in the h2h_cache.data JSONB field using home=0 / away=api_team_id.
    # Simpler: use a separate upsert keyed by (home_team_id=0, away_team_id=api_team_id).
    try:
        sentinel_away = int(api_team_id)
    except (ValueError, TypeError):
        return None

    row = (
        supabase.table("h2h_cache")
        .select("*")
        .eq("home_team_id", 0)
        .eq("away_team_id", sentinel_away)
        .gt("cached_at", cutoff)
        .maybe_single()
        .execute()
    ).data

    if row:
        data = row.get("data") or {}
        # Verify the cache is for the correct league/season (different leagues
        # may share the same team ID)
        if (
            isinstance(data, dict)
            and data.get("league_id") == api_league_id
            and data.get("season") == season
        ):
            logger.info(
                "fetch_team_statistics: CACHE HIT for team=%s league=%s season=%d",
                api_team_id, api_league_id, season,
            )
            return data

    return None


def _store_stats_cache(api_team_id: str, api_league_id: str, season: int, stats: dict) -> None:
    """Persist team statistics in h2h_cache for 7 days."""
    now_utc = datetime.now(tz=timezone.utc)
    expires = (now_utc + timedelta(days=_STATS_CACHE_DAYS)).isoformat()
    try:
        sentinel_away = int(api_team_id)
    except (ValueError, TypeError):
        return

    payload = dict(stats, league_id=api_league_id, season=season)

    supabase.table("h2h_cache").upsert(
        {
            "home_team_id": 0,
            "away_team_id": sentinel_away,
            "data":         payload,
            "cached_at":    now_utc.isoformat(),
            "expires_at":   expires,
        },
        on_conflict="home_team_id,away_team_id",
    ).execute()
    logger.info(
        "fetch_team_statistics: cached stats for team=%s league=%s season=%d (expires %s)",
        api_team_id, api_league_id, season, expires[:10],
    )


def _parse_team_statistics(raw_stats: dict) -> dict:
    """Extract the fields most useful for the Poisson model."""
    fixtures  = raw_stats.get("fixtures") or {}
    goals     = raw_stats.get("goals") or {}
    goals_for = goals.get("for") or {}
    goals_ag  = goals.get("against") or {}

    gf_avg = (goals_for.get("average") or {})
    ga_avg = (goals_ag.get("average") or {})

    biggest   = raw_stats.get("biggest") or {}
    lineups_s = raw_stats.get("lineups") or []

    # Possession: may be nested under "ball_possession"
    possession = raw_stats.get("ball_possession") or raw_stats.get("possession") or {}

    # xG may not be on free tier — include if present, None if not
    biggest_s  = biggest.get("streak") or {}

    return {
        "played_home":          (fixtures.get("played") or {}).get("home", 0),
        "played_away":          (fixtures.get("played") or {}).get("away", 0),
        "wins_home":            (fixtures.get("wins") or {}).get("home", 0),
        "wins_away":            (fixtures.get("wins") or {}).get("away", 0),
        "avg_goals_for_home":   _safe_float(gf_avg.get("home")),
        "avg_goals_for_away":   _safe_float(gf_avg.get("away")),
        "avg_goals_for_total":  _safe_float(gf_avg.get("total")),
        "avg_goals_ag_home":    _safe_float(ga_avg.get("home")),
        "avg_goals_ag_away":    _safe_float(ga_avg.get("away")),
        "avg_goals_ag_total":   _safe_float(ga_avg.get("total")),
        "win_streak":           biggest_s.get("wins", 0),
        "lose_streak":          biggest_s.get("loses", 0),
        "draw_streak":          biggest_s.get("draws", 0),
        "most_used_formation":  lineups_s[0].get("formation") if lineups_s else None,
        "possession_avg":       _safe_float(possession.get("average") or possession.get("total")),
        "clean_sheets_total":   (raw_stats.get("clean_sheet") or {}).get("total", 0),
        "failed_to_score":      (raw_stats.get("failed_to_score") or {}).get("total", 0),
    }


def _safe_float(val: Any) -> float | None:
    """Safely cast a value to float, returning None on failure."""
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


async def fetch_team_statistics(
    api_team_id: str,
    api_league_id: str,
    season: int,
) -> dict:
    """
    Fetch aggregated team statistics (goals, win rate, form) for use by
    the Poisson model. Serves from a 7-day cache before touching the API.

    Args:
        api_team_id:    api-football team ID string.
        api_league_id:  api-football league ID string.
        season:         Season year (e.g. 2024).

    Returns:
        Dict of parsed statistics, or {} if budget is exhausted and no cache.
    """
    logger.info(
        "fetch_team_statistics: team=%s league=%s season=%d",
        api_team_id, api_league_id, season,
    )

    # ── 7-day cache check ─────────────────────────────────────────── #
    cached = _stats_cache_hit(api_team_id, api_league_id, season)
    if cached:
        logger.info(
            "fetch_team_statistics: returning cached stats for team=%s", api_team_id
        )
        return cached

    logger.info("fetch_team_statistics: CACHE MISS — checking budget for API call")

    # ── Budget guard ──────────────────────────────────────────────── #
    if not await _guard_budget(
        f"fetch_team_statistics(team={api_team_id}, league={api_league_id}, season={season})"
    ):
        return {}

    raw = await _get(
        "/teams/statistics",
        params={"team": api_team_id, "league": api_league_id, "season": season},
    )
    await record_api_call("api_football")
    await _log_budget()

    raw_stats: dict = raw.get("response") or {}
    if not raw_stats:
        logger.warning(
            "fetch_team_statistics: empty response for team=%s league=%s season=%d",
            api_team_id, api_league_id, season,
        )
        return {}

    parsed = _parse_team_statistics(raw_stats)
    logger.info(
        "fetch_team_statistics: parsed stats for team=%s "
        "(avg_gf=%.2f avg_ga=%.2f possession=%s)",
        api_team_id,
        parsed.get("avg_goals_for_total") or 0,
        parsed.get("avg_goals_ag_total") or 0,
        parsed.get("possession_avg"),
    )

    _store_stats_cache(api_team_id, api_league_id, season, parsed)
    return parsed
