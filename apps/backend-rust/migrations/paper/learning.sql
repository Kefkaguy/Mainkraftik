
CREATE TABLE IF NOT EXISTS learning_accounts(account_id TEXT PRIMARY KEY,label TEXT NOT NULL,account_number TEXT NOT NULL,
 baseline_equity NUMERIC NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS learning_records(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,kind TEXT NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),payload JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS learning_records_lookup ON learning_records(account_id,kind,recorded_at DESC);
CREATE TABLE IF NOT EXISTS learning_intents(client_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,prediction_id TEXT NOT NULL,
 symbol TEXT NOT NULL,phase TEXT NOT NULL,state TEXT NOT NULL,body JSONB NOT NULL,order_payload JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),UNIQUE(account_id,prediction_id,symbol,phase));
CREATE TABLE IF NOT EXISTS learning_controls(id INTEGER PRIMARY KEY CHECK(id=1),enabled BOOLEAN NOT NULL DEFAULT false);
INSERT INTO learning_controls VALUES(1,false) ON CONFLICT DO NOTHING;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_learning_records') THEN
CREATE TRIGGER immutable_learning_records BEFORE UPDATE OR DELETE ON learning_records
FOR EACH ROW EXECUTE FUNCTION reject_pilot_evidence_mutation(); END IF; END $$;
