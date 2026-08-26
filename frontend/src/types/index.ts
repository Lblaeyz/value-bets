export interface TeamRef {
  id: number;
  name: string;
  elo_rating: number | null;
}

export interface LeagueRef {
  id: number;
  name: string;
  country: string;
  trust_score: number | null;
}

export interface Prediction {
  id: number;
  fixture_id: number;
  market: string;
  model_probability: number;
  bookmaker_probability: number;
  value_edge: number;
  confidence_score: number;
  recommended_odds: number;
  best_bookmaker: string | null;
  kelly_fraction: number | null;
  reasoning: string | null;
  status: "RECOMMENDED" | "PENDING" | "WON" | "LOST" | "VOID";
  created_at: string;
}

export interface TodayFixture {
  id: number;
  kickoff_utc: string;
  status: string;
  home_goals: number | null;
  away_goals: number | null;
  data_quality_score: number | null;
  home_team: TeamRef;
  away_team: TeamRef;
  league: LeagueRef;
  predictions: Prediction[];
}

export interface TodayMeta {
  total_fixtures: number;
  value_bets: number;
}

export interface TodayResponse {
  date: string;
  data: TodayFixture[];
  meta: TodayMeta;
  message?: string;
}

export interface MatchOdds {
  bookmaker: string;
  market: string;
  selection: string;
  odds_decimal: number;
  implied_probability: number | null;
  recorded_at: string;
}

export interface MatchDetail {
  id: number;
  kickoff_utc: string;
  status: string;
  home_goals: number | null;
  away_goals: number | null;
  home_team: TeamRef;
  away_team: TeamRef;
  league: LeagueRef;
  predictions: Prediction[];
  odds: MatchOdds[];
  h2h: {
    home_wins: number;
    draws: number;
    away_wins: number;
    avg_total_goals: number;
    last_5: Array<{ date: string; home_score: number; away_score: number }>;
  } | null;
  injuries: Array<{
    player_name: string;
    injury_type: string | null;
    status: string;
    team_id: number;
  }>;
}

export interface PaginatedPredictions {
  predictions: Array<
    Prediction & {
      home_team: string;
      away_team: string;
      league_name: string;
      kickoff_utc: string;
    }
  >;
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface PerformanceSummary {
  total_bets: number;
  won: number;
  lost: number;
  void: number;
  win_rate: number;
  roi: number;
  yield: number;
  total_profit_loss: number;
  avg_clv: number | null;
  avg_odds: number | null;
  insufficient_data: boolean;
}

export interface PerformanceByLeague {
  leagues: Array<{
    league_name: string;
    total_bets: number;
    won: number;
    lost: number;
    win_rate: number;
    roi: number;
    avg_clv: number | null;
  }>;
}

export interface PerformanceByMarket {
  markets: Array<{
    market: string;
    total_bets: number;
    won: number;
    lost: number;
    win_rate: number;
    roi: number;
    avg_clv: number | null;
  }>;
}

export interface ClvTrend {
  data: Array<{ date: string; avg_clv: number; count: number }>;
  overall_avg_clv: number | null;
  interpretation: string;
  days: number;
}

export interface BudgetStatus {
  api_football_calls_today: number;
  api_football_daily_limit: number;
  api_football_remaining: number;
  odds_api_calls_this_month: number;
  odds_api_monthly_limit: number;
  odds_api_remaining: number;
  budget_warning: boolean;
}
