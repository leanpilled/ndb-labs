import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

COMPOSE_DIR = Path(__file__).parent
APP1 = "http://localhost:8001"
APP2 = "http://localhost:8002"
APP3 = "http://localhost:8003"
ITEM_ID = 1


def request(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def wait_for(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            request("GET", url)
            return
        except (urllib.error.URLError, ConnectionError) as e:
            last_error = e
            time.sleep(1)
    raise TimeoutError(f"{url} did not become ready: {last_error}")


def compose_up() -> None:
    subprocess.run(
        ["docker", "compose", "up", "-d", "--build"], cwd=COMPOSE_DIR, check=True
    )


def compose_down() -> None:
    subprocess.run(["docker", "compose", "down"], cwd=COMPOSE_DIR, check=True)


def test_cache_invalidation_propagates_via_pubsub():
    compose_up()
    try:
        for base in (APP1, APP2, APP3):
            wait_for(f"{base}/cache")

        assert request("GET", f"{APP1}/items/{ITEM_ID}")["source"] == "db"
        assert request("GET", f"{APP1}/items/{ITEM_ID}")["source"] == "cache"

        assert request("GET", f"{APP2}/items/{ITEM_ID}")["source"] == "db"
        assert request("GET", f"{APP2}/items/{ITEM_ID}")["source"] == "cache"

        updated = request(
            "PUT", f"{APP1}/items/{ITEM_ID}", {"name": "Laptop Pro", "price": 1999.0}
        )
        assert updated["updated"] is True

        time.sleep(0.5)

        for base in (APP2, APP3):
            result = request("GET", f"{base}/items/{ITEM_ID}")
            assert result["source"] == "db", f"{base} served stale cache"
            assert result["item"]["name"] == "Laptop Pro"
            assert result["item"]["price"] == 1999.0

        print("OK: cache invalidation propagated to all instances via Redis pub/sub")
    finally:
        compose_down()


if __name__ == "__main__":
    test_cache_invalidation_propagates_via_pubsub()
