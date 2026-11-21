"""작은 LRU 캐시. 임베딩과 검색 결과에 쓴다. 적중·실패 횟수를 센다."""
from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Any, Callable, Hashable


class LRU:
    def __init__(self, size: int = 512):
        self.size = size
        self._d: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def get_or_set(self, key: Hashable, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                self.hits += 1
                return self._d[key]
        value = fn()
        with self._lock:
            self.misses += 1
            self._d[key] = value
            if len(self._d) > self.size:
                self._d.popitem(last=False)
        return value

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses, "size": len(self._d)}
