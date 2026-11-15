"""Evaluate text->image retrieval quality of the deployed system.

Flickr30k comes with 5 human captions per image. We hold out the
non-canonical captions as synthetic user queries, encode them
with our CLIP text encoder, search Qdrant among image_emb vectors, and check
whether the correct image comes back near the top.

Metrics (see README.md for justification):
  - Recall@1 / Recall@5 / Recall@10
  - Mean Reciprocal Rank (MRR)
  - Median rank of the correct image

Usage:
    python scripts/evaluate.py --n-queries 1000
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import lance
import numpy as np
from qdrant_client import QdrantClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.clip_encoder import ClipTextEncoder

COLLECTION = "flickr30k"


def main() -> None:
    parser = argparse.ArgumentParser()
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
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--n-queries", type=int, default=1000)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parent.parent / "reports" / "eval_metrics.json"
        ),
    )
    args = parser.parse_args()
    random.seed(args.seed)

    ds = lance.dataset(args.dataset)
    table = ds.to_table(columns=["id", "captions"]).to_pydict()
    ids, captions_lists = table["id"], table["captions"]

    pairs = []  # (query_text, ground_truth_id)
    for pid, caps in zip(ids, captions_lists):
        for cap in caps[1:]:  # hold out non-canonical captions
            pairs.append((cap, pid))
    random.shuffle(pairs)
    pairs = pairs[: args.n_queries]
    print(f"Evaluating on {len(pairs)} held-out caption queries")

    encoder = ClipTextEncoder()
    client = QdrantClient(url=args.qdrant_url)

    ranks = []
    latencies_ms = []
    for text, gt_id in pairs:
        t0 = time.perf_counter()
        vector = encoder.encode_one(text)
        hits = client.query_points(
            collection_name=COLLECTION, query=vector, limit=args.top_k
        ).points
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        hit_ids = [h.id for h in hits]
        rank = hit_ids.index(gt_id) + 1 if gt_id in hit_ids else args.top_k + 1
        ranks.append(rank)

    ranks = np.array(ranks)
    recall_at = lambda k: float((ranks <= k).mean())
    mrr = float((1.0 / ranks).mean())
    median_rank = float(np.median(ranks))

    metrics = {
        "n_queries": len(pairs),
        "recall@1": recall_at(1),
        "recall@5": recall_at(5),
        "recall@10": recall_at(10),
        "mrr": mrr,
        "median_rank": median_rank,
        "mean_query_latency_ms": float(np.mean(latencies_ms)),
        "p95_query_latency_ms": float(np.percentile(latencies_ms, 95)),
    }
    print(json.dumps(metrics, indent=2))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2))
    print(f"Saved metrics to {out_path}")


if __name__ == "__main__":
    main()
