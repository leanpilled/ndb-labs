"""Load test text-to-image search API.

Usage:
    python scripts/load_test.py --base-url http://localhost:8000 \
        --concurrency 1 5 10 20 50 --requests-per-level 200
"""

import argparse
import asyncio
import csv
import random
import time
from pathlib import Path

import httpx
import lance

QUERY_POOL_SIZE = 500


def load_query_pool(dataset_path: str, n: int, seed: int) -> list[str]:
    ds = lance.dataset(dataset_path)
    table = ds.to_table(columns=["caption"]).to_pydict()
    captions = table["caption"]
    random.seed(seed)
    return random.sample(captions, min(n, len(captions)))


async def worker(
    client: httpx.AsyncClient, base_url: str, queries: list[str], k: int, results: list
) -> None:
    q = random.choice(queries)
    t0 = time.perf_counter()
    try:
        resp = await client.get(
            f"{base_url}/search", params={"q": q, "k": k}, timeout=30.0
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        results.append((elapsed_ms, resp.status_code == 200))
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        results.append((elapsed_ms, False))


async def run_level(
    base_url: str, queries: list[str], k: int, concurrency: int, n_requests: int
) -> dict:
    results: list = []
    async with httpx.AsyncClient() as client:
        start = time.perf_counter()
        sem = asyncio.Semaphore(concurrency)

        async def bound_worker():
            async with sem:
                await worker(client, base_url, queries, k, results)

        await asyncio.gather(*(bound_worker() for _ in range(n_requests)))
        total_time = time.perf_counter() - start

    latencies = sorted(r[0] for r in results)
    n = len(latencies)
    errors = sum(1 for _, ok in results if not ok)
    pct = lambda p: latencies[min(int(len(latencies) * p), len(latencies) - 1)]
    return {
        "concurrency": concurrency,
        "n_requests": n_requests,
        "total_time_s": total_time,
        "rps": n_requests / total_time,
        "mean_latency_ms": sum(latencies) / n,
        "p50_latency_ms": pct(0.50),
        "p95_latency_ms": pct(0.95),
        "p99_latency_ms": pct(0.99),
        "max_latency_ms": latencies[-1],
        "error_rate": errors / n,
    }


async def main_async(args) -> list[dict]:
    queries = load_query_pool(args.dataset, QUERY_POOL_SIZE, args.seed)
    summaries = []
    for c in args.concurrency:
        print(f"--- concurrency={c} ---")
        summary = await run_level(
            args.base_url, queries, args.k, c, args.requests_per_level
        )
        print(summary)
        summaries.append(summary)
        await asyncio.sleep(1)  # let the system settle between levels
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--dataset",
        default=str(
            Path(__file__).resolve().parent.parent
            / "data"
            / "raw"
            / "data"
            / "train.lance"
        ),
    )
    parser.add_argument(
        "--concurrency", type=int, nargs="+", default=[1, 5, 10, 20, 50]
    )
    parser.add_argument("--requests-per-level", type=int, default=200)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parent.parent / "reports" / "load_test_results.csv"
        ),
    )
    args = parser.parse_args()

    summaries = asyncio.run(main_async(args))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summaries[0].keys()))
        writer.writeheader()
        writer.writerows(summaries)
    print(f"Saved load test summary to {out_path}")


if __name__ == "__main__":
    main()
