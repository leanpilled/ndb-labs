#!/usr/bin/env python3
"""
Load generator for Lab 3's cache-invalidation demo.

Fires a mix of GET /items/{id} (cache reads) and PUT /items/{id} (writes
that trigger a Redis pub/sub invalidation) against one or more app
instances, so the effect shows up live on the Grafana dashboard
(http://localhost:3001) and in Prometheus (http://localhost:9090).

Usage:
    pip install -r requirements.txt

    # hit nginx (round-robins across app1/app2/app3 on the server side)
    python generate_load.py --rps 200 --duration 120

    # bypass nginx and round-robin between instances yourself
    python generate_load.py --url http://localhost:8001,http://localhost:8002,http://localhost:8003
"""

import argparse
import asyncio
import itertools
import random
import time
from collections import Counter
from dataclasses import dataclass, field

import aiohttp

ITEM_NAMES = ["Laptop", "Mouse", "Keyboard", "Monitor", "Headphones"]


@dataclass
class Stats:
    requests: int = 0
    errors: int = 0
    status_codes: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)  # cache / db, GET only
    latencies_ms: list = field(default_factory=list)

    def record(self, status: int, latency_ms: float, source: str | None) -> None:
        self.requests += 1
        self.status_codes[status] += 1
        self.latencies_ms.append(latency_ms)
        if source:
            self.sources[source] += 1
        if status >= 400:
            self.errors += 1


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(int(len(values) * p), len(values) - 1)
    return values[idx]


async def worker(
    session: aiohttp.ClientSession,
    base_urls: list[str],
    item_ids: list[int],
    update_ratio: float,
    delay: float,
    stop_at: float,
    stats: Stats,
) -> None:
    urls = itertools.cycle(base_urls)
    while time.monotonic() < stop_at:
        base = next(urls)
        item_id = random.choice(item_ids)
        is_update = random.random() < update_ratio
        started = time.monotonic()
        try:
            if is_update:
                payload = {
                    "name": random.choice(ITEM_NAMES),
                    "price": round(random.uniform(10, 2000), 2),
                }
                async with session.put(f"{base}/items/{item_id}", json=payload) as resp:
                    await resp.read()
                    stats.record(resp.status, (time.monotonic() - started) * 1000, None)
            else:
                async with session.get(f"{base}/items/{item_id}") as resp:
                    body = await resp.json()
                    latency_ms = (time.monotonic() - started) * 1000
                    stats.record(resp.status, latency_ms, body.get("source"))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            stats.requests += 1
            stats.errors += 1

        await asyncio.sleep(delay)


async def report(stats: Stats, stop_at: float, interval: float = 1.0) -> None:
    last_requests = 0
    while time.monotonic() < stop_at:
        await asyncio.sleep(interval)
        rps = (stats.requests - last_requests) / interval
        last_requests = stats.requests
        hits, misses = stats.sources["cache"], stats.sources["db"]
        total_reads = hits + misses
        hit_ratio = (hits / total_reads * 100) if total_reads else 0.0
        print(
            f"[{time.strftime('%H:%M:%S')}] rps={rps:6.1f}  "
            f"total={stats.requests:6d}  errors={stats.errors:4d}  "
            f"cache_hit_ratio={hit_ratio:5.1f}%"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8090",
        help="Comma-separated base URL(s) to hit. Default: nginx at :8090.",
    )
    parser.add_argument(
        "--items", default="1,2,3", help="Comma-separated item ids to query/update"
    )
    parser.add_argument(
        "--concurrency", type=int, default=20, help="Number of concurrent workers"
    )
    parser.add_argument(
        "--rps", type=float, default=100.0, help="Approximate target total requests/sec"
    )
    parser.add_argument(
        "--duration", type=float, default=60.0, help="Duration of the test in seconds"
    )
    parser.add_argument(
        "--update-ratio",
        type=float,
        default=0.05,
        help="Fraction of requests that are PUT updates (trigger cache invalidation)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    base_urls = [u.strip().rstrip("/") for u in args.url.split(",")]
    item_ids = [int(i) for i in args.items.split(",")]
    delay = args.concurrency / args.rps

    print(
        f"targets={base_urls} items={item_ids} concurrency={args.concurrency} "
        f"target_rps={args.rps} update_ratio={args.update_ratio} duration={args.duration}s\n"
    )

    stats = Stats()
    stop_at = time.monotonic() + args.duration

    connector = aiohttp.TCPConnector(limit=args.concurrency * 2)
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        workers = [
            worker(session, base_urls, item_ids, args.update_ratio, delay, stop_at, stats)
            for _ in range(args.concurrency)
        ]
        await asyncio.gather(*workers, report(stats, stop_at))

    total_reads = stats.sources["cache"] + stats.sources["db"]
    hit_ratio = (stats.sources["cache"] / total_reads * 100) if total_reads else 0.0
    print("\n--- summary ---")
    print(f"total requests : {stats.requests}")
    print(f"errors         : {stats.errors}")
    print(f"status codes   : {dict(stats.status_codes)}")
    print(
        f"cache hit ratio: {hit_ratio:.1f}% "
        f"({stats.sources['cache']} hits / {stats.sources['db']} misses)"
    )
    print(
        f"latency ms     : p50={percentile(stats.latencies_ms, 0.5):.1f}  "
        f"p95={percentile(stats.latencies_ms, 0.95):.1f}  "
        f"p99={percentile(stats.latencies_ms, 0.99):.1f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
