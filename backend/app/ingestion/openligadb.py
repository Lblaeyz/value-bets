"""
OpenLigaDB ingestion client — no API key required.
Base URL: https://api.openligadb.de
Docs:     https://github.com/sportschef/openligadb-json-api

Supported leagues
-----------------
bl1  Bundesliga        (supplements football-data.org BL1)
bl2  2. Bundesliga
bl3  3. Liga
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from app.db.supabase_client import supabase
from app.utils.logger import logger
from app.utils.rate_limiter import openligadb_limiter

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

BASE_URL = "https://api.openligadb.de"

SUPPORTED_LEAGUES: frozenset[str] = frozenset({"bl1", "bl2", "bl3"})

# OpenLigaDB match result type IDs
_RESULT_TYPE_FINAL = 2        # MatchResultTypeID 2 = final result
_RESULT_TYPE_HT    = 1        # MatchResultTypeID 1 = half-time

# Season boundary: new season starts on 1 July
_SEASON_CHANGE_MONTH = 7

# Data quality scores per source
_DATA_QUALITY_OPENLIGADB = 0.85

# ------------------------------------------------------------------ #
# HTTP helper — no auth headers needed
# ------------------------------------------------------------------ #

async def _get(path: str) -> list | dict:
    """
    Perform a GET against the OpenLigaDB JSON API with rate limiting.
    Returns the parsed JSON (list or dict).
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    url = f"{BASE_URL}{path}"
    await openligadb_limiter.acquire()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.RequestError as exc:
        logger.error("openligadb network error on %s: %s", path, exc)
        raise

    if response.status_code == 404:
        logger.warning("openligadb 404 on %s — empty or invalid resource", path)
        return []

    if response.status_code == 429:
        logger.error("openligadb 429 on %s — rate limit hit despite limiter; sleeping 30s", path)
        await asyncio.sleep(30)
        raise httpx.HTTPStatusError(
            f"openligadb: Rate limited (429) on {path}.",
            request=response.request,
            response=response,
        )

    if response.status_code >= 500:
        logger.error("openligadb server error %d on %s", response.status_code, path)
        response.raise_for_status()

    if response.status_code != 200:
        logger.error("openligadb unexpected %d on %s", response.status_code, path)
        response.raise_for_status()

    logger.debug("openligadb GET %s -> 200", path)
    return response.json()


# ------------------------------------------------------------------ #
# Season helper
# ------------------------------------------------------------------ #

def get_current_season() -> int:
    """
    Return the current season start year.

    OpenLigaDB uses the season *start* year:
      - Season 2024/25 → 2024
      - The new season begins on 1 July each calendar year.

    Examples:
        June 2025  → 2024  (still 2024/25 season)
        August 2025 → 2025  (2025/26 season has started)
    """
    now = datetime.now(tz=timezone.utc)
    if now.month >= _SEASON_CHANGE_MONTH:
        season = now.year
    else:
        season = now.year - 1
    logger.debug("get_current_season: %d (month=%d)", season, now.month)
    return season


# ------------------------------------------------------------------ #
# Team resolution helpers
# ------------------------------------------------------------------ #

def _get_or_create_team(team_name: str, team_openliga_id: int | None, league_id: int) -> int | None:
    """
    Look up a team by name or openligadb ID; insert if missing.
    Returns the internal teams.id, or None on failure.
    """
    # 1. Try lookup by name + league (most reliable for openligadb)
    _resp = (
        supabase.table("teams")
        .select("id")
        .eq("name", team_name)
        .eq("league_id", league_id)
        .maybe_single()
        .execute()
    )
    existing = _resp.data if _resp else None

    if existing:
        return existing["id"]

    # 2. Create the team
    try:
        result = (
            supabase.table("teams")
            .insert({
                "name": team_name,
                "league_id": league_id,
                "elo_rating": 1500.0,
            })
            .execute()
        )
        new_team = result.data[0] if result.data else None
        if new_team:
            logger.info("openligadb: created team '%s' (id=%d)", team_name, new_team["id"])
            return new_team["id"]
        # Insert succeeded but returned no data — re-fetch by name
        recheck = (
            supabase.table("teams")
            .select("id")
            .eq("name", team_name)
            .eq("league_id", league_id)
            .maybe_single()
            .execute()
        )
        if recheck and recheck.data:
            return recheck.data["id"]
        logger.warning(
            "openligadb: insert for team '%s' returned no data (result=%r)",
            team_name, result.data,
        )
    except Exception as exc:
        logger.error("openligadb: failed to create team '%s': %s", team_name, exc)

    return None


def _maybe_single_data(response) -> dict | None:
    """Safely extract .data from a maybe_single() response.

    supabase-py returns None (not a response object) when no row matches,
    so accessing .data directly on it raises AttributeError.
    """
    if response is None:
        return None
    return response.data


def _resolve_league_id(league_shortcut: str) -> int | None:
    """Return the internal leagues.id for an openligadb league shortcut."""
    # openligadb bl1/bl2/bl3 → football_data_code BL1 (bl2/bl3 have no fd code)
    openliga_to_fd = {"bl1": "BL1"}
    fd_code = openliga_to_fd.get(league_shortcut)

    if fd_code:
        row = _maybe_single_data(
            supabase.table("leagues")
            .select("id")
            .eq("football_data_code", fd_code)
            .maybe_single()
            .execute()
        )
        if row:
            return row["id"]

    # Fall back to openligadb_code column
    row = _maybe_single_data(
        supabase.table("leagues")
        .select("id")
        .eq("openligadb_code", league_shortcut)
        .maybe_single()
        .execute()
    )
    if row:
        return row["id"]

    logger.warning("openligadb: no league found for shortcut '%s'", league_shortcut)
    return None


# ------------------------------------------------------------------ #
# Parsing helpers
# ------------------------------------------------------------------ #

def _extract_goals(match: dict) -> tuple[int | None, int | None]:
    """
    Extract final-score goals from the matchResults list.
    OpenLigaDB returns camelCase field names.
    Returns (home_goals, away_goals) or (None, None) if not final.
    """
    results: list[dict] = match.get("matchResults") or []
    for r in results:
        if r.get("resultTypeID") == _RESULT_TYPE_FINAL:
            return r.get("pointsTeam1"), r.get("pointsTeam2")
    return None, None


def _parse_kickoff(match: dict) -> str | None:
    """Return ISO-8601 UTC kickoff string, or None."""
    raw: str | None = match.get("matchDateTimeUTC") or match.get("matchDateTime")
    if not raw:
        return None
    try:
        # OpenLigaDB returns strings like "2024-08-23T18:30:00Z" or without Z (UTC)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        logger.debug("openligadb: could not parse date %r", raw)
        return None


def _is_finished(match: dict) -> bool:
    """Return True if the match has a confirmed final result."""
    if not match.get("matchIsFinished", False):
        return False
    home_goals, away_goals = _extract_goals(match)
    return home_goals is not None and away_goals is not None


def _parse_match_to_fixture(
    match: dict,
    league_id: int,
) -> dict | None:
    """
    Map a raw OpenLigaDB match object to our fixtures table schema.
    Returns None if required fields are missing.
    OpenLigaDB uses camelCase: matchID, team1, team2, teamName, shortName.
    """
    openligadb_id: int | None = match.get("matchID")
    if not openligadb_id:
        return None

    team1 = match.get("team1") or {}
    team2 = match.get("team2") or {}
    team1_name: str = team1.get("teamName") or team1.get("shortName") or ""
    team2_name: str = team2.get("teamName") or team2.get("shortName") or ""
    team1_ol_id: int | None = team1.get("teamId")
    team2_ol_id: int | None = team2.get("teamId")

    if not team1_name or not team2_name:
        logger.debug("openligadb: match %d missing team names — skipping", openligadb_id)
        return None

    home_team_id = _get_or_create_team(team1_name, team1_ol_id, league_id)
    away_team_id = _get_or_create_team(team2_name, team2_ol_id, league_id)

    if not home_team_id or not away_team_id:
        logger.warning(
            "openligadb: match %d — could not resolve teams ('%s', '%s')",
            openligadb_id, team1_name, team2_name,
        )
        return None

    home_goals, away_goals = _extract_goals(match)
    finished = _is_finished(match)
    status = "FINISHED" if finished else "SCHEDULED"

    return {
        "openligadb_id": openligadb_id,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "league_id": league_id,
        "kickoff_utc": _parse_kickoff(match),
        "status": status,
        "home_goals": home_goals if finished else None,
        "away_goals": away_goals if finished else None,
        "data_quality_score": _DATA_QUALITY_OPENLIGADB,
        "source": "openligadb",
    }


def _parse_match_to_result(match: dict, league_id: int) -> dict | None:
    """
    Parse a finished match into a compact result dict for Poisson model input.
    OpenLigaDB uses camelCase field names.
    """
    if not _is_finished(match):
        return None

    home_goals, away_goals = _extract_goals(match)
    team1 = match.get("team1") or {}
    team2 = match.get("team2") or {}

    return {
        "openligadb_id": match.get("matchID"),
        "kickoff_utc": _parse_kickoff(match),
        "home_team_name": team1.get("teamName") or team1.get("shortName", ""),
        "away_team_name": team2.get("teamName") or team2.get("shortName", ""),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "league_id": league_id,
    }


# ------------------------------------------------------------------ #
# Public async functions
# ------------------------------------------------------------------ #

async def fetch_current_matches(league: str, season: int) -> list[dict]:
    """
    Fetch all matches for *league* and *season* from OpenLigaDB,
    parse them into our fixtures schema, and upsert into Supabase
    using openligadb_id as the unique conflict key.

    Args:
        league: League shortcut — one of 'bl1', 'bl2', 'bl3'.
        season: Season start year (e.g. 2024 for the 2024/25 season).

    Returns:
        List of upserted fixture dicts from Supabase.

    Raises:
        ValueError: If *league* is not in SUPPORTED_LEAGUES.
        httpx.HTTPStatusError: On unrecoverable API errors.
    """
    league = league.lower()
    if league not in SUPPORTED_LEAGUES:
        raise ValueError(
            f"fetch_current_matches: unsupported league {league!r}. "
            f"Supported: {sorted(SUPPORTED_LEAGUES)}"
        )

    logger.info("fetch_current_matches: league=%s season=%d", league, season)

    raw = await _get(f"/getmatchdata/{league}/{season}")
    matches: list[dict] = raw if isinstance(raw, list) else []

    logger.info(
        "fetch_current_matches: league=%s season=%d → %d raw matches from API",
        league, season, len(matches),
    )

    league_id = _resolve_league_id(league)
    if league_id is None:
        logger.error(
            "fetch_current_matches: league '%s' not in DB — cannot upsert fixtures", league
        )
        return []

    # ── Batch team resolution (avoid per-match roundtrips) ─────────── #
    # 1. Extract unique teams from raw match data
    seen_teams: dict[str, int | None] = {}  # teamName → openliga teamId
    for m in matches:
        for key in ("team1", "team2"):
            t = m.get(key) or {}
            name = t.get("teamName") or t.get("shortName") or ""
            if name and name not in seen_teams:
                seen_teams[name] = t.get("teamId")

    # 2. Fetch all existing teams for this league in one query
    existing_resp = (
        supabase.table("teams")
        .select("id, name")
        .eq("league_id", league_id)
        .execute()
    )
    team_cache: dict[str, int] = {
        row["name"]: row["id"] for row in (existing_resp.data or [])
    }

    # 3. Bulk-insert missing teams
    missing = [name for name in seen_teams if name not in team_cache]
    if missing:
        new_teams = [{"name": n, "league_id": league_id, "elo_rating": 1500.0} for n in missing]
        ins_resp = supabase.table("teams").insert(new_teams).execute()
        for row in (ins_resp.data or []):
            team_cache[row["name"]] = row["id"]
        # Re-fetch if insert returned no data
        if len(team_cache) < len(seen_teams):
            recheck = (
                supabase.table("teams")
                .select("id, name")
                .eq("league_id", league_id)
                .execute()
            )
            for row in (recheck.data or []):
                team_cache[row["name"]] = row["id"]
        logger.info(
            "fetch_current_matches: created %d new teams for %s", len(missing), league
        )

    # ── Parse using in-memory cache ─────────────────────────────────── #
    parsed: list[dict] = []
    for m in matches:
        t1 = m.get("team1") or {}
        t2 = m.get("team2") or {}
        name1 = t1.get("teamName") or t1.get("shortName") or ""
        name2 = t2.get("teamName") or t2.get("shortName") or ""
        ol_id = m.get("matchID")
        if not ol_id or not name1 or not name2:
            continue
        home_id = team_cache.get(name1)
        away_id = team_cache.get(name2)
        if not home_id or not away_id:
            logger.warning("fetch_current_matches: no team id for '%s' or '%s'", name1, name2)
            continue
        finished = _is_finished(m)
        home_goals, away_goals = _extract_goals(m)
        parsed.append({
            "openligadb_id": ol_id,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "league_id": league_id,
            "kickoff_utc": _parse_kickoff(m),
            "status": "FINISHED" if finished else "SCHEDULED",
            "home_goals": home_goals if finished else None,
            "away_goals": away_goals if finished else None,
            "data_quality_score": _DATA_QUALITY_OPENLIGADB,
        })

    logger.info(
        "fetch_current_matches: %d/%d matches parsed for %s %d",
        len(parsed), len(matches), league, season,
    )

    if not parsed:
        return []

    # Filter out fixtures already in the DB (partial index blocks ON CONFLICT)
    ol_ids = [r["openligadb_id"] for r in parsed]
    # Query in batches of 500 to avoid URL length limits
    existing_ids: set[int] = set()
    for i in range(0, len(ol_ids), 500):
        chunk = ol_ids[i:i + 500]
        ex_resp = (
            supabase.table("fixtures")
            .select("openligadb_id")
            .in_("openligadb_id", chunk)
            .execute()
        )
        existing_ids.update(row["openligadb_id"] for row in (ex_resp.data or []))

    new_rows = [r for r in parsed if r["openligadb_id"] not in existing_ids]

    if not new_rows:
        logger.info("fetch_current_matches: all %d fixtures already in DB", len(parsed))
        return parsed  # Return all so caller can filter by date

    # Insert in batches of 200
    upserted: list[dict] = []
    for i in range(0, len(new_rows), 200):
        batch = new_rows[i:i + 200]
        ins = supabase.table("fixtures").insert(batch).execute()
        upserted.extend(ins.data or [])

    logger.info(
        "fetch_current_matches: inserted %d new fixtures for %s %d",
        len(upserted), league, season,
    )
    return parsed  # Return all (including existing) so caller can filter by date


async def fetch_match_results(league: str, season: int) -> list[dict]:
    """
    Fetch completed match results for *league* and *season*.
    Filters raw API data to finished matches only and returns compact
    result dicts suitable for feeding the Poisson model.

    Args:
        league: League shortcut — one of 'bl1', 'bl2', 'bl3'.
        season: Season start year.

    Returns:
        List of result dicts ordered chronologically (oldest first), each with:
        openligadb_id, kickoff_utc, home_team_name, away_team_name,
        home_goals, away_goals, league_id.
    """
    league = league.lower()
    if league not in SUPPORTED_LEAGUES:
        raise ValueError(
            f"fetch_match_results: unsupported league {league!r}. "
            f"Supported: {sorted(SUPPORTED_LEAGUES)}"
        )

    logger.info("fetch_match_results: league=%s season=%d", league, season)

    raw = await _get(f"/getmatchdata/{league}/{season}")
    matches: list[dict] = raw if isinstance(raw, list) else []

    league_id = _resolve_league_id(league)

    results: list[dict] = []
    for m in matches:
        entry = _parse_match_to_result(m, league_id or 0)
        if entry:
            results.append(entry)

    # Oldest first for sequential model training
    results.sort(key=lambda r: r.get("kickoff_utc") or "")

    logger.info(
        "fetch_match_results: %d/%d completed results for %s %d",
        len(results), len(matches), league, season,
    )
    return results


async def fetch_all_supported_leagues(
    season: int | None = None,
    target_date: str | None = None,
) -> list[dict]:
    """
    Convenience wrapper — fetches and upserts fixtures for bl2 and bl3.

    Args:
        season:      Season start year. Derived from *target_date* if omitted,
                     else falls back to get_current_season().
        target_date: ISO date string, e.g. "2026-05-10".  When provided,
                     only fixtures on that calendar date are returned (all are
                     still upserted to keep the DB complete).

    Returns:
        Combined list of upserted fixture dicts, filtered to *target_date* if given.
    """
    if season is None:
        if target_date:
            # Derive season from target_date using the same July-boundary rule
            dt = datetime.fromisoformat(target_date)
            season = dt.year if dt.month >= _SEASON_CHANGE_MONTH else dt.year - 1
        else:
            season = get_current_season()

    logger.info(
        "fetch_all_supported_leagues: season=%d (bl2 + bl3)%s",
        season,
        f" target_date={target_date}" if target_date else "",
    )

    # Run bl2 and bl3 concurrently — each has its own rate limiter slot
    bl2_fixtures, bl3_fixtures = await asyncio.gather(
        fetch_current_matches("bl2", season),
        fetch_current_matches("bl3", season),
        return_exceptions=False,
    )

    combined: list[dict] = []
    combined.extend(bl2_fixtures if isinstance(bl2_fixtures, list) else [])
    combined.extend(bl3_fixtures if isinstance(bl3_fixtures, list) else [])

    # Filter to target_date when performing a backfill
    if target_date:
        on_date = [
            f for f in combined
            if (f.get("kickoff_utc") or "").startswith(target_date)
        ]
        logger.info(
            "fetch_all_supported_leagues: filtered to %d fixtures on %s",
            len(on_date), target_date,
        )
        if not on_date:
            return []
        # Re-fetch from DB so fixtures carry their DB `id` (required by Poisson model)
        ol_ids = [f["openligadb_id"] for f in on_date if f.get("openligadb_id")]
        if not ol_ids:
            return []
        db_resp = (
            supabase.table("fixtures")
            .select("*")
            .in_("openligadb_id", ol_ids)
            .execute()
        )
        db_rows = db_resp.data or []
        logger.info(
            "fetch_all_supported_leagues: re-fetched %d full fixture rows from DB",
            len(db_rows),
        )
        return db_rows
    else:
        logger.info(
            "fetch_all_supported_leagues: season=%d → %d total fixtures (bl2=%d, bl3=%d)",
            season,
            len(combined),
            len(bl2_fixtures) if isinstance(bl2_fixtures, list) else 0,
            len(bl3_fixtures) if isinstance(bl3_fixtures, list) else 0,
        )
        return combined
