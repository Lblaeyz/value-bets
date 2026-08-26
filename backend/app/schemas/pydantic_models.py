from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict


# ------------------------------------------------------------------ #
# Shared config
# ------------------------------------------------------------------ #
class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ #
# League
# ------------------------------------------------------------------ #
class LeagueOut(_Base):
    id: int
    name: str
    country: str
    football_data_code: Optional[str] = None
    api_football_id: Optional[int] = None
    openligadb_code: Optional[str] = None
    trust_score: float
    data_quality_score: float
    created_at: datetime


# ------------------------------------------------------------------ #
# Team
# ------------------------------------------------------------------ #
class TeamOut(_Base):
    id: int
    name: str
    league_id: int
    elo_rating: float
    football_data_id: Optional[int] = None
    api_football_id: Optional[int] = None
    created_at: datetime


# ------------------------------------------------------------------ #
# Fixture
# ------------------------------------------------------------------ #
class FixtureOut(_Base):
    id: int
    home_team_id: int
    away_team_id: int
    league_id: int
    kickoff_utc: datetime
    status: str
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    data_quality_score: float
    created_at: datetime


# ------------------------------------------------------------------ #
# Odds
# ------------------------------------------------------------------ #
class OddsOut(_Base):
    id: int
    fixture_id: int
    bookmaker: str
    market: str
    selection: str
    odds_decimal: float
    implied_probability: float
    recorded_at: datetime


# ------------------------------------------------------------------ #
# Prediction
# ------------------------------------------------------------------ #
class PredictionOut(_Base):
    id: int
    fixture_id: int
    market: str
    selection: str
    model_probability: float
    bookmaker_probability: float
    value_edge: float
    confidence_score: float
    risk_score: float
    data_quality_score: float
    recommended_odds: Optional[float] = None
    recommended_bookmaker: Optional[str] = None
    reasoning: Optional[str] = None
    kelly_stake: Optional[float] = None
    status: str
    created_at: datetime


# ------------------------------------------------------------------ #
# Result
# ------------------------------------------------------------------ #
class ResultOut(_Base):
    id: int
    prediction_id: int
    fixture_id: int
    outcome: str
    profit_loss: float
    closing_odds: Optional[float] = None
    clv: Optional[float] = None
    brier_contribution: Optional[float] = None
    recorded_at: datetime


# ------------------------------------------------------------------ #
# Performance summary
# ------------------------------------------------------------------ #
class PerformanceSummaryOut(_Base):
    id: int
    period: str
    league_id: Optional[int] = None
    market: Optional[str] = None
    total_bets: int
    wins: int
    losses: int
    roi: float
    yield_pct: float
    avg_clv: Optional[float] = None
    brier_score: Optional[float] = None
    updated_at: datetime


# ------------------------------------------------------------------ #
# API call budget
# ------------------------------------------------------------------ #
class ApiCallBudgetOut(_Base):
    id: int
    api_name: str
    date: date
    calls_used: int
    calls_limit: int
    last_updated: datetime


# ------------------------------------------------------------------ #
# Health
# ------------------------------------------------------------------ #
class HealthResponse(BaseModel):
    status: str = "ok"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    environment: str
