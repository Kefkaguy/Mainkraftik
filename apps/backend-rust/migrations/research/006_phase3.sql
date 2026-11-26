INSERT INTO securities VALUES ('XLK','Technology Select Sector SPDR ETF',NULL,true),('XLI','Industrial Select Sector SPDR ETF',NULL,true) ON CONFLICT DO NOTHING;
CREATE TABLE information_versions (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL, symbols JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL,
 acquired_at TIMESTAMPTZ NOT NULL, source_id UUID NOT NULL REFERENCES source_documents(id),
 payload JSONB NOT NULL, UNIQUE(provider,external_id,id), CHECK(updated_at>=created_at)
);
CREATE INDEX information_availability ON information_versions(provider,available_at);
CREATE TABLE information_fetches (
 id UUID PRIMARY KEY, provider TEXT NOT NULL, symbols JSONB NOT NULL, started_at TIMESTAMPTZ NOT NULL,
 ended_at TIMESTAMPTZ NOT NULL, acquired_at TIMESTAMPTZ NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('complete','partial','unavailable')), detail JSONB NOT NULL
);
CREATE TABLE filing_events (
 id TEXT PRIMARY KEY, symbol TEXT NOT NULL REFERENCES securities(symbol), accession TEXT NOT NULL,
 form TEXT NOT NULL, items TEXT NOT NULL, published_at TIMESTAMPTZ NOT NULL,
 acquired_at TIMESTAMPTZ NOT NULL, source_id UUID NOT NULL REFERENCES source_documents(id), payload JSONB NOT NULL
);
CREATE TABLE feature_snapshots (
 id TEXT PRIMARY KEY, symbol TEXT NOT NULL REFERENCES securities(symbol), origin_session DATE NOT NULL,
 cutoff TIMESTAMPTZ NOT NULL, version TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('historical','prospective')),
 values JSONB NOT NULL, lineage JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE tuning_plans (
 id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), code_hash TEXT NOT NULL,
 specification JSONB NOT NULL, dataset JSONB NOT NULL
);
CREATE TABLE tuning_events (
 id BIGSERIAL PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES tuning_plans(id), trial_key TEXT NOT NULL,
 event TEXT NOT NULL, detail JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(plan_id,trial_key,event)
);
CREATE TABLE tuning_trials (
 plan_id TEXT NOT NULL REFERENCES tuning_plans(id), trial_key TEXT NOT NULL,
 family TEXT NOT NULL, feature_group TEXT NOT NULL, horizon INTEGER NOT NULL,
 number INTEGER NOT NULL, status TEXT NOT NULL, parameters JSONB NOT NULL,
 objective DOUBLE PRECISION, details JSONB NOT NULL,
 PRIMARY KEY(plan_id,trial_key)
);
CREATE TABLE candidate_results (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES tuning_plans(id), family TEXT NOT NULL,
 feature_group TEXT NOT NULL, symbol TEXT NOT NULL, horizon INTEGER NOT NULL,
 status TEXT NOT NULL, report JSONB NOT NULL, predictions JSONB NOT NULL,
 UNIQUE(plan_id,family,feature_group,symbol,horizon)
);
CREATE TABLE shadow_protocols (
 id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, start_session DATE NOT NULL,
 model_id TEXT NOT NULL REFERENCES model_versions(id), specification JSONB NOT NULL, artifact_hash TEXT NOT NULL
);
CREATE TABLE shadow_artifacts (
 protocol_id TEXT NOT NULL REFERENCES shadow_protocols(id), symbol TEXT NOT NULL, horizon INTEGER NOT NULL,
 artifact JSONB NOT NULL, PRIMARY KEY(protocol_id,symbol,horizon)
);
CREATE TABLE shadow_events (
 id BIGSERIAL PRIMARY KEY, protocol_id TEXT NOT NULL REFERENCES shadow_protocols(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), event TEXT NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE shadow_verdicts (
 protocol_id TEXT PRIMARY KEY REFERENCES shadow_protocols(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 status TEXT NOT NULL CHECK(status IN ('passed','failed','inconclusive')), report JSONB NOT NULL
);
DO $$ DECLARE t TEXT; BEGIN
 FOREACH t IN ARRAY ARRAY['information_versions','information_fetches','filing_events','feature_snapshots',
 'tuning_plans','tuning_events','tuning_trials','candidate_results','shadow_protocols','shadow_artifacts','shadow_events','shadow_verdicts'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',t);
 END LOOP;
END $$;
