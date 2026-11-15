"""
Realistic event generator for the simulated shop-api service.

Generates >= --events request events across --days of simulated history
(ending "now") with:
  - diurnal RPS curve (daytime peak, nighttime trough) + weekend dampening
  - endpoint traffic skew (/api/health ~10x /api/pay)
  - endpoint-specific 4xx/5xx error rates
  - heavy-tailed latency (~1% of requests in the 2000-5000ms range)
  - a handful of injected "incidents" (temporary error/latency spikes on one
    endpoint) so the dashboard has something interesting to show on the
    "are spikes visible over time" question

...then batch-inserts everything into ClickHouse via clickhouse-connect.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timedelta, timezone

import clickhouse_connect
import numpy as np

SERVICE_NAME = "shop-api"

# name, relative traffic weight, HTTP method
ENDPOINTS = [
    ("/api/health", 50, "GET"),
    ("/api/orders", 20, "GET"),
    ("/api/login", 15, "POST"),
    ("/api/pay", 5, "POST"),
]

# endpoint -> (p_5xx, p_4xx) baseline error probabilities
BASE_ERROR_RATES = {
    "/api/health": (0.01, 0.00),
    "/api/login": (0.01, 0.10),
    "/api/orders": (0.05, 0.00),
    "/api/pay": (0.05, 0.00),
}

STATUS_5XX = [500, 502, 503, 504]
ERROR_TYPES_5XX = ["internal_error", "timeout", "service_unavailable", "bad_gateway"]

BASE_HEAVY_TAIL_P = 0.01  # fraction of requests landing in the 2000-5000ms tail

# diurnal RPS multiplier by hour-of-day (0..23): night trough, daytime peak
HOURLY_PROFILE = [
    0.20, 0.15, 0.12, 0.10, 0.10, 0.15, 0.25, 0.45,
    0.70, 0.90, 1.00, 1.00, 0.95, 1.00, 1.00, 0.95,
    0.90, 0.85, 0.70, 0.60, 0.50, 0.40, 0.35, 0.25,
]
WEEKEND_FACTOR = 0.65

# injected incidents, as (hour_offset_from_start, duration_hours, endpoint, kind)
# kind "error_spike" boosts 5xx rate; "latency_spike" boosts heavy-tail probability
INCIDENTS = [
    (3 * 24 + 14, 1, "/api/pay", "error_spike"),
    (7 * 24 + 10, 2, "/api/orders", "latency_spike"),
    (11 * 24 + 20, 1, "/api/pay", "latency_spike"),
    (5 * 24 + 9, 1, "/api/login", "error_spike"),
]


def build_incident_index(days: int) -> dict[int, list[tuple[str, str]]]:
    index: dict[int, list[tuple[str, str]]] = {}
    for start_hour, duration, endpoint, kind in INCIDENTS:
        for h in range(start_hour, min(start_hour + duration, days * 24)):
            index.setdefault(h, []).append((endpoint, kind))
    return index


def hourly_counts(start: datetime, days: int, total_events: int, rng: np.random.Generator) -> np.ndarray:
    hours = days * 24
    weights = np.empty(hours)
    for h in range(hours):
        ts = start + timedelta(hours=h)
        mult = HOURLY_PROFILE[ts.hour]
        if ts.weekday() >= 5:
            mult *= WEEKEND_FACTOR
        weights[h] = mult
    scale = total_events / weights.sum()
    return rng.poisson(weights * scale)


def generate(days: int, total_events: int, seed: int):
    rng = np.random.default_rng(seed)
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(days=days)

    counts = hourly_counts(start, days, total_events, rng)
    incident_index = build_incident_index(days)

    endpoint_names = [e[0] for e in ENDPOINTS]
    endpoint_methods = {e[0]: e[2] for e in ENDPOINTS}
    endpoint_weights = np.array([e[1] for e in ENDPOINTS], dtype=float)
    endpoint_probs = endpoint_weights / endpoint_weights.sum()

    rows = []
    for hour_offset, n in enumerate(counts):
        n = int(n)
        if n == 0:
            continue
        hour_start = start + timedelta(hours=hour_offset)
        active = incident_index.get(hour_offset, [])

        endpoints_in_hour = rng.choice(endpoint_names, size=n, p=endpoint_probs)
        offsets_ms = rng.uniform(0, 3600_000, size=n)

        for endpoint, off_ms in zip(endpoints_in_hour, offsets_ms):
            ts = hour_start + timedelta(milliseconds=float(off_ms))
            p_5xx, p_4xx = BASE_ERROR_RATES[endpoint]
            heavy_tail_p = BASE_HEAVY_TAIL_P

            for inc_endpoint, kind in active:
                if inc_endpoint != endpoint:
                    continue
                if kind == "error_spike":
                    p_5xx = max(p_5xx, 0.40)
                elif kind == "latency_spike":
                    heavy_tail_p = max(heavy_tail_p, 0.35)

            roll = rng.random()
            if roll < p_5xx:
                status = int(rng.choice(STATUS_5XX))
                error_type = str(rng.choice(ERROR_TYPES_5XX))
            elif roll < p_5xx + p_4xx:
                status = 401
                error_type = "invalid_password"
            else:
                status = 200
                error_type = ""

            if rng.random() < heavy_tail_p:
                duration_ms = rng.uniform(2000, 5000)
            else:
                duration_ms = rng.triangular(50, 90, 200)
            if status >= 500:
                duration_ms += rng.uniform(0, 400)

            rows.append((
                ts,
                SERVICE_NAME,
                endpoint,
                endpoint_methods[endpoint],
                status,
                int(duration_ms),
                error_type,
                str(uuid.uuid4()),
            ))

    rows.sort(key=lambda r: r[0])
    return rows


def insert_batches(rows, host, port, username, password, database, batch_size):
    client = clickhouse_connect.get_client(
        host=host, port=port, username=username, password=password, database=database,
    )
    column_names = [
        "timestamp", "service_name", "endpoint", "method",
        "status_code", "duration_ms", "error_type", "request_id",
    ]
    total = len(rows)
    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        client.insert("service_logs", batch, column_names=column_names)
        print(f"inserted {min(i + batch_size, total)}/{total}")
    client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="simulated history window")
    parser.add_argument("--events", type=int, default=150_000, help="target total event count")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=20_000)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--username", default="default")
    parser.add_argument("--password", default="lab4clickhouse")
    parser.add_argument("--database", default="default")
    args = parser.parse_args()

    print(f"generating ~{args.events} events over {args.days} days (seed={args.seed})...")
    rows = generate(args.days, args.events, args.seed)
    print(f"generated {len(rows)} events, inserting in batches of {args.batch_size}...")
    insert_batches(
        rows, args.host, args.port, args.username, args.password, args.database, args.batch_size,
    )
    print("done.")


if __name__ == "__main__":
    main()
