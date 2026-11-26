CREATE TABLE scheduler_marks (
 task TEXT NOT NULL, session_date DATE NOT NULL, job_id UUID REFERENCES jobs(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(task,session_date)
);
