CREATE UNIQUE INDEX evaluation_frozen_identity ON evaluation_reports(model_id,dataset_id);
CREATE TABLE evaluation_access_log (
 id UUID PRIMARY KEY, report_id UUID NOT NULL REFERENCES evaluation_reports(id),
 accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(), purpose TEXT NOT NULL
);
CREATE TRIGGER immutable_evaluation_access BEFORE UPDATE OR DELETE ON evaluation_access_log
 FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
