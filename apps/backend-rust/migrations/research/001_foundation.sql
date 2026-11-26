CREATE TABLE securities (
 symbol TEXT PRIMARY KEY CHECK (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'), name TEXT NOT NULL,
 cik TEXT, benchmark BOOLEAN NOT NULL DEFAULT FALSE
);
INSERT INTO securities VALUES ('MSI','Motorola Solutions','0000068505',false),
 ('AXON','Axon Enterprise','0001069183',false),('SPY','SPDR S&P 500 ETF Trust',NULL,true);
CREATE TABLE watchlist_versions (id BIGSERIAL PRIMARY KEY, symbols JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
INSERT INTO watchlist_versions(symbols) VALUES ('["MSI","AXON"]');
CREATE TABLE training_universe_versions (
 id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, symbols JSONB NOT NULL, policy JSONB NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('blocked','exploratory','audited')), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO training_universe_versions(name,symbols,policy,status) VALUES
 ('independent-cohort-v1','[]','{"reason":"Historical membership, delistings, and feed coverage require an independent audit. Watchlist membership never implies training eligibility."}','blocked');
CREATE TABLE source_documents (
 id UUID PRIMARY KEY, provider TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
 acquired_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL,
 UNIQUE(provider,url,content_hash)
);
CREATE TABLE ingest_runs (
 id UUID PRIMARY KEY, kind TEXT NOT NULL, symbols JSONB NOT NULL, status TEXT NOT NULL,
 started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ, result JSONB, error TEXT
);
CREATE TABLE daily_bars (
 id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL REFERENCES securities(symbol), session_date DATE NOT NULL,
 feed TEXT NOT NULL CHECK(feed IN ('sip','iex')), adjustment TEXT NOT NULL CHECK(adjustment='split'),
 adjustment_asof DATE NOT NULL, open NUMERIC NOT NULL, high NUMERIC NOT NULL, low NUMERIC NOT NULL,
 close NUMERIC NOT NULL, volume NUMERIC NOT NULL, available_at TIMESTAMPTZ NOT NULL,
 acquired_at TIMESTAMPTZ NOT NULL, source_id UUID NOT NULL REFERENCES source_documents(id), content_hash TEXT NOT NULL,
 UNIQUE(symbol,session_date,feed,adjustment,adjustment_asof,content_hash),
 CHECK(open>0 AND low>0 AND high>=low AND close>0 AND volume>=0),
 CHECK(open BETWEEN low AND high AND close BETWEEN low AND high)
);
CREATE INDEX daily_bars_lookup ON daily_bars(symbol,feed,adjustment_asof,session_date,acquired_at DESC);
CREATE TABLE corporate_actions (
 id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL REFERENCES securities(symbol), action_type TEXT NOT NULL,
 effective_date DATE, acquired_at TIMESTAMPTZ NOT NULL, source_id UUID NOT NULL REFERENCES source_documents(id),
 content_hash TEXT NOT NULL UNIQUE, payload JSONB NOT NULL
);
CREATE TABLE financial_facts (
 id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL REFERENCES securities(symbol), metric TEXT NOT NULL, tag TEXT NOT NULL,
 unit TEXT NOT NULL, value NUMERIC NOT NULL, period_start DATE, period_end DATE NOT NULL,
 fiscal_year INTEGER, fiscal_period TEXT, form TEXT NOT NULL, accession TEXT NOT NULL,
 filed_date DATE NOT NULL, available_at TIMESTAMPTZ NOT NULL, acquired_at TIMESTAMPTZ NOT NULL,
 source_id UUID NOT NULL REFERENCES source_documents(id), source_url TEXT NOT NULL,
 content_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX facts_lookup ON financial_facts(symbol,metric,period_end DESC,available_at DESC);
CREATE TABLE calculated_metrics (
 id UUID PRIMARY KEY, symbol TEXT NOT NULL REFERENCES securities(symbol), as_of TIMESTAMPTZ NOT NULL,
 calculation_version TEXT NOT NULL, inputs_hash TEXT NOT NULL, payload JSONB NOT NULL,
 UNIQUE(symbol,inputs_hash,calculation_version)
);
CREATE TABLE dataset_manifests (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 feed TEXT NOT NULL, adjustment TEXT NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE model_versions (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, specification JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now()
);
INSERT INTO model_versions(id,kind,specification) VALUES
 ('historical-baseline-v1','reference','{"target":"next-open-to-horizon-close split-adjusted price return, excluding dividends","minimum_samples":252,"horizons":[1,5],"authority":"research_only"}'),
 ('technical-v1','candidate','{"ridge_alpha":1.0,"logistic_C":1.0,"split":[0.6,0.2,0.2],"purge_sessions":5,"bootstrap_samples":2000,"bootstrap_block_sessions":20,"confidence":0.9875,"minimum_evaluation_dates":126,"authority":"research_only"}');
CREATE TABLE forecast_runs (
 id UUID PRIMARY KEY, origin_session DATE NOT NULL, started_at TIMESTAMPTZ NOT NULL,
 completed_at TIMESTAMPTZ, status TEXT NOT NULL, detail JSONB NOT NULL
);
CREATE TABLE forecasts (
 id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES forecast_runs(id), symbol TEXT NOT NULL REFERENCES securities(symbol),
 origin_session DATE NOT NULL, horizon INTEGER NOT NULL CHECK(horizon IN(1,5)), issued_at TIMESTAMPTZ NOT NULL,
 data_cutoff TIMESTAMPTZ NOT NULL, entry_session DATE NOT NULL, target_session DATE NOT NULL,
 feed TEXT NOT NULL, adjustment TEXT NOT NULL CHECK(adjustment='split'), adjustment_asof DATE NOT NULL,
 dataset_id TEXT NOT NULL REFERENCES dataset_manifests(id), model_id TEXT NOT NULL REFERENCES model_versions(id),
 feature_version TEXT NOT NULL, expected_return DOUBLE PRECISION NOT NULL,
 probability_positive DOUBLE PRECISION NOT NULL CHECK(probability_positive BETWEEN 0 AND 1),
 lower_return DOUBLE PRECISION NOT NULL, upper_return DOUBLE PRECISION NOT NULL,
 sample_count INTEGER NOT NULL, provenance JSONB NOT NULL,
 UNIQUE(symbol,origin_session,horizon,model_id), CHECK(data_cutoff<=issued_at),
 CHECK(entry_session>origin_session AND target_session>=entry_session)
);
CREATE TABLE forecast_outcomes (
 id UUID PRIMARY KEY, forecast_id UUID NOT NULL REFERENCES forecasts(id), resolved_at TIMESTAMPTZ NOT NULL,
 realized_return DOUBLE PRECISION NOT NULL, spy_return DOUBLE PRECISION,
 entry_bar_id BIGINT NOT NULL REFERENCES daily_bars(id), exit_bar_id BIGINT NOT NULL REFERENCES daily_bars(id),
 provenance JSONB NOT NULL, supersedes UUID REFERENCES forecast_outcomes(id), UNIQUE(forecast_id,id)
);
CREATE INDEX outcomes_forecast ON forecast_outcomes(forecast_id,resolved_at DESC);
CREATE TABLE evaluation_reports (
 id UUID PRIMARY KEY, model_id TEXT NOT NULL REFERENCES model_versions(id), symbol TEXT,
 created_at TIMESTAMPTZ DEFAULT now(), dataset_id TEXT REFERENCES dataset_manifests(id),
 status TEXT NOT NULL, protocol JSONB NOT NULL, report JSONB NOT NULL
);
CREATE TABLE vision_models (
 id TEXT PRIMARY KEY, repository TEXT NOT NULL, revision TEXT NOT NULL, weight_file TEXT NOT NULL,
 weight_sha256 TEXT, license_review_reference TEXT, license_cleared BOOLEAN NOT NULL DEFAULT false,
 runtime_verified BOOLEAN NOT NULL DEFAULT false,
 CHECK (NOT license_cleared OR (license_review_reference IS NOT NULL AND weight_sha256 ~ '^[a-f0-9]{64}$'))
);
INSERT INTO vision_models(id,repository,revision,weight_file) VALUES
 ('chart-patterns','foduucom/stockmarket-pattern-detection-yolov8','31bbb9abc9843dc3d9c08349507c178cde56be31','model.pt'),
 ('chart-direction','foduucom/stockmarket-future-prediction','310919ed9d2d9cce9c1095cd50bb0a04de8c9e68','best.pt');
CREATE TABLE chart_detections (
 id UUID PRIMARY KEY, symbol TEXT NOT NULL REFERENCES securities(symbol), model_id TEXT NOT NULL REFERENCES vision_models(id),
 created_at TIMESTAMPTZ NOT NULL, cutoff TIMESTAMPTZ NOT NULL, renderer_version TEXT NOT NULL,
 input_hash TEXT NOT NULL, image_hash TEXT NOT NULL, weight_sha256 TEXT NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE detection_outcomes (
 id UUID PRIMARY KEY, detection_id UUID NOT NULL REFERENCES chart_detections(id), horizon INTEGER NOT NULL CHECK(horizon IN(1,5)),
 realized_return DOUBLE PRECISION NOT NULL, resolved_at TIMESTAMPTZ NOT NULL, provenance JSONB NOT NULL,
 UNIQUE(detection_id,horizon)
);
CREATE TABLE jobs (
 id UUID PRIMARY KEY, kind TEXT NOT NULL, payload JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
 heartbeat_at TIMESTAMPTZ, attempts INTEGER NOT NULL DEFAULT 0, result JSONB, error TEXT
);
CREATE UNIQUE INDEX one_active_job ON jobs((1)) WHERE status IN ('running');
CREATE TABLE sessions (
 token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ
);
CREATE TABLE login_attempts (id BIGSERIAL PRIMARY KEY, address_hash TEXT NOT NULL, attempted_at TIMESTAMPTZ DEFAULT now(), succeeded BOOLEAN NOT NULL);
CREATE INDEX login_throttle ON login_attempts(address_hash,attempted_at DESC);
CREATE FUNCTION reject_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Evidence is append-only; record a correction with provenance'; END $$;
DO $$ DECLARE t TEXT; BEGIN
 FOREACH t IN ARRAY ARRAY['watchlist_versions','training_universe_versions','source_documents','daily_bars',
 'corporate_actions','financial_facts','calculated_metrics','dataset_manifests','model_versions','forecasts',
 'forecast_outcomes','evaluation_reports','chart_detections','detection_outcomes'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',t);
 END LOOP;
END $$;
