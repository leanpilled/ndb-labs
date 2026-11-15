-- service_logs: raw request-level event log for the imitated shop API
-- (/api/login, /api/orders, /api/pay, /api/health).
--
-- Engine: MergeTree.
--   Append-only event stream (no updates/deletes, no need for
--   ReplacingMergeTree/CollapsingMergeTree), high insert throughput from
--   batched generator inserts -- the classic MergeTree use case.
--
-- PARTITION BY toDate(timestamp):
--   All dashboard panels (RPS, error rate, latency quantiles, top-10
--   endpoints) filter by a time range first ($__timeFilter(timestamp)).
--   Daily partitions let ClickHouse prune whole partitions outside the
--   selected window instead of scanning the full table, and make
--   retention trivial later (DROP PARTITION per day) -- the right trade-off
--   for a log table that is queried almost exclusively "last N hours/days".
--
-- ORDER BY (service_name, endpoint, timestamp):
--   service_name/endpoint have very low cardinality (1 and 4 distinct
--   values respectively) so they compress to almost nothing yet let
--   ClickHouse skip whole granules for per-endpoint queries (the
--   "top-10 problem endpoints" and per-endpoint breakdowns). timestamp is
--   last in the key so that, within one (service, endpoint) pair, rows
--   stay time-ordered for efficient range reads and quantile scans.
--
-- Column types:
--   timestamp     DateTime64(3)       millisecond resolution, needed for
--                                     realistic request-level latency work
--   service_name  LowCardinality(String)  1 distinct value today, kept as a
--                                     string so more services can be added
--                                     without a schema change
--   endpoint      LowCardinality(String)  4 distinct values
--   method        LowCardinality(String)  GET/POST, 2 distinct values
--   status_code   UInt16              HTTP status codes fit comfortably,
--                                     avoids sign handling of Int
--   duration_ms   UInt32              request latency in ms; UInt32 covers
--                                     the simulated heavy tail (up to 5000ms)
--                                     with headroom
--   error_type    LowCardinality(String)  empty string for successful
--                                     requests, short label otherwise
--   request_id    UUID                unique id per request

CREATE TABLE IF NOT EXISTS service_logs
(
    timestamp    DateTime64(3),
    service_name LowCardinality(String),
    endpoint     LowCardinality(String),
    method       LowCardinality(String),
    status_code  UInt16,
    duration_ms  UInt32,
    error_type   LowCardinality(String),
    request_id   UUID
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (service_name, endpoint, timestamp)
SETTINGS index_granularity = 8192;
