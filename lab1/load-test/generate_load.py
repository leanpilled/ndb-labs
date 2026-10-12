import argparse
import random
import string
import threading
import time

from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError

CATEGORIES = ["alpha", "beta", "gamma", "delta", "epsilon"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--uri",
        default="mongodb://192.168.252.2:27017",
        help="MongoDB connection URI (default: %(default)s)",
    )
    parser.add_argument(
        "--database", default="loadtest", help="Database to use (default: %(default)s)"
    )
    parser.add_argument(
        "--collection", default="items", help="Collection to use (default: %(default)s)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=50,
        help="Concurrent worker connections, i.e. target connection count (default: %(default)s)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=120,
        help="How long to run, in seconds. 0 = run until Ctrl+C (default: %(default)s)",
    )
    parser.add_argument(
        "--seed-docs",
        type=int,
        default=5000,
        help="Documents to seed into the collection before starting load (default: %(default)s)",
    )
    return parser.parse_args()


def random_text(length: int = 32) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def seed_collection(uri: str, db_name: str, coll_name: str, count: int) -> None:
    client = MongoClient(uri)
    try:
        coll = client[db_name][coll_name]
        coll.create_index([("category", ASCENDING)])
        existing = coll.estimated_document_count()
        to_insert = max(0, count - existing)
        if to_insert == 0:
            print(
                f"Collection {db_name}.{coll_name} already has {existing} docs, skipping seed"
            )
            return
        print(f"Seeding {to_insert} documents into {db_name}.{coll_name} ...")
        batch = []
        for _ in range(to_insert):
            batch.append(
                {
                    "category": random.choice(CATEGORIES),
                    "value": random.randint(1, 100_000),
                    "text": random_text(),
                    "created_at": time.time(),
                }
            )
            if len(batch) >= 1000:
                coll.insert_many(batch)
                batch.clear()
        if batch:
            coll.insert_many(batch)
        print("Seeding done")
    finally:
        client.close()


def worker_loop(
    worker_id: int,
    uri: str,
    db_name: str,
    coll_name: str,
    stop_at: float,
) -> None:
    client = MongoClient(uri, maxPoolSize=1, minPoolSize=1)
    coll = client[db_name][coll_name]
    rng = random.Random(worker_id)
    try:
        while time.monotonic() < stop_at:
            op = rng.choices(
                [
                    "insert",
                    "find_indexed",
                    "find_unindexed",
                    "aggregate",
                    "update",
                    "delete",
                ],
                weights=[20, 25, 15, 15, 17, 8],
                k=1,
            )[0]
            try:
                if op == "insert":
                    coll.insert_one(
                        {
                            "category": rng.choice(CATEGORIES),
                            "value": rng.randint(1, 100_000),
                            "text": random_text(),
                            "created_at": time.time(),
                        }
                    )
                elif op == "find_indexed":
                    category = rng.choice(CATEGORIES)
                    list(coll.find({"category": category}).limit(20))
                elif op == "find_unindexed":
                    threshold = rng.randint(1, 100_000)
                    list(coll.find({"value": {"$gte": threshold}}).limit(20))
                elif op == "aggregate":
                    list(
                        coll.aggregate(
                            [
                                {"$match": {"category": rng.choice(CATEGORIES)}},
                                {
                                    "$group": {
                                        "_id": "$category",
                                        "avg_value": {"$avg": "$value"},
                                        "count": {"$sum": 1},
                                    }
                                },
                                {"$sort": {"avg_value": -1}},
                            ]
                        )
                    )
                elif op == "update":
                    coll.update_one(
                        {"category": rng.choice(CATEGORIES)},
                        {"$set": {"text": random_text(), "updated_at": time.time()}},
                    )
                elif op == "delete":
                    coll.delete_one({"category": rng.choice(CATEGORIES)})
            except PyMongoError as exc:
                print(f"[worker {worker_id}] {op} failed: {exc}")
    finally:
        client.close()


def main() -> None:
    args = parse_args()

    seed_collection(args.uri, args.database, args.collection, args.seed_docs)

    stop_at = time.monotonic() + args.duration if args.duration > 0 else float("inf")

    print(
        f"Starting {args.workers} workers against {args.uri} "
        f"({'until Ctrl+C' if args.duration <= 0 else f'{args.duration:.0f}s'}) ..."
    )

    threads = [
        threading.Thread(
            target=worker_loop,
            args=(i, args.uri, args.database, args.collection, stop_at),
            daemon=True,
        )
        for i in range(args.workers)
    ]
    for t in threads:
        t.start()

    try:
        for t in threads:
            while t.is_alive():
                t.join(timeout=1)
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C) ...")

    print("Done.")


if __name__ == "__main__":
    main()
