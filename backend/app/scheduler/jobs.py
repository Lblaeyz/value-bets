"""
APScheduler job definitions — full daily pipeline.

Pipeline schedule (all times UTC):
  01:00  daily_pipeline     — main end-to-end run
  Every 30 min             — refresh_odds_job (top fixtures only)
  Every 6 h                — sync_fixtures_job (keep fixture list fresh)
  01:45  settle_results_job — settle yesterday's finished fixtures
  03:30  update_performance_job — recompute summary stats

Manual trigger:
  POST /api/admin/run-pipeline  (wired in admin.py)
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.db.supabase_client import supabase
from app.utils.call_budget import get_remaining_calls
from app.utils.logger import logger

scheduler = AsyncIOScheduler(timezone="UTC")


# ================================================================== #
# DAILY PIPELINE
# ================================================================== #

async def daily_pipeline(target_date: str | None = None) -> dict[str, Any]:
    """
    Full end-to-end daily pipeline.

    Args:
        target_date: ISO date string, e.g. "2026-05-10".  When provided the
                     pipeline fetches fixtures for that specific date instead
                     of today — useful for backfilling historical data.

    Returns a summary dict (also logged) so it can be returned from
    the manual trigger endpoint.
    """
    started_at = time.monotonic()
    run_ts = datetime.now(tz=timezone.utc).isoformat()
    logger.info("═" * 60)
    logger.info(
        "daily_pipeline: START  %s%s",
        run_ts,
        f"  [backfill={target_date}]" if target_date else "",
    )
    logger.info("═" * 60)

    summary: dict[str, Any] = {
        "run_at":                     run_ts,
        "fixtures_pulled":            0,
        "fixtures_after_dedup":       0,
        "fixtures_prioritised":       0,
        "fixtures_analyzed":          0,
        "fixtures_skipped":           0,
        "skip_reasons":               {},
        "recommendations_generated":  0,
        "predictions_stored":         0,
        "api_football_calls_used":    0,
        "odds_api_calls_used":        0,
        "pipeline_duration_seconds":  0.0,
        "errors":                     [],
    }

    summary["target_date"] = target_date or "today"

    # ── Step 1: Fetch fixtures from football-data.org ──────────────── #
    logger.info("daily_pipeline [1/11]: fetch_todays_fixtures(target_date=%s)", target_date)
    fd_fixtures: list[dict] = []
    try:
        from app.ingestion.football_data import fetch_todays_fixtures
        fd_fixtures = await fetch_todays_fixtures(target_date=target_date)
        logger.info("daily_pipeline [1/11]: %d fixtures from football-data.org", len(fd_fixtures))
    except Exception as exc:
        logger.error("daily_pipeline [1/11]: FAILED — %s", exc, exc_info=True)
        summary["errors"].append(f"step1_football_data: {exc}")

    # ── Step 2: Fetch bl2 + bl3 from OpenLigaDB ───────────────────── #
    logger.info("daily_pipeline [2/11]: fetch_all_supported_leagues(target_date=%s)", target_date)
    ol_fixtures: list[dict] = []
    try:
        from app.ingestion.openligadb import fetch_all_supported_leagues
        ol_fixtures = await fetch_all_supported_leagues(target_date=target_date)
        logger.info("daily_pipeline [2/11]: %d fixtures from OpenLigaDB", len(ol_fixtures))
    except Exception as exc:
        logger.error("daily_pipeline [2/11]: FAILED — %s", exc, exc_info=True)
        summary["errors"].append(f"step2_openligadb: {exc}")

    # ── Step 3: Merge & deduplicate ───────────────────────────────── #
    logger.info("daily_pipeline [3/11]: merge + deduplicate fixtures")
    all_fixtures = fd_fixtures + ol_fixtures
    summary["fixtures_pulled"] = len(all_fixtures)

    seen_keys: set[str] = set()
    unique_fixtures: list[dict] = []
    for f in all_fixtures:
        # Football-data fixtures carry DB "id"; OpenLigaDB fixtures carry "openligadb_id"
        db_id = f.get("id") or f.get("fixture_id")
        if db_id:
            key = f"db:{db_id}"
        elif f.get("openligadb_id"):
            key = f"ol:{f['openligadb_id']}"
        else:
            continue  # no usable key — skip
        if key not in seen_keys:
            seen_keys.add(key)
            unique_fixtures.append(f)
    summary["fixtures_after_dedup"] = len(unique_fixtures)
    logger.info(
        "daily_pipeline [3/11]: %d total → %d after dedup",
        len(all_fixtures), len(unique_fixtures),
    )

    # ── Step 4: Score each fixture for data quality ───────────────── #
    logger.info("daily_pipeline [4/11]: enrich fixtures with league trust + cache flags")
    enriched: list[dict] = []
    for f in unique_fixtures:
        fixture_id = f.get("id") or f.get("fixture_id")

        # Resolve league trust score (best-effort; default 0.5 if not in row)
        league_trust = float(f.get("league_trust_score") or f.get("trust_score") or 0.5)

        # Check if odds are already cached in Supabase (within 6 h)
        from datetime import timedelta
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=6)).isoformat()
        odds_cached = False
        if fixture_id:
            odds_row = (
                supabase.table("odds")
                .select("id")
                .eq("fixture_id", fixture_id)
                .gt("recorded_at", cutoff)
                .limit(1)
                .execute()
            ).data
            odds_cached = bool(odds_row)

        # Check H2H cache
        h2h_cached = False
        home_id = f.get("home_team_id")
        away_id = f.get("away_team_id")
        if home_id and away_id:
            h2h_row = (
                supabase.table("h2h_cache")
                .select("id")
                .eq("home_team_id", home_id)
                .eq("away_team_id", away_id)
                .gt("expires_at", datetime.now(tz=timezone.utc).isoformat())
                .limit(1)
                .execute()
            ).data
            h2h_cached = bool(h2h_row)

        enriched.append({
            **f,
            "league_trust_score": league_trust,
            "odds_cached":        odds_cached,
            "h2h_cached":         h2h_cached,
        })

    # ── Step 5: Prioritise — take top 30 ─────────────────────────── #
    logger.info("daily_pipeline [5/11]: prioritize_fixtures() → top 30")
    from app.utils.call_budget import prioritize_fixtures
    priority_fixtures = prioritize_fixtures(enriched)
    summary["fixtures_prioritised"] = len(priority_fixtures)
    logger.info("daily_pipeline [5/11]: %d priority fixtures selected", len(priority_fixtures))

    # ── Step 6: Per-fixture enrichment (H2H, injuries, lineups) ───── #
    logger.info("daily_pipeline [6/11]: fetch H2H / injuries / lineups per fixture")
    af_budget_before = await get_remaining_calls("api_football")

    for f in priority_fixtures:
        fixture_id  = f.get("id") or f.get("fixture_id")
        home_id     = f.get("home_team_id")
        away_id     = f.get("away_team_id")
        api_fix_id  = str(f.get("api_football_id") or "")
        api_home_id = str(f.get("home_api_football_id") or "")
        api_away_id = str(f.get("away_api_football_id") or "")

        # H2H (uses cache; only calls API on miss)
        if home_id and away_id and api_home_id and api_away_id:
            try:
                from app.ingestion.api_football import fetch_h2h
                await fetch_h2h(api_home_id, api_away_id, home_id, away_id)
            except Exception as exc:
                logger.warning("daily_pipeline [6]: H2H failed fixture=%s — %s", fixture_id, exc)

        if not api_fix_id:
            continue

        # Injuries
        try:
            from app.ingestion.api_football import fetch_injuries
            await fetch_injuries(fixture_id, api_fix_id)
        except Exception as exc:
            logger.warning("daily_pipeline [6]: injuries failed fixture=%s — %s", fixture_id, exc)

        # Lineups — only if budget > 20 calls remaining (preserve headroom)
        remaining = await get_remaining_calls("api_football")
        if remaining > 20:
            try:
                from app.ingestion.api_football import fetch_lineups
                await fetch_lineups(fixture_id, api_fix_id)
            except Exception as exc:
                logger.warning("daily_pipeline [6]: lineups failed fixture=%s — %s", fixture_id, exc)
        else:
            logger.warning(
                "daily_pipeline [6]: skipping lineups for fixture=%s "
                "— only %d api-football calls left",
                fixture_id, remaining,
            )

    af_budget_after = await get_remaining_calls("api_football")
    summary["api_football_calls_used"] = (af_budget_before or 0) - (af_budget_after or 0)
    logger.info(
        "daily_pipeline [6/11]: enrichment done — used %d api-football calls (%d remaining)",
        summary["api_football_calls_used"], af_budget_after,
    )

    # ── Step 7: Poisson model ─────────────────────────────────────── #
    logger.info("daily_pipeline [7/11]: run_poisson_for_all_fixtures()")
    probabilities: list[dict] = []
    try:
        from app.models.poisson import run_poisson_for_all_fixtures
        probabilities = run_poisson_for_all_fixtures(priority_fixtures)
        summary["fixtures_analyzed"] = len(probabilities)
        summary["fixtures_skipped"]  = len(priority_fixtures) - len(probabilities)
        logger.info(
            "daily_pipeline [7/11]: %d/%d fixtures produced probabilities",
            len(probabilities), len(priority_fixtures),
        )
    except Exception as exc:
        logger.error("daily_pipeline [7/11]: FAILED — %s", exc, exc_info=True)
        summary["errors"].append(f"step7_poisson: {exc}")

    # ── Step 8: Fetch odds for qualifying fixtures ─────────────────── #
    logger.info("daily_pipeline [8/11]: fetch_odds_batch()")
    odds_by_fixture: dict[int, list[dict]] = {}
    odds_budget_before = await get_remaining_calls("odds_api")
    try:
        from app.ingestion.odds_api import fetch_odds_batch
        # Only pass fixtures that have Poisson output (others can't generate recs)
        prob_fixture_ids = {p["fixture_id"] for p in probabilities}
        qualifying = [f for f in priority_fixtures
                      if (f.get("id") or f.get("fixture_id")) in prob_fixture_ids]
        odds_by_fixture = await fetch_odds_batch(qualifying)
        logger.info("daily_pipeline [8/11]: odds fetched for %d fixtures", len(odds_by_fixture))
    except Exception as exc:
        logger.error("daily_pipeline [8/11]: FAILED — %s", exc, exc_info=True)
        summary["errors"].append(f"step8_odds: {exc}")

    odds_budget_after = await get_remaining_calls("odds_api")
    summary["odds_api_calls_used"] = (odds_budget_before or 0) - (odds_budget_after or 0)

    # ── Step 9: Generate recommendations ─────────────────────────── #
    logger.info("daily_pipeline [9/11]: generate_recommendations()")
    recommendations: list[dict] = []
    try:
        from app.models.ev_calculator import generate_recommendations
        recommendations = generate_recommendations(
            fixtures=priority_fixtures,
            probabilities=probabilities,
            odds=odds_by_fixture,
        )
        summary["recommendations_generated"] = len(recommendations)
        logger.info(
            "daily_pipeline [9/11]: %d recommendations generated", len(recommendations)
        )
    except Exception as exc:
        logger.error("daily_pipeline [9/11]: FAILED — %s", exc, exc_info=True)
        summary["errors"].append(f"step9_ev: {exc}")

    # ── Step 10: Store predictions in Supabase ─────────────────────── #
    logger.info("daily_pipeline [10/11]: store %d predictions", len(recommendations))
    stored_count = 0
    for rec in recommendations:
        try:
            from app.db.queries import upsert_prediction
            upsert_prediction(rec)
            stored_count += 1
        except Exception as exc:
            logger.error(
                "daily_pipeline [10]: failed to store prediction fixture=%s — %s",
                rec.get("fixture_id"), exc,
            )
    summary["predictions_stored"] = stored_count
    logger.info("daily_pipeline [10/11]: stored %d/%d predictions", stored_count, len(recommendations))

    # ── Step 11: Log full summary ─────────────────────────────────── #
    duration = round(time.monotonic() - started_at, 2)
    summary["pipeline_duration_seconds"] = duration

    logger.info("═" * 60)
    logger.info("daily_pipeline: COMPLETE in %.1fs", duration)
    logger.info("  fixtures_pulled:           %d", summary["fixtures_pulled"])
    logger.info("  fixtures_after_dedup:      %d", summary["fixtures_after_dedup"])
    logger.info("  fixtures_prioritised:      %d", summary["fixtures_prioritised"])
    logger.info("  fixtures_analyzed:         %d", summary["fixtures_analyzed"])
    logger.info("  fixtures_skipped:          %d", summary["fixtures_skipped"])
    logger.info("  recommendations_generated: %d", summary["recommendations_generated"])
    logger.info("  predictions_stored:        %d", summary["predictions_stored"])
    logger.info("  api_football_calls_used:   %d", summary["api_football_calls_used"])
    logger.info("  odds_api_calls_used:       %d", summary["odds_api_calls_used"])
    if summary["errors"]:
        logger.warning("  errors: %s", summary["errors"])
    logger.info("═" * 60)

    return summary


# ================================================================== #
# SUPPORTING JOBS
# ================================================================== #

async def refresh_odds_job() -> None:
    """Refresh odds for fixtures kicking off in the next 24 h."""
    logger.info("[scheduler] refresh_odds_job: START")
    try:
        from datetime import timedelta
        from app.ingestion.odds_api import fetch_odds_batch
        from app.utils.call_budget import prioritize_fixtures

        now = datetime.now(tz=timezone.utc)
        cutoff = (now + timedelta(hours=24)).isoformat()

        rows = (
            supabase.table("fixtures")
            .select("*")
            .eq("status", "SCHEDULED")
            .lte("kickoff_utc", cutoff)
            .execute()
        ).data or []

        prioritised = prioritize_fixtures(rows)
        await fetch_odds_batch(prioritised)
        logger.info("[scheduler] refresh_odds_job: done (%d fixtures)", len(prioritised))
    except Exception as exc:
        logger.error("[scheduler] refresh_odds_job FAILED: %s", exc, exc_info=True)


async def sync_fixtures_job() -> None:
    """Pull the latest fixture list from all sources."""
    logger.info("[scheduler] sync_fixtures_job: START")
    try:
        from app.ingestion.football_data import fetch_todays_fixtures
        from app.ingestion.openligadb import fetch_all_supported_leagues
        await asyncio.gather(
            fetch_todays_fixtures(),
            fetch_all_supported_leagues(),
            return_exceptions=True,
        )
        logger.info("[scheduler] sync_fixtures_job: done")
    except Exception as exc:
        logger.error("[scheduler] sync_fixtures_job FAILED: %s", exc, exc_info=True)


async def settle_results_job() -> None:
    """
    Find FINISHED fixtures with RECOMMENDED predictions and mark them
    WON or LOST based on actual scoreline.
    """
    logger.info("[scheduler] settle_results_job: START")
    try:
        # Load predictions awaiting settlement
        preds = (
            supabase.table("predictions")
            .select("id, fixture_id, market, selection")
            .eq("status", "RECOMMENDED")
            .execute()
        ).data or []

        settled = 0
        for pred in preds:
            fixture = (
                supabase.table("fixtures")
                .select("status, home_goals, away_goals")
                .eq("id", pred["fixture_id"])
                .maybe_single()
                .execute()
            ).data
            if not fixture or fixture.get("status") != "FINISHED":
                continue

            home_g = fixture.get("home_goals")
            away_g = fixture.get("away_goals")
            if home_g is None or away_g is None:
                continue

            total = home_g + away_g
            market    = pred["market"]
            selection = pred["selection"]

            outcome = _determine_outcome(market, selection, home_g, away_g, total)
            if outcome:
                supabase.table("predictions").update(
                    {"status": "WON" if outcome == "WIN" else "LOST"}
                ).eq("id", pred["id"]).execute()
                settled += 1

        logger.info("[scheduler] settle_results_job: settled %d predictions", settled)
    except Exception as exc:
        logger.error("[scheduler] settle_results_job FAILED: %s", exc, exc_info=True)


def _determine_outcome(
    market: str, selection: str, home_g: int, away_g: int, total: int
) -> str | None:
    """Return 'WIN', 'LOSS', or None (unresolvable market)."""
    sel = selection.lower()
    if market == "1X2":
        if sel == "home":  return "WIN" if home_g > away_g else "LOSS"
        if sel == "draw":  return "WIN" if home_g == away_g else "LOSS"
        if sel == "away":  return "WIN" if away_g > home_g else "LOSS"
    elif market == "BTTS":
        btts = home_g > 0 and away_g > 0
        if sel == "yes": return "WIN" if btts else "LOSS"
        if sel == "no":  return "WIN" if not btts else "LOSS"
    elif market in ("OU", "AOU"):
        try:
            line = float(sel.split("_", 1)[1])
            over = total > line
            if sel.startswith("over"):  return "WIN" if over else "LOSS"
            if sel.startswith("under"): return "WIN" if not over else "LOSS"
        except (IndexError, ValueError):
            pass
    return None


async def update_performance_job() -> None:
    """Recompute performance_summary for the current week and month."""
    logger.info("[scheduler] update_performance_job: START")
    try:
        from datetime import date
        now = date.today()
        week_period  = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        month_period = f"{now.year}-{now.month:02d}"

        for period in [week_period, month_period]:
            results = (
                supabase.table("results")
                .select("outcome, profit_loss, clv")
                .execute()
            ).data or []

            if not results:
                continue

            wins   = sum(1 for r in results if r.get("outcome") == "WIN")
            losses = sum(1 for r in results if r.get("outcome") == "LOSS")
            total  = wins + losses
            if total == 0:
                continue

            pl      = sum(float(r.get("profit_loss") or 0) for r in results)
            roi     = pl / total
            yield_  = pl / total
            avg_clv = sum(float(r.get("clv") or 0) for r in results) / len(results)

            supabase.table("performance_summary").upsert(
                {
                    "period":     period,
                    "league_id":  None,
                    "market":     None,
                    "total_bets": total,
                    "wins":       wins,
                    "losses":     losses,
                    "roi":        round(roi, 4),
                    "yield_pct":  round(yield_, 4),
                    "avg_clv":    round(avg_clv, 4),
                },
                on_conflict="period,league_id,market",
            ).execute()

        logger.info("[scheduler] update_performance_job: done")
    except Exception as exc:
        logger.error("[scheduler] update_performance_job FAILED: %s", exc, exc_info=True)


# ================================================================== #
# REGISTRATION
# ================================================================== #

def register_jobs() -> None:
    """Register all scheduled jobs. Called once at app startup."""
    scheduler.add_job(
        daily_pipeline,
        trigger=CronTrigger(hour=1, minute=0),
        id="daily_pipeline",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        refresh_odds_job,
        trigger=IntervalTrigger(minutes=30),
        id="refresh_odds",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        sync_fixtures_job,
        trigger=CronTrigger(hour="*/6", minute=0),
        id="sync_fixtures",
        replace_existing=True,
    )
    scheduler.add_job(
        settle_results_job,
        trigger=CronTrigger(hour=1, minute=45),
        id="settle_results",
        replace_existing=True,
    )
    scheduler.add_job(
        update_performance_job,
        trigger=CronTrigger(hour=3, minute=30),
        id="update_performance",
        replace_existing=True,
    )

    jobs = scheduler.get_jobs()
    logger.info(
        "[scheduler] %d jobs registered: %s",
        len(jobs),
        [j.id for j in jobs],
    )
