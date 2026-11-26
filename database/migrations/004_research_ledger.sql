CREATE TABLE research_plans (
 id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 dataset_id TEXT NOT NULL REFERENCES dataset_manifests(id),
 protected_start DATE NOT NULL, specification JSONB NOT NULL, code_hash TEXT NOT NULL
);
CREATE TABLE research_candidates (
 id TEXT PRIMARY KEY, specification JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE research_experiments (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES research_plans(id),
 candidate_id TEXT NOT NULL REFERENCES research_candidates(id), symbol TEXT NOT NULL REFERENCES securities(symbol),
 horizon INTEGER NOT NULL CHECK(horizon IN (1,5)),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 status TEXT NOT NULL CHECK(status IN ('passed','failed','inconclusive')),
 report JSONB NOT NULL, UNIQUE(plan_id,candidate_id,symbol,horizon)
);
CREATE TABLE research_predictions (
 experiment_id TEXT NOT NULL REFERENCES research_experiments(id), origin_session DATE NOT NULL,
 target_session DATE NOT NULL, expected_return DOUBLE PRECISION NOT NULL,
 probability_positive DOUBLE PRECISION NOT NULL CHECK(probability_positive BETWEEN 0 AND 1),
 lower_return DOUBLE PRECISION NOT NULL, upper_return DOUBLE PRECISION NOT NULL,
 realized_return DOUBLE PRECISION NOT NULL, baseline_return DOUBLE PRECISION NOT NULL,
 baseline_probability DOUBLE PRECISION NOT NULL, regime TEXT NOT NULL,
 PRIMARY KEY(experiment_id,origin_session), CHECK(target_session>origin_session)
);
CREATE TABLE research_audits (
 id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), report JSONB NOT NULL
);
CREATE TABLE job_events (
 id BIGSERIAL PRIMARY KEY, job_id UUID REFERENCES jobs(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 event TEXT NOT NULL, detail JSONB NOT NULL
);
CREATE TABLE worker_status (
 id TEXT PRIMARY KEY, heartbeat_at TIMESTAMPTZ NOT NULL, detail JSONB NOT NULL
);
DO $$ DECLARE t TEXT; BEGIN
 FOREACH t IN ARRAY ARRAY['research_plans','research_candidates','research_experiments',
 'research_predictions','research_audits','job_events'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',t);
 END LOOP;
END $$;
