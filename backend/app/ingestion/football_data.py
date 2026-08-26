"""
football-data.org ingestion client — free tier.
Docs: https://www.football-data.org/documentation/quickstart

Supported competitions
----------------------
PL   Premier League (England)
ELC  Championship (England)
PD   La Liga (Spain)
SA   Serie A (Italy)
BL1  Bundesliga (Germany)
FL1  Ligue 1 (France)
PPL  Primeira Liga (Portugal)
DED  Eredivisie (Netherlands)
BSA  Brasileirao (Brazil)
CL   UEFA Champions League
EL   UEFA Europa League
EC   European Championship
"""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx

from app.db.supabase_client import supabase
from app.utils.logger import logger
from app.utils.rate_limiter import football_data_limiter

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

API_KEY: str = os.getenv("FOOTBALL_DATA_API_KEY", "")
BASE_URL: str = "https://api.football-data.org/v4"

SUPPORTED_COMPETITIONS: frozenset[str] = frozenset({
    "PL",   # Premier League (England)
    "ELC",  # Championship (England)
    "PD",   # La Liga (Spain)
    "SA",   # Serie A (Italy)
    "BL1",  # Bundesliga (Germany)
    "FL1",  # Ligue 1 (France)
    "PPL",  # Primeira Liga (Portugal)
    "DED",  # Eredivisie (Netherlands)
    "BSA",  # Brasileirao (Brazil)
    "CL",   # UEFA Champions League
    "EL",   # UEFA Europa League
    "EC",   # European Championship
})

# Status values from the API that mean the match is finished
_FINISHED_STATUSES = {"FINISHED", "AWARDED"}

# Exponential backoff config for 429 responses
_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0   # seconds — doubles each attempt


# ------------------------------------------------------------------ #
# HTTP helpers
# ------------------------------------------------------------------ #

def _headers() -> dict[str, str]:
    if not API_KEY:
        logger.warning("FOOTBALL_DATA_API_KEY is not set — requests will be rejected")
    return {
        "X-Auth-Token": API_KEY,
        "Accept": "application/json",
    }


def _error_message(status_code: int, path: str) -> str:
    messages = {
        401: f"football-data.org: Unauthorised (401) on {path}. Check FOOTBALL_DATA_API_KEY.",
        403: f"football-data.org: Forbidden (403) on {path}. Competition may not be on your plan.",
        404: f"football-data.org: Not found (404) on {path}. Resource does not exist.",
        429: f"football-data.org: Rate limited (429) on {path}. Backing off.",
        500: f"football-data.org: Server error (500) on {path}. Retry later.",
    }
    return messages.get(status_code, f"football-data.org: HTTP {status_code} on {path}.")


async def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    """
    Perform an authenticated GET against the football-data.org v4 API.

    Applies the shared rate limiter and retries with exponential backoff
    on HTTP 429.  Raises httpx.HTTPStatusError for unrecoverable errors.
    """
    url = f"{BASE_URL}{path}"
    attempt = 0

    while attempt <= _MAX_RETRIES:
        await football_data_limiter.acquire()

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(url, params=params, headers=_headers())
        except httpx.RequestError as exc:
            logger.error("football-data network error on %s: %s", path, exc)
            raise

        if response.status_code == 429:
            wait = _BACKOFF_BASE ** attempt
            logger.warning(
                "football-data 429 on %s (attempt %d/%d). Sleeping %.1fs.",
                path, attempt + 1, _MAX_RETRIES, wait,
            )
            if attempt == _MAX_RETRIES:
                raise httpx.HTTPStatusError(
                    _error_message(429, path),
                    request=response.request,
                    response=response,
                )
            await asyncio.sleep(wait)
            attempt += 1
            continue

        if response.status_code != 200:
            msg = _error_message(response.status_code, path)
            logger.error(msg)
            response.raise_for_status()

        logger.debug("football-data GET %s -> 200 (attempt=%d)", path, attempt + 1)
        return response.json()

    # Should never be reached
    raise RuntimeError(f"Exhausted retries for {path}")


# ------------------------------------------------------------------ #
# Parsing helpers
# ------------------------------------------------------------------ #

def _parse_fixture(match: dict) -> dict | None:
    """
    Map a raw football-data.org match object to our fixtures table schema.
    Returns None if the match lacks required fields.
    """
    try:
        competition_code: str = match["competition"]["code"]
        football_data_id: int = match["id"]

        home_team = match.get("homeTeam") or {}
        away_team = match.get("awayTeam") or {}
        home_fd_id = home_team.get("id")
        away_fd_id = away_team.get("id")

        if not home_fd_id or not away_fd_id:
            return None

        # Resolve internal team IDs from Supabase
        _home_resp = (
            supabase.table("teams")
            .select("id")
            .eq("football_data_id", home_fd_id)
            .maybe_single()
            .execute()
        )
        home_row = _home_resp.data if _home_resp else None
        _away_resp = (
            supabase.table("teams")
            .select("id")
            .eq("football_data_id", away_fd_id)
            .maybe_single()
            .execute()
        )
        away_row = _away_resp.data if _away_resp else None

        if not home_row or not away_row:
            logger.debug(
                "Skipping match %d — teams %d/%d not in DB yet",
                football_data_id, home_fd_id, away_fd_id,
            )
            return None

        _league_resp = (
            supabase.table("leagues")
            .select("id")
            .eq("football_data_code", competition_code)
            .maybe_single()
            .execute()
        )
        league_row = _league_resp.data if _league_resp else None
        if not league_row:
            logger.debug("Skipping match %d — league %s not in DB", football_data_id, competition_code)
            return None

        score = match.get("score") or {}
        full_time = score.get("fullTime") or {}
        status: str = match.get("status", "SCHEDULED")
        kickoff_raw: str | None = match.get("utcDate")
        kickoff_utc = kickoff_raw if kickoff_raw else None

        # Map football-data statuses to our enum
        status_map = {
            "SCHEDULED": "SCHEDULED",
            "TIMED": "SCHEDULED",
            "IN_PLAY": "LIVE",
            "PAUSED": "LIVE",
            "FINISHED": "FINISHED",
            "AWARDED": "FINISHED",
            "POSTPONED": "POSTPONED",
            "CANCELLED": "CANCELLED",
            "SUSPENDED": "POSTPONED",
        }
        mapped_status = status_map.get(status, "SCHEDULED")

        return {
            "football_data_id": football_data_id,
            "home_team_id": home_row["id"],
            "away_team_id": away_row["id"],
            "league_id": league_row["id"],
            "kickoff_utc": kickoff_utc,
            "status": mapped_status,
            "home_goals": full_time.get("home") if mapped_status == "FINISHED" else None,
            "away_goals": full_time.get("away") if mapped_status == "FINISHED" else None,
            "data_quality_score": 0.9,  # football-data.org is a trusted source
        }

    except (KeyError, TypeError) as exc:
        logger.warning("_parse_fixture: failed to parse match — %s | raw=%s", exc, match.get("id"))
        return None


def _parse_team_match(match: dict) -> dict | None:
    """Parse a single match from /teams/{id}/matches into a compact form."""
    try:
        score = match.get("score") or {}
        full_time = score.get("fullTime") or {}
        home_goals = full_time.get("home")
        away_goals = full_time.get("away")

        if home_goals is None or away_goals is None:
            return None

        home_id = (match.get("homeTeam") or {}).get("id")
        home_name = (match.get("homeTeam") or {}).get("shortName") or (match.get("homeTeam") or {}).get("name", "")
        away_name = (match.get("awayTeam") or {}).get("shortName") or (match.get("awayTeam") or {}).get("name", "")

        return {
            "football_data_id": match["id"],
            "competition_code": (match.get("competition") or {}).get("code", ""),
            "kickoff_utc": match.get("utcDate"),
            "home_team_id_fd": home_id,
            "home_team_name": home_name,
            "away_team_name": away_name,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "status": match.get("status", "FINISHED"),
        }
    except (KeyError, TypeError) as exc:
        logger.warning("_parse_team_match: skipping match — %s", exc)
        return None


# ------------------------------------------------------------------ #
# Public async functions
# ------------------------------------------------------------------ #

async def fetch_todays_fixtures(target_date: str | None = None) -> list[dict]:
    """
    Pull all fixtures scheduled for *target_date* (or today if omitted) from
    football-data.org, filter to our supported competitions, parse, and upsert.

    Args:
        target_date: ISO date string, e.g. "2026-05-10".  Defaults to today.

    Returns:
        List of upserted fixture dicts in our internal schema format.
    """
    today_str = target_date or date.today().isoformat()
    logger.info("fetch_todays_fixtures: fetching for date=%s", today_str)

    raw = await _get("/matches", params={"dateFrom": today_str, "dateTo": today_str})

    matches: list[dict] = raw.get("matches") or []
    logger.info("fetch_todays_fixtures: %d total matches from API", len(matches))

    # Filter to supported competitions
    filtered = [
        m for m in matches
        if (m.get("competition") or {}).get("code") in SUPPORTED_COMPETITIONS
    ]

    # Count per competition for logging
    per_competition: dict[str, int] = defaultdict(int)
    for m in filtered:
        code = (m.get("competition") or {}).get("code", "UNKNOWN")
        per_competition[code] += 1
    for code, count in sorted(per_competition.items()):
        logger.info("fetch_todays_fixtures: %s → %d fixtures", code, count)

    # Parse
    parsed: list[dict] = []
    for m in filtered:
        fixture = _parse_fixture(m)
        if fixture:
            parsed.append(fixture)

    logger.info(
        "fetch_todays_fixtures: %d/%d fixtures parsed successfully",
        len(parsed), len(filtered),
    )

    if not parsed:
        logger.info("fetch_todays_fixtures: nothing to upsert")
        return []

    # Filter out fixtures already in the DB (partial index blocks ON CONFLICT)
    fd_ids = [r["football_data_id"] for r in parsed if r.get("football_data_id")]
    if fd_ids:
        existing_resp = (
            supabase.table("fixtures")
            .select("football_data_id")
            .in_("football_data_id", fd_ids)
            .execute()
        )
        existing_ids = {row["football_data_id"] for row in (existing_resp.data or [])}
        new_rows = [r for r in parsed if r.get("football_data_id") not in existing_ids]
    else:
        new_rows = parsed

    if not new_rows:
        logger.info("fetch_todays_fixtures: all fixtures already in DB — skipping insert")
        return []

    response = supabase.table("fixtures").insert(new_rows).execute()
    upserted: list[dict] = response.data or []
    logger.info("fetch_todays_fixtures: inserted %d new rows into fixtures table", len(upserted))
    return upserted


async def fetch_team_form(team_id: int, limit: int = 10) -> list[dict]:
    """
    Fetch the last *limit* completed matches for a football-data.org team ID.
    Used by the Poisson model to derive attack / defence strength.

    Args:
        team_id: football-data.org internal team ID.
        limit:   Number of recent matches to return (default 10).

    Returns:
        List of parsed match dicts ordered most-recent-first, each containing:
        football_data_id, kickoff_utc, home_team_name, away_team_name,
        home_goals, away_goals, competition_code.
    """
    logger.info("fetch_team_form: team_id=%d limit=%d", team_id, limit)

    raw = await _get(
        f"/teams/{team_id}/matches",
        params={"status": "FINISHED", "limit": min(limit * 2, 100)},
    )

    matches: list[dict] = raw.get("matches") or []
    logger.info("fetch_team_form: team_id=%d → %d raw matches from API", team_id, len(matches))

    parsed: list[dict] = []
    for m in matches:
        if (m.get("status") or "") not in _FINISHED_STATUSES:
            continue
        entry = _parse_team_match(m)
        if entry:
            parsed.append(entry)

    # Most recent first; respect limit
    parsed.sort(key=lambda m: m.get("kickoff_utc") or "", reverse=True)
    result = parsed[:limit]

    logger.info(
        "fetch_team_form: team_id=%d → returning %d completed matches",
        team_id, len(result),
    )
    return result


async def fetch_standings(competition_code: str) -> list[dict]:
    """
    Fetch the current league table for *competition_code* and return a
    list of standing rows with: position, team_name, team_fd_id,
    played, won, draw, lost, goals_for, goals_against, goal_diff, points.

    Also stores/updates the league's standing data in a 'standings' key
    on the leagues row if the league exists in Supabase.

    Args:
        competition_code: One of the SUPPORTED_COMPETITIONS codes.

    Returns:
        List of standing entry dicts (one per team, ordered 1st to last).

    Raises:
        ValueError: If *competition_code* is not in SUPPORTED_COMPETITIONS.
        httpx.HTTPStatusError: On unrecoverable API errors.
    """
    if competition_code not in SUPPORTED_COMPETITIONS:
        raise ValueError(
            f"fetch_standings: unsupported competition code {competition_code!r}. "
            f"Choose from: {sorted(SUPPORTED_COMPETITIONS)}"
        )

    logger.info("fetch_standings: competition=%s", competition_code)

    raw = await _get(f"/competitions/{competition_code}/standings")

    standings_data: list[dict] = raw.get("standings") or []
    # football-data returns TOTAL / HOME / AWAY tables; we want TOTAL
    total_table: list[dict] = []
    for block in standings_data:
        if block.get("type") == "TOTAL":
            total_table = block.get("table") or []
            break

    if not total_table:
        logger.warning("fetch_standings: no TOTAL table found for %s", competition_code)
        return []

    parsed_rows: list[dict] = []
    for entry in total_table:
        team = entry.get("team") or {}
        parsed_rows.append({
            "position": entry.get("position"),
            "team_name": team.get("shortName") or team.get("name", ""),
            "team_fd_id": team.get("id"),
            "played": entry.get("playedGames", 0),
            "won": entry.get("won", 0),
            "draw": entry.get("draw", 0),
            "lost": entry.get("lost", 0),
            "goals_for": entry.get("goalsFor", 0),
            "goals_against": entry.get("goalsAgainst", 0),
            "goal_diff": entry.get("goalDifference", 0),
            "points": entry.get("points", 0),
        })

    logger.info(
        "fetch_standings: %s → %d teams in table (leader: %s, %d pts)",
        competition_code,
        len(parsed_rows),
        parsed_rows[0]["team_name"] if parsed_rows else "?",
        parsed_rows[0]["points"] if parsed_rows else 0,
    )

    # Persist standings snapshot on the league row (best-effort)
    try:
        _lr = (
            supabase.table("leagues")
            .select("id")
            .eq("football_data_code", competition_code)
            .maybe_single()
            .execute()
        )
        league_row = _lr.data if _lr else None
        if league_row:
            supabase.table("leagues").update(
                {"trust_score": 0.9}  # Reaffirm quality on fresh data
            ).eq("id", league_row["id"]).execute()
    except Exception as exc:
        logger.warning("fetch_standings: could not update league row — %s", exc)

    return parsed_rows
