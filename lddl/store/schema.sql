-- LDDL Fantasy Analysis — DuckDB schema.
-- JSON columns hold raw Sleeper payload fragments; commonly-queried bits are
-- denormalized into typed columns alongside.

CREATE TABLE IF NOT EXISTS leagues (
    league_id            VARCHAR PRIMARY KEY,
    previous_league_id   VARCHAR,
    season               VARCHAR NOT NULL,
    name                 VARCHAR NOT NULL,
    status               VARCHAR,
    sport                VARCHAR,
    total_rosters        INTEGER,
    league_type          INTEGER,    -- settings.type: 0=redraft, 1=keeper, 2=dynasty
    playoff_week_start   INTEGER,
    playoff_teams        INTEGER,
    settings             JSON,
    scoring_settings     JSON,
    roster_positions     JSON,
    metadata             JSON,
    fetched_at           TIMESTAMP
);

CREATE TABLE IF NOT EXISTS league_users (
    league_id      VARCHAR,
    user_id        VARCHAR,
    display_name   VARCHAR,
    team_name      VARCHAR,
    is_owner       BOOLEAN,
    is_bot         BOOLEAN,
    avatar         VARCHAR,
    metadata       JSON,
    PRIMARY KEY (league_id, user_id)
);

CREATE TABLE IF NOT EXISTS managers (
    user_id              VARCHAR PRIMARY KEY,
    display_name         VARCHAR,    -- most-recent display name
    aliases              JSON,
    team_names           JSON,
    first_seen_season    VARCHAR,
    last_seen_season     VARCHAR
);

CREATE TABLE IF NOT EXISTS rosters (
    league_id              VARCHAR,
    roster_id              INTEGER,
    owner_id               VARCHAR,
    co_owners              JSON,
    division               INTEGER,
    players                JSON,
    starters               JSON,
    taxi                   JSON,
    reserve                JSON,
    keepers                JSON,
    wins                   INTEGER,
    losses                 INTEGER,
    ties                   INTEGER,
    fpts                   DOUBLE,
    fpts_against           DOUBLE,
    ppts                   DOUBLE,
    waiver_position        INTEGER,
    waiver_budget_used     INTEGER,
    total_moves            INTEGER,
    settings               JSON,
    metadata               JSON,
    PRIMARY KEY (league_id, roster_id)
);

CREATE TABLE IF NOT EXISTS matchups (
    league_id         VARCHAR,
    week              INTEGER,
    matchup_id        INTEGER,
    roster_id         INTEGER,
    points            DOUBLE,
    custom_points     DOUBLE,
    starters          JSON,
    starters_points   JSON,
    players           JSON,
    players_points    JSON,
    PRIMARY KEY (league_id, week, roster_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id      VARCHAR PRIMARY KEY,
    league_id           VARCHAR,
    week                INTEGER,
    type                VARCHAR,
    status              VARCHAR,
    creator             VARCHAR,
    created_at          TIMESTAMP,
    status_updated_at   TIMESTAMP,
    roster_ids          JSON,
    consenter_ids       JSON,
    waiver_budget       JSON,
    leg                 INTEGER,
    settings            JSON,
    metadata            JSON
);

CREATE TABLE IF NOT EXISTS transaction_players (
    transaction_id   VARCHAR,
    player_id        VARCHAR,
    roster_id        INTEGER,
    movement         VARCHAR,    -- 'add' or 'drop'
    PRIMARY KEY (transaction_id, player_id, movement)
);

CREATE TABLE IF NOT EXISTS transaction_picks (
    transaction_id        VARCHAR,
    season                VARCHAR,
    round                 INTEGER,
    roster_id             INTEGER,    -- pick's original roster_id
    owner_id              INTEGER,    -- new owner roster_id after this transaction
    previous_owner_id     INTEGER,
    PRIMARY KEY (transaction_id, season, round, roster_id)
);

CREATE TABLE IF NOT EXISTS traded_picks (
    league_id             VARCHAR,
    season                VARCHAR,
    round                 INTEGER,
    roster_id             INTEGER,
    owner_id              INTEGER,
    previous_owner_id     INTEGER,
    PRIMARY KEY (league_id, season, round, roster_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id             VARCHAR PRIMARY KEY,
    league_id            VARCHAR,
    season               VARCHAR,
    type                 VARCHAR,
    status               VARCHAR,
    sport                VARCHAR,
    rounds               INTEGER,
    settings             JSON,
    metadata             JSON,
    start_time           TIMESTAMP,
    last_picked          TIMESTAMP,
    draft_order          JSON,
    slot_to_roster_id    JSON
);

CREATE TABLE IF NOT EXISTS draft_picks (
    draft_id      VARCHAR,
    pick_no       INTEGER,
    round         INTEGER,
    draft_slot    INTEGER,
    roster_id     INTEGER,
    picked_by     VARCHAR,
    player_id     VARCHAR,
    is_keeper     BOOLEAN,
    metadata      JSON,
    PRIMARY KEY (draft_id, pick_no)
);

CREATE TABLE IF NOT EXISTS draft_traded_picks (
    draft_id              VARCHAR,
    season                VARCHAR,
    round                 INTEGER,
    roster_id             INTEGER,
    owner_id              INTEGER,
    previous_owner_id     INTEGER,
    PRIMARY KEY (draft_id, season, round, roster_id)
);

CREATE TABLE IF NOT EXISTS playoff_bracket (
    league_id     VARCHAR,
    bracket       VARCHAR,    -- 'winners' or 'losers'
    match_id      INTEGER,    -- 'm' from Sleeper
    round         INTEGER,    -- 'r' from Sleeper
    placement     INTEGER,    -- 'p' from Sleeper (null if not a placement game)
    t1            INTEGER,    -- roster_id
    t2            INTEGER,    -- roster_id
    winner        INTEGER,    -- roster_id; null until game played
    loser         INTEGER,    -- roster_id; null until game played
    t1_from       JSON,       -- e.g. {"w": 3} or {"l": 4}
    t2_from       JSON,
    PRIMARY KEY (league_id, bracket, match_id)
);

CREATE TABLE IF NOT EXISTS players (
    player_id            VARCHAR PRIMARY KEY,
    full_name            VARCHAR,
    first_name           VARCHAR,
    last_name            VARCHAR,
    position             VARCHAR,
    fantasy_positions    JSON,
    team                 VARCHAR,
    age                  INTEGER,
    years_exp            INTEGER,
    status               VARCHAR,
    injury_status        VARCHAR,
    metadata             JSON,
    fetched_at           TIMESTAMP
);
