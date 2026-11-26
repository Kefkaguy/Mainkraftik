CREATE TABLE IF NOT EXISTS pilot_settings(id INTEGER PRIMARY KEY CHECK(id=1), enabled BOOLEAN NOT NULL DEFAULT FALSE, reason TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
INSERT INTO pilot_settings VALUES(1,false,'Dedicated account and deployment acceptance not verified',now()) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS pilot_events(id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), kind TEXT NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_sources(id TEXT PRIMARY KEY, symbol TEXT NOT NULL, provider TEXT NOT NULL, acquired_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_decisions(id TEXT PRIMARY KEY, session_date DATE NOT NULL, symbol TEXT NOT NULL, cutoff TIMESTAMPTZ NOT NULL, issued_at TIMESTAMPTZ NOT NULL, action TEXT NOT NULL, payload JSONB NOT NULL, UNIQUE(session_date,symbol));
CREATE TABLE IF NOT EXISTS pilot_intents(client_id TEXT PRIMARY KEY, decision_id TEXT REFERENCES pilot_decisions(id), symbol TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN('buy','sell')), session_date DATE NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), state TEXT NOT NULL, notional NUMERIC, quantity NUMERIC, broker_id TEXT, payload JSONB NOT NULL, UNIQUE(symbol,side,session_date));
CREATE TABLE IF NOT EXISTS pilot_order_versions(id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES pilot_intents(client_id), observed_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_snapshots(id BIGSERIAL PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL, account_id TEXT, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_asset_versions(id TEXT PRIMARY KEY,symbol TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_quotes(symbol TEXT PRIMARY KEY, source_time TIMESTAMPTZ NOT NULL, acquired_at TIMESTAMPTZ NOT NULL, price NUMERIC NOT NULL,bid NUMERIC,ask NUMERIC,feed TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_benchmarks(id BIGSERIAL PRIMARY KEY,observed_at TIMESTAMPTZ NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_connection(id TEXT PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_readiness(id BIGSERIAL PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS pilot_readiness_observed ON pilot_readiness(observed_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS pilot_research_runs(id TEXT PRIMARY KEY,session_date DATE NOT NULL,mode TEXT NOT NULL,completed_at TIMESTAMPTZ NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_selections(session_date DATE PRIMARY KEY,research_id TEXT NOT NULL REFERENCES pilot_research_runs(id),cutoff TIMESTAMPTZ NOT NULL,symbols JSONB NOT NULL CHECK(jsonb_typeof(symbols)='array' AND jsonb_array_length(symbols)<=20));
CREATE TABLE IF NOT EXISTS pilot_groq_calls(id BIGSERIAL PRIMARY KEY,started_at TIMESTAMPTZ NOT NULL DEFAULT now(),session_date DATE NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_forecast_outcomes(id TEXT PRIMARY KEY,decision_id TEXT NOT NULL REFERENCES pilot_decisions(id),horizon INTEGER NOT NULL CHECK(horizon IN(1,5)),resolved_at TIMESTAMPTZ NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_strategy_sessions(
 session_date DATE PRIMARY KEY, cutoff TIMESTAMPTZ NOT NULL, issued_at TIMESTAMPTZ NOT NULL,
 bundle_id TEXT NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_strategy_signals(
 id TEXT PRIMARY KEY, session_date DATE NOT NULL REFERENCES pilot_strategy_sessions(session_date),
 symbol TEXT NOT NULL, strategy TEXT NOT NULL CHECK(strategy IN ('day','swing')),
 role TEXT NOT NULL CHECK(role IN ('paper','challenger')), issued_at TIMESTAMPTZ NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('BUY','HOLD','SKIP')), payload JSONB NOT NULL,
 UNIQUE(session_date,symbol,strategy,role));
CREATE TABLE IF NOT EXISTS pilot_strategy_outcomes(
 signal_id TEXT PRIMARY KEY REFERENCES pilot_strategy_signals(id), resolved_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_shadow_sessions(
 session_date DATE PRIMARY KEY REFERENCES pilot_strategy_sessions(session_date), frozen_at TIMESTAMPTZ NOT NULL,
 picks JSONB NOT NULL);
ALTER TABLE pilot_intents ADD COLUMN IF NOT EXISTS strategy_signal_id TEXT REFERENCES pilot_strategy_signals(id);
ALTER TABLE pilot_intents DROP CONSTRAINT IF EXISTS pilot_intents_symbol_side_session_date_key;
CREATE UNIQUE INDEX IF NOT EXISTS pilot_single_daily_buy
 ON pilot_intents(symbol,session_date) WHERE side='buy';
CREATE UNIQUE INDEX IF NOT EXISTS pilot_single_legacy_sell
 ON pilot_intents(symbol,session_date) WHERE side='sell' AND strategy_signal_id IS NULL;
CREATE TABLE IF NOT EXISTS pilot_virtual_outcomes(
 signal_id TEXT PRIMARY KEY REFERENCES pilot_strategy_signals(id), resolved_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_strategy_verdicts(
 bundle_id TEXT PRIMARY KEY, generated_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_daily_reports(
 session_date DATE PRIMARY KEY, generated_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE OR REPLACE FUNCTION reject_pilot_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Paper evidence is append-only'; END $$;
DO $$ DECLARE t TEXT; BEGIN
 FOREACH t IN ARRAY ARRAY['pilot_events','pilot_sources','pilot_decisions','pilot_order_versions','pilot_snapshots','pilot_asset_versions','pilot_benchmarks','pilot_forecast_outcomes','pilot_research_runs','pilot_selections','pilot_groq_calls','pilot_readiness','pilot_strategy_sessions','pilot_strategy_signals','pilot_strategy_outcomes','pilot_shadow_sessions','pilot_daily_reports','pilot_virtual_outcomes','pilot_strategy_verdicts'] LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_'||t) THEN
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_pilot_evidence_mutation()','immutable_'||t,t);
 END IF; END LOOP; END $$;
