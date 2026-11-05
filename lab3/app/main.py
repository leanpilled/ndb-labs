import asyncio
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
import cache
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("app")

INSTANCE_ID = os.environ.get("INSTANCE_ID", "unknown")
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
INVALIDATION_CHANNEL = "cache-invalidate"

db_pool: asyncpg.Pool | None = None
redis_client: redis.Redis | None = None

cache_hits_total = Counter("cache_hits_total", "Local cache hits")
cache_misses_total = Counter("cache_misses_total", "Local cache misses")
cache_invalidations_total = Counter(
    "cache_invalidations_total", "Local cache entries invalidated via Redis pub/sub"
)
cache_size = Gauge("cache_size", "Number of items currently in the local cache")


async def listen_for_invalidations() -> None:
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(INVALIDATION_CHANNEL)
    logger.info("[%s] subscribed to '%s'", INSTANCE_ID, INVALIDATION_CHANNEL)
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        item_id = int(message["data"])
        removed = cache.invalidate(item_id)
        if removed:
            cache_invalidations_total.inc()
            cache_size.set(len(cache.snapshot()))
        logger.info(
            "[%s] PUB/SUB: invalidated item_id=%s from local cache (was_cached=%s)",
            INSTANCE_ID,
            item_id,
            removed,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    listener_task = asyncio.create_task(listen_for_invalidations())
    logger.info("[%s] started", INSTANCE_ID)
    yield
    listener_task.cancel()
    await redis_client.aclose()
    await db_pool.close()


app = FastAPI(title="Cache Invalidation Demo", lifespan=lifespan)
Instrumentator().instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False
)


class ItemUpdate(BaseModel):
    name: str
    price: float


@app.get("/items/{item_id}")
async def get_item(item_id: int):
    cached = cache.get(item_id)
    if cached is not None:
        cache_hits_total.inc()
        logger.info("[%s] CACHE HIT item_id=%s", INSTANCE_ID, item_id)
        return {"served_by": INSTANCE_ID, "source": "cache", "item": cached}

    cache_misses_total.inc()
    logger.info("[%s] CACHE MISS item_id=%s, loading from DB", INSTANCE_ID, item_id)
    row = await db_pool.fetchrow(
        "SELECT id, name, price FROM items WHERE id = $1", item_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="item not found")

    item = dict(row)
    cache.set(item_id, item)
    cache_size.set(len(cache.snapshot()))
    return {"served_by": INSTANCE_ID, "source": "db", "item": item}


@app.put("/items/{item_id}")
async def update_item(item_id: int, payload: ItemUpdate):
    result = await db_pool.execute(
        "UPDATE items SET name = $1, price = $2 WHERE id = $3",
        payload.name,
        payload.price,
        item_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="item not found")

    await redis_client.publish(INVALIDATION_CHANNEL, item_id)
    logger.info(
        "[%s] updated item_id=%s in DB, published invalidation event",
        INSTANCE_ID,
        item_id,
    )
    return {"served_by": INSTANCE_ID, "updated": True, "item_id": item_id}


@app.get("/cache")
async def cache_debug():
    return {"served_by": INSTANCE_ID, "cache": cache.snapshot()}
