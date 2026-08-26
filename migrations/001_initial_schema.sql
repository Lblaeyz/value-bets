-- ============================================================
-- Football Value Betting Platform — Initial Schema Migration
-- ============================================================

-- --------------------------------------------------------
-- leagues
-- --------------------------------------------------------
CREATE TABLE leagues (
    id                  SERIAL PRIMARY KEY,
    name                TEXT        NOT NULL,
    country             TEXT        NOT NULL,
    football_data_code  TEXT,
    api_football_id     INTEGER,
    openligadb_code     TEXT,
    trust_score         NUMERIC(5,4)    NOT NULL DEFAULT 1.0
                            CHECK (trust_score BETWEEN 0 AND 1),
    data_quality_score  NUMERIC(5,4)    NOT NULL DEFAULT 1.0
                            CHECK (data_quality_score BETWEEN 0 AND 1),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_leagues_football_data_code   ON leagues (football_data_code)  WHERE football_data_code  IS NOT NULL;
CREATE UNIQUE INDEX idx_leagues_api_football_id      ON leagues (api_football_id)     WHERE api_football_id     IS NOT NULL;
CREATE UNIQUE INDEX idx_leagues_openligadb_code      ON leagues (openligadb_code)     WHERE openligadb_code     IS NOT NULL;

-- --------------------------------------------------------
-- teams
-- --------------------------------------------------------
CREATE TABLE teams (
    id                  SERIAL PRIMARY KEY,
    name                TEXT        NOT NULL,
    league_id           INTEGER     NOT NULL REFERENCES leagues (id) ON DELETE RESTRICT,
    elo_rating          NUMERIC(8,2) NOT NULL DEFAULT 1500.0,
    football_data_id    INTEGER,
    api_football_id     INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_teams_league_id          ON teams (league_id);
CREATE UNIQUE INDEX idx_teams_football_data_id  ON teams (football_data_id)  WHERE football_data_id  IS NOT NULL;
CREATE UNIQUE INDEX idx_teams_api_football_id   ON teams (api_football_id)   WHERE api_football_id   IS NOT NULL;

-- --------------------------------------------------------
-- fixtures
-- --------------------------------------------------------
CREATE TABLE fixtures (
    id                  SERIAL PRIMARY KEY,
    football_data_id    INTEGER,
    api_football_id     INTEGER,
    openligadb_id       INTEGER,
    home_team_id        INTEGER     NOT NULL REFERENCES teams   (id) ON DELETE RESTRICT,
    away_team_id        INTEGER     NOT NULL REFERENCES teams   (id) ON DELETE RESTRICT,
    league_id           INTEGER     NOT NULL REFERENCES leagues (id) ON DELETE RESTRICT,
    kickoff_utc         TIMESTAMPTZ NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'SCHEDULED'
                            CHECK (status IN ('SCHEDULED','LIVE','FINISHED','POSTPONED','CANCELLED')),
    home_goals          SMALLINT    CHECK (home_goals >= 0),
    away_goals          SMALLINT    CHECK (away_goals >= 0),
    data_quality_score  NUMERIC(5,4) NOT NULL DEFAULT 1.0
                            CHECK (data_quality_score BETWEEN 0 AND 1),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_fixture_teams CHECK (home_team_id <> away_team_id)
);

CREATE INDEX idx_fixtures_home_team_id    ON fixtures (home_team_id);
CREATE INDEX idx_fixtures_away_team_id    ON fixtures (away_team_id);
CREATE INDEX idx_fixtures_league_id       ON fixtures (league_id);
CREATE INDEX idx_fixtures_kickoff_utc     ON fixtures (kickoff_utc);
CREATE INDEX idx_fixtures_status          ON fixtures (status);
CREATE UNIQUE INDEX idx_fixtures_football_data_id  ON fixtures (football_data_id) WHERE football_data_id IS NOT NULL;
CREATE UNIQUE INDEX idx_fixtures_api_football_id   ON fixtures (api_football_id)  WHERE api_football_id  IS NOT NULL;

-- --------------------------------------------------------
-- odds
-- --------------------------------------------------------
CREATE TABLE odds (
    id                  BIGSERIAL   PRIMARY KEY,
    fixture_id          INTEGER     NOT NULL REFERENCES fixtures (id) ON DELETE CASCADE,
    bookmaker           TEXT        NOT NULL,
    market              TEXT        NOT NULL,  -- e.g. '1X2', 'BTTS', 'OU25'
    selection           TEXT        NOT NULL,  -- e.g. 'home', 'draw', 'away', 'over', 'yes'
    odds_decimal        NUMERIC(10,4) NOT NULL CHECK (odds_decimal > 1),
    implied_probability NUMERIC(7,6)  NOT NULL CHECK (implied_probability BETWEEN 0 AND 1),
    recorded_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_odds_fixture_id    ON odds (fixture_id);
CREATE INDEX idx_odds_recorded_at   ON odds (recorded_at);
CREATE INDEX idx_odds_bookmaker     ON odds (bookmaker);
CREATE INDEX idx_odds_market        ON odds (market);

-- --------------------------------------------------------
-- line_movements
-- --------------------------------------------------------
CREATE TABLE line_movements (
    id                  BIGSERIAL   PRIMARY KEY,
    fixture_id          INTEGER     NOT NULL REFERENCES fixtures (id) ON DELETE CASCADE,
    market              TEXT        NOT NULL,
    selection           TEXT        NOT NULL,
    opening_odds        NUMERIC(10,4) NOT NULL CHECK (opening_odds > 1),
    current_odds        NUMERIC(10,4) NOT NULL CHECK (current_odds > 1),
    closing_odds        NUMERIC(10,4) CHECK (closing_odds > 1),
    movement_direction  TEXT        NOT NULL DEFAULT 'UNCHANGED'
                            CHECK (movement_direction IN ('STEAM','DRIFT','UNCHANGED')),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_line_movements_fixture_id  ON line_movements (fixture_id);
CREATE INDEX idx_line_movements_recorded_at ON line_movements (recorded_at);
CREATE INDEX idx_line_movements_market      ON line_movements (market);

-- --------------------------------------------------------
-- injuries
-- --------------------------------------------------------
CREATE TABLE injuries (
    id                  BIGSERIAL   PRIMARY KEY,
    fixture_id          INTEGER     NOT NULL REFERENCES fixtures (id) ON DELETE CASCADE,
    team_id             INTEGER     NOT NULL REFERENCES teams    (id) ON DELETE RESTRICT,
    player_name         TEXT        NOT NULL,
    injury_type         TEXT,
    status              TEXT        NOT NULL DEFAULT 'DOUBTFUL'
                            CHECK (status IN ('OUT','DOUBTFUL','AVAILABLE')),
    return_date         DATE,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_injuries_fixture_id  ON injuries (fixture_id);
CREATE INDEX idx_injuries_team_id     ON injuries (team_id);

-- --------------------------------------------------------
-- lineups
-- --------------------------------------------------------
CREATE TABLE lineups (
    id                  BIGSERIAL   PRIMARY KEY,
    fixture_id          INTEGER     NOT NULL REFERENCES fixtures (id) ON DELETE CASCADE,
    team_id             INTEGER     NOT NULL REFERENCES teams    (id) ON DELETE RESTRICT,
    player_name         TEXT        NOT NULL,
    position            TEXT,
    is_starter          BOOLEAN     NOT NULL DEFAULT FALSE,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_lineups_fixture_id   ON lineups (fixture_id);
CREATE INDEX idx_lineups_team_id      ON lineups (team_id);

-- --------------------------------------------------------
-- h2h_cache
-- --------------------------------------------------------
CREATE TABLE h2h_cache (
    id                  SERIAL      PRIMARY KEY,
    home_team_id        INTEGER     NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    away_team_id        INTEGER     NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    data                JSONB       NOT NULL DEFAULT '{}',
    cached_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days'),

    CONSTRAINT uq_h2h_pair UNIQUE (home_team_id, away_team_id)
);

CREATE INDEX idx_h2h_cache_home_team_id  ON h2h_cache (home_team_id);
CREATE INDEX idx_h2h_cache_away_team_id  ON h2h_cache (away_team_id);
CREATE INDEX idx_h2h_cache_expires_at    ON h2h_cache (expires_at);

-- --------------------------------------------------------
-- predictions
-- --------------------------------------------------------
CREATE TABLE predictions (
    id                      BIGSERIAL   PRIMARY KEY,
    fixture_id              INTEGER     NOT NULL REFERENCES fixtures (id) ON DELETE CASCADE,
    market                  TEXT        NOT NULL,
    selection               TEXT        NOT NULL,
    model_probability       NUMERIC(7,6) NOT NULL CHECK (model_probability BETWEEN 0 AND 1),
    bookmaker_probability   NUMERIC(7,6) NOT NULL CHECK (bookmaker_probability BETWEEN 0 AND 1),
    value_edge              NUMERIC(8,4) NOT NULL,  -- (model_prob - bookmaker_prob), can be negative
    confidence_score        NUMERIC(5,4) NOT NULL DEFAULT 0.5
                                CHECK (confidence_score BETWEEN 0 AND 1),
    risk_score              NUMERIC(5,4) NOT NULL DEFAULT 0.5
                                CHECK (risk_score BETWEEN 0 AND 1),
    data_quality_score      NUMERIC(5,4) NOT NULL DEFAULT 1.0
                                CHECK (data_quality_score BETWEEN 0 AND 1),
    recommended_odds        NUMERIC(10,4) CHECK (recommended_odds > 1),
    recommended_bookmaker   TEXT,
    reasoning               TEXT,
    kelly_stake             NUMERIC(8,4),   -- fraction of bankroll; NULL = no bet
    status                  TEXT        NOT NULL DEFAULT 'PENDING'
                                CHECK (status IN ('PENDING','RECOMMENDED','REJECTED','WON','LOST','VOID')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_predictions_fixture_id   ON predictions (fixture_id);
CREATE INDEX idx_predictions_status       ON predictions (status);
CREATE INDEX idx_predictions_created_at   ON predictions (created_at);
CREATE INDEX idx_predictions_market       ON predictions (market);
CREATE INDEX idx_predictions_value_edge   ON predictions (value_edge DESC);

-- --------------------------------------------------------
-- results
-- --------------------------------------------------------
CREATE TABLE results (
    id                  BIGSERIAL   PRIMARY KEY,
    prediction_id       BIGINT      NOT NULL REFERENCES predictions (id) ON DELETE CASCADE,
    fixture_id          INTEGER     NOT NULL REFERENCES fixtures    (id) ON DELETE CASCADE,
    outcome             TEXT        NOT NULL
                            CHECK (outcome IN ('WIN','LOSS','VOID','PUSH')),
    profit_loss         NUMERIC(12,4) NOT NULL DEFAULT 0,  -- in stake units
    closing_odds        NUMERIC(10,4) CHECK (closing_odds > 1),
    clv                 NUMERIC(8,4),   -- closing line value; positive = beat closing line
    brier_contribution  NUMERIC(8,6),   -- contribution to Brier score
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_results_prediction_id  ON results (prediction_id);
CREATE INDEX idx_results_fixture_id     ON results (fixture_id);
CREATE INDEX idx_results_recorded_at    ON results (recorded_at);
CREATE INDEX idx_results_outcome        ON results (outcome);

-- --------------------------------------------------------
-- performance_summary
-- --------------------------------------------------------
CREATE TABLE performance_summary (
    id                  SERIAL      PRIMARY KEY,
    period              TEXT        NOT NULL,  -- e.g. '2024-W01', '2024-01', 'all-time'
    league_id           INTEGER     REFERENCES leagues (id) ON DELETE SET NULL,
    market              TEXT,                  -- NULL = all markets
    total_bets          INTEGER     NOT NULL DEFAULT 0 CHECK (total_bets >= 0),
    wins                INTEGER     NOT NULL DEFAULT 0 CHECK (wins >= 0),
    losses              INTEGER     NOT NULL DEFAULT 0 CHECK (losses >= 0),
    roi                 NUMERIC(10,4) NOT NULL DEFAULT 0,   -- as a decimal, e.g. 0.05 = 5 %
    yield_pct           NUMERIC(10,4) NOT NULL DEFAULT 0,
    avg_clv             NUMERIC(8,4),
    brier_score         NUMERIC(8,6) CHECK (brier_score BETWEEN 0 AND 2),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_perf_summary UNIQUE (period, league_id, market)
);

CREATE INDEX idx_performance_summary_league_id  ON performance_summary (league_id);
CREATE INDEX idx_performance_summary_period     ON performance_summary (period);

-- --------------------------------------------------------
-- api_call_budget
-- --------------------------------------------------------
CREATE TABLE api_call_budget (
    id              SERIAL      PRIMARY KEY,
    api_name        TEXT        NOT NULL,
    date            DATE        NOT NULL DEFAULT CURRENT_DATE,
    calls_used      INTEGER     NOT NULL DEFAULT 0  CHECK (calls_used >= 0),
    calls_limit     INTEGER     NOT NULL DEFAULT 0  CHECK (calls_limit >= 0),
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_api_call_budget UNIQUE (api_name, date)
);

CREATE INDEX idx_api_call_budget_api_name  ON api_call_budget (api_name);
CREATE INDEX idx_api_call_budget_date      ON api_call_budget (date);
