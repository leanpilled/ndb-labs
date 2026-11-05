import logging
from typing import Any, Optional

logger = logging.getLogger("cache")

_store: dict[int, dict[str, Any]] = {}


def get(item_id: int) -> Optional[dict[str, Any]]:
    return _store.get(item_id)


def set(item_id: int, value: dict[str, Any]) -> None:
    _store[item_id] = value


def invalidate(item_id: int) -> bool:
    return _store.pop(item_id, None) is not None


def snapshot() -> dict[int, dict[str, Any]]:
    return dict(_store)
