-- Human-facing sequence for global dispatches. UUIDs remain stable internal
-- identities; run_number is the monotonically increasing operator handle.

ALTER TABLE pipeline_runs ADD COLUMN run_number BIGINT;

WITH numbered AS (
    SELECT run_id,
           row_number() OVER (ORDER BY started_at, run_id) AS run_number
    FROM pipeline_runs
)
UPDATE pipeline_runs AS run
SET run_number = numbered.run_number
FROM numbered
WHERE run.run_id = numbered.run_id;

CREATE TABLE pipeline_run_counter (
    next_number BIGINT NOT NULL
);

INSERT INTO pipeline_run_counter
SELECT coalesce(max(run_number), 0) + 1 FROM pipeline_runs;
