"""
The Odds API ingestion client — v4.
Base URL: https://api.the-odds-api.com/v4
Docs:     https://the-odds-api.com/liveapi/guides/v4/

BUDGET CRITICAL: ~500 calls per month (~16 per day).
Enforcement rules (all checked in code):
  1. can_call_odds_api() must return True before any request.
  2. Odds already fetched within the last 6 hours are served from Supabase.
  3. Only fixtures with data_quality_score >= 0.60 are eligible.
  4. Remaining monthly calls are logged from API response headers after
     every successful request.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.db.supabase_client import supabase
from app.utils.call_budget import can_call_odds_api, get_remaining_calls, record_api_call
from app.utils.logger import logger
from app.utils.rate_limiter import odds_api_limiter

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

API_KEY: str = os.getenv("ODDS_API_KEY", "")
BASE_URL: str = "https://api.the-odds-api.com/v4"

SUPPORTED_MARKETS = "h2h,totals,asian_totals,btts"
REGIONS           = "eu,uk"
ODDS_FORMAT       = "decimal"
DATE_FORMAT       = "iso"
SPORT_KEY         = "soccer"

_ODDS_CACHE_HOURS    = 6      # Re-use Supabase odds if < 6 h old
_MIN_DATA_QUALITY    = 0.60   # Skip fixtures below this threshold
_BUDGET_WARNING_AT   = 50     # Warn when monthly budget drops below this


# ------------------------------------------------------------------ #
# Utility: implied probability
# ------------------------------------------------------------------ #

def parse_implied_probability(odds_decimal: float | None) -> float:
    """
    Convert decimal odds to raw implied probability (1 / odds).

    Edge cases handled:
      - None          → 0.0
      - 0 or negative → 0.0  (mathematically undefined)
      - < 1.0         → 0.0  (invalid odds — would imply certainty > 100 %)
      - Very large    → rounds to 6 decimal places

    Args:
        odds_decimal: Decimal odds (e.g. 2.50).

    Returns:
        Implied probability as a float in [0.0, 1.0].
    """
    if odds_decimal is None:
        return 0.0
    try:
        val = float(odds_decimal)
    except (ValueError, TypeError):
        return 0.0
    if val < 1.0:
        logger.debug("parse_implied_probability: invalid odds %s → 0.0", odds_decimal)
        return 0.0
    if val == 0.0:
        return 0.0
    return round(1.0 / val, 6)


# ------------------------------------------------------------------ #
# HTTP helper
# ------------------------------------------------------------------ #

def _headers() -> dict[str, str]:
    if not API_KEY:
        logger.warning("ODDS_API_KEY is not set — all requests will fail")
    return {"Accept": "application/json"}


def _error_message(status_code: int, path: str) -> str:
    messages = {
        401: f"odds-api 401 on {path}: Unauthorised. Check ODDS_API_KEY.",
        403: f"odds-api 403 on {path}: Forbidden. Check plan limits.",
        422: f"odds-api 422 on {path}: Unprocessable — invalid parameters.",
        429: f"odds-api 429 on {path}: Hard rate limit hit.",
        500: f"odds-api 500 on {path}: Server error. Retry later.",
    }
    return messages.get(status_code, f"odds-api HTTP {status_code} on {path}.")


def _log_headers(headers: httpx.Headers, path: str) -> None:
    """Extract and log the quota headers returned by the Odds API."""
    remaining = headers.get("x-requests-remaining", "?")
    used      = headers.get("x-requests-used", "?")
    last_used = headers.get("x-requests-last", "?")

    remaining_int: int | None = None
    try:
        remaining_int = int(remaining)
    except (ValueError, TypeError):
        pass

    if remaining_int is not None and remaining_int <= _BUDGET_WARNING_AT:
        logger.warning(
            "odds-api BUDGET LOW: %s remaining this month (used=%s, last_cost=%s) [%s]",
            remaining, used, last_used, path,
        )
    else:
        logger.info(
            "odds-api budget: %s remaining this month (used=%s, last_cost=%s) [%s]",
            remaining, used, last_used, path,
        )


async def _get(path: str, params: dict[str, Any] | None = None) -> tuple[list | dict, httpx.Headers]:
    """
    Authenticated GET with rate limiter.
    Returns (parsed_json, response_headers).
    Does NOT check budget — callers must do that.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    url = f"{BASE_URL}{path}"
    query = dict(params or {})
    query["apiKey"] = API_KEY

    await odds_api_limiter.acquire()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, params=query, headers=_headers())
    except httpx.RequestError as exc:
        logger.error("odds-api network error on %s: %s", path, exc)
        raise

    if response.status_code != 200:
        msg = _error_message(response.status_code, path)
        logger.error(msg)
        response.raise_for_status()

    logger.debug("odds-api GET %s -> 200", path)
    return response.json(), response.headers


# ------------------------------------------------------------------ #
# Cache check: recent odds in Supabase
# ------------------------------------------------------------------ #

def _recent_odds_from_db(fixture_id: int) -> list[dict]:
    """
    Return odds rows from the last 6 hours for *fixture_id*.
    Returns [] on cache miss.
    """
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=_ODDS_CACHE_HOURS)).isoformat()

    response = (
        supabase.table("odds")
        .select("*")
        .eq("fixture_id", fixture_id)
        .gt("recorded_at", cutoff)
        .execute()
    )
    rows: list[dict] = response.data or []

    if rows:
        logger.info(
            "odds cache HIT: %d rows for fixture_id=%d (< %dh old)",
            len(rows), fixture_id, _ODDS_CACHE_HOURS,
        )
    return rows


# ------------------------------------------------------------------ #
# Parsing helpers
# ------------------------------------------------------------------ #

def _market_label(market_key: str) -> str:
    """Normalise Odds API market keys to our internal labels."""
    return {
        "h2h":           "1X2",
        "totals":        "OU",
        "asian_totals":  "AOU",
        "btts":          "BTTS",
    }.get(market_key, market_key.upper())


def _parse_outcome(
    outcome: dict,
    fixture_id: int,
    bookmaker: str,
    market_key: str,
    recorded_at: str,
) -> dict | None:
    """Map a single outcome object to our odds table schema."""
    name: str  = outcome.get("name") or ""
    price: Any = outcome.get("price")
    point: Any = outcome.get("point")  # for totals/asian lines

    if not name or price is None:
        return None

    odds_decimal = float(price)
    if odds_decimal < 1.0:
        logger.debug(
            "_parse_outcome: skipping invalid odds %.4f for %s/%s/%s",
            odds_decimal, bookmaker, market_key, name,
        )
        return None

    # Build a composite selection label: "over_2.5", "home", "yes", etc.
    if point is not None:
        selection = f"{name.lower().replace(' ', '_')}_{point}"
    else:
        selection = name.lower().replace(" ", "_")

    return {
        "fixture_id":         fixture_id,
        "bookmaker":          bookmaker,
        "market":             _market_label(market_key),
        "selection":          selection,
        "odds_decimal":       round(odds_decimal, 4),
        "implied_probability": parse_implied_probability(odds_decimal),
        "recorded_at":        recorded_at,
    }


def _match_fixture_to_event(event: dict, fixture: dict) -> bool:
    """
    Return True if an Odds API event plausibly corresponds to *fixture*.
    Matching strategy: kickoff date ± 1 day AND team name substring overlap.
    """
    kickoff_raw: str | None = fixture.get("kickoff_utc")
    if not kickoff_raw:
        return False

    try:
        kickoff = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00"))
    except ValueError:
        return False

    event_date_raw: str | None = event.get("commence_time")
    if not event_date_raw:
        return False

    try:
        event_date = datetime.fromisoformat(event_date_raw.replace("Z", "+00:00"))
    except ValueError:
        return False

    # Date must be within 1 day
    if abs((event_date - kickoff).total_seconds()) > 86_400:
        return False

    # Team name matching — Odds API uses full names; DB may have short names
    home_name: str = (fixture.get("home_team") or {}).get("name") or fixture.get("home_team_name", "")
    away_name: str = (fixture.get("away_team") or {}).get("name") or fixture.get("away_team_name", "")
    event_home: str = event.get("home_team", "")
    event_away: str = event.get("away_team", "")

    def _overlap(a: str, b: str) -> bool:
        a, b = a.lower(), b.lower()
        return a[:5] in b or b[:5] in a

    return _overlap(home_name, event_home) and _overlap(away_name, event_away)


def _parse_event_odds(event: dict, fixture_id: int) -> list[dict]:
    """Parse all bookmakers and markets from a single Odds API event."""
    recorded_at = datetime.now(tz=timezone.utc).isoformat()
    rows: list[dict] = []

    for bk in event.get("bookmakers") or []:
        bookmaker: str = bk.get("key") or bk.get("title") or "unknown"
        for market in bk.get("markets") or []:
            market_key: str = market.get("key") or ""
            for outcome in market.get("outcomes") or []:
                row = _parse_outcome(outcome, fixture_id, bookmaker, market_key, recorded_at)
                if row:
                    rows.append(row)

    return rows


# ------------------------------------------------------------------ #
# Public async functions
# ------------------------------------------------------------------ #

async def fetch_odds_for_fixture(fixture: dict) -> list[dict]:
    """
    Return odds records for a single fixture.

    Flow:
      1. Return cached odds from Supabase if < 6 hours old.
      2. Check monthly budget.
      3. Reject fixture if data_quality_score < 0.60.
      4. Call /sports/soccer/odds, match event to fixture, parse, store.

    Args:
        fixture: A fixture dict containing at minimum:
                 fixture_id (int), kickoff_utc (str),
                 data_quality_score (float),
                 home_team.name / away_team.name (or equivalent).

    Returns:
        List of odds row dicts. Empty list if skipped for any reason.
    """
    fixture_id: int = fixture.get("id") or fixture.get("fixture_id") or 0
    dq_score: float = float(fixture.get("data_quality_score") or 0.0)
    kickoff_raw: str | None = fixture.get("kickoff_utc")

    logger.info(
        "fetch_odds_for_fixture: fixture_id=%d dq=%.2f kickoff=%s",
        fixture_id, dq_score, kickoff_raw,
    )

    # ── 1. Supabase 6-hour cache ──────────────────────────────────── #
    cached = _recent_odds_from_db(fixture_id)
    if cached:
        return cached

    # ── 2. Data quality gate ──────────────────────────────────────── #
    if dq_score < _MIN_DATA_QUALITY:
        logger.info(
            "fetch_odds_for_fixture: fixture_id=%d skipped — dq_score %.2f < %.2f",
            fixture_id, dq_score, _MIN_DATA_QUALITY,
        )
        return []

    # ── 3. Budget check ───────────────────────────────────────────── #
    if not await can_call_odds_api():
        logger.error(
            "fetch_odds_for_fixture: odds-api budget EXHAUSTED — skipping fixture_id=%d. "
            "Budget resets on the 1st of next month.",
            fixture_id,
        )
        return []

    # ── 4. Determine the sport event date for the API query ───────── #
    event_date: str | None = None
    if kickoff_raw:
        try:
            dt = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00"))
            event_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    params: dict[str, Any] = {
        "regions":    REGIONS,
        "markets":    SUPPORTED_MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": DATE_FORMAT,
    }
    if event_date:
        params["commenceTimeFrom"] = event_date
        # +90 minutes ceiling — give some slack for kick-off time drift
        try:
            dt_end = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00")) + timedelta(minutes=90)
            params["commenceTimeTo"] = dt_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            pass

    # ── 5. API call ───────────────────────────────────────────────── #
    logger.info("fetch_odds_for_fixture: calling odds-api for fixture_id=%d", fixture_id)

    raw, resp_headers = await _get(f"/sports/{SPORT_KEY}/odds", params=params)
    await record_api_call("odds_api")
    _log_headers(resp_headers, f"/sports/{SPORT_KEY}/odds")

    events: list[dict] = raw if isinstance(raw, list) else []
    logger.info(
        "fetch_odds_for_fixture: %d events returned from API for fixture_id=%d",
        len(events), fixture_id,
    )

    # ── 6. Match event → fixture ──────────────────────────────────── #
    matched_event: dict | None = None
    for event in events:
        if _match_fixture_to_event(event, fixture):
            matched_event = event
            break

    if not matched_event:
        logger.warning(
            "fetch_odds_for_fixture: no matching event found for fixture_id=%d", fixture_id
        )
        return []

    # ── 7. Parse and store ────────────────────────────────────────── #
    odds_rows = _parse_event_odds(matched_event, fixture_id)
    logger.info(
        "fetch_odds_for_fixture: fixture_id=%d → %d odds rows parsed (%d bookmakers)",
        fixture_id,
        len(odds_rows),
        len(matched_event.get("bookmakers") or []),
    )

    if not odds_rows:
        return []

    response = supabase.table("odds").insert(odds_rows).execute()
    stored: list[dict] = response.data or []

    logger.info(
        "fetch_odds_for_fixture: stored %d odds rows for fixture_id=%d",
        len(stored), fixture_id,
    )
    return stored


async def fetch_odds_batch(fixtures: list[dict]) -> dict[int, list[dict]]:
    """
    Fetch odds for multiple fixtures, respecting the monthly budget.

    Applies filtering and priority ordering before any API calls:
      - Skips fixtures with data_quality_score < 0.60
      - Stops as soon as the monthly budget is exhausted

    Args:
        fixtures: List of fixture dicts (same shape as fetch_odds_for_fixture).

    Returns:
        Dict mapping fixture_id → list of odds rows.
        Fixtures that were skipped (budget, quality, cache) are omitted.
    """
    logger.info("fetch_odds_batch: received %d fixtures", len(fixtures))

    # ── Quality filter ─────────────────────────────────────────────── #
    eligible = [
        f for f in fixtures
        if float(f.get("data_quality_score") or 0.0) >= _MIN_DATA_QUALITY
    ]
    skipped_dq = len(fixtures) - len(eligible)
    logger.info(
        "fetch_odds_batch: %d eligible after data quality filter (skipped %d below %.0f%%)",
        len(eligible), skipped_dq, _MIN_DATA_QUALITY * 100,
    )

    if not eligible:
        return {}

    # ── Priority sort: highest value-edge potential first ──────────── #
    # Use priority_score if already computed (from call_budget.prioritize_fixtures),
    # otherwise fall back to data_quality_score * league trust_score.
    def _priority_key(f: dict) -> float:
        if "priority_score" in f:
            return float(f["priority_score"])
        dq    = float(f.get("data_quality_score") or 0.0)
        trust = float(f.get("league_trust_score") or f.get("trust_score") or 0.5)
        return dq * trust

    eligible.sort(key=_priority_key, reverse=True)

    results: dict[int, list[dict]] = {}
    fetched_count   = 0
    skipped_budget  = 0
    served_cache    = 0

    for fixture in eligible:
        fixture_id: int = fixture.get("id") or fixture.get("fixture_id") or 0

        # Stop early if budget is gone
        if not await can_call_odds_api():
            remaining_fixtures = len(eligible) - fetched_count - served_cache - skipped_budget
            logger.error(
                "fetch_odds_batch: budget EXHAUSTED after %d API calls. "
                "%d fixtures will not get odds this run.",
                fetched_count, remaining_fixtures,
            )
            skipped_budget += remaining_fixtures
            break

        odds = await fetch_odds_for_fixture(fixture)

        if odds:
            results[fixture_id] = odds
            # Distinguish whether this came from cache or a fresh API call
            # (cache hits don't consume budget — we just count them separately)
            cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=_ODDS_CACHE_HOURS)).isoformat()
            first_recorded = odds[0].get("recorded_at", "") if odds else ""
            if first_recorded < cutoff:
                # recorded_at predates the cache window → this was truly cached
                served_cache += 1
                logger.debug("fetch_odds_batch: fixture_id=%d served from Supabase cache", fixture_id)
            else:
                fetched_count += 1
        else:
            logger.debug("fetch_odds_batch: fixture_id=%d returned no odds (skipped)", fixture_id)

    logger.info(
        "fetch_odds_batch: DONE — %d fixtures got odds "
        "(%d fresh API calls, %d from cache, %d skipped budget, %d skipped quality)",
        len(results),
        fetched_count,
        served_cache,
        skipped_budget,
        skipped_dq,
    )
    return results
