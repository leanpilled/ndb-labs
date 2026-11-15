"""Load the flickr30k-lance dataset into Qdrant and extract images to disk.

Usage:
    python scripts/ingest_qdrant.py --limit 0   # 0 = full dataset
"""

import argparse
from pathlib import Path

import lance
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from tqdm import tqdm

COLLECTION = "flickr30k"
VECTOR_SIZE = 512


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
    parser.add_argument(
        "--images-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "images"),
    )
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--limit", type=int, default=0, help="0 means no limit (full dataset)"
    )
    parser.add_argument(
        "--recreate", action="store_true", help="Drop and recreate the collection"
    )
    parser.add_argument("--extract-images", action="store_true", default=True)
    parser.add_argument(
        "--no-extract-images", dest="extract_images", action="store_false"
    )
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    ds = lance.dataset(args.dataset)
    total_rows = ds.count_rows()
    n_rows = total_rows if args.limit in (0, None) else min(args.limit, total_rows)
    print(f"Dataset rows: {total_rows}, ingesting: {n_rows}")

    client = QdrantClient(url=args.qdrant_url)

    if args.recreate or not client.collection_exists(COLLECTION):
        client.recreate_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(
                size=VECTOR_SIZE, distance=qm.Distance.COSINE
            ),
        )
        print(f"(Re)created collection '{COLLECTION}' with COSINE distance")

    columns = ["id", "image_id", "filename", "caption", "captions", "image_emb"]
    if args.extract_images:
        columns.append("image")

    scanner = ds.scanner(columns=columns, batch_size=args.batch_size)
    seen = 0
    pbar = tqdm(total=n_rows, desc="ingesting")
    for batch in scanner.to_batches():
        if seen >= n_rows:
            break
        table = batch.to_pydict()
        n = len(table["id"])
        points = []
        for i in range(n):
            if seen >= n_rows:
                break
            point_id = int(table["id"][i])
            vector = list(table["image_emb"][i])
            payload = {
                "image_id": table["image_id"][i],
                "filename": table["filename"][i],
                "caption": table["caption"][i],
                "captions": table["captions"][i],
            }
            if args.extract_images:
                img_path = images_dir / f"{point_id}.jpg"
                if not img_path.exists():
                    img_path.write_bytes(table["image"][i])
                payload["image_path"] = img_path.name
            points.append(qm.PointStruct(id=point_id, vector=vector, payload=payload))
            seen += 1
        if points:
            client.upsert(collection_name=COLLECTION, points=points)
            pbar.update(len(points))
    pbar.close()

    count = client.count(COLLECTION, exact=True).count
    print(f"Qdrant collection '{COLLECTION}' now has {count} points")


if __name__ == "__main__":
    main()
