"""A cache of decoded payloads bounded by the bytes it keeps.

Codecs decode the same payloads many times while a project opens, so they
keep the results; keyed by the payload's SHA-256 (not by the payload itself)
and limited by the total size of what they keep, the least recently used
results go first.
"""

from __future__ import annotations

from collections import OrderedDict
import hashlib
from typing import Callable, Hashable


class BoundedCache:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[tuple[bytes, Hashable], bytes] = OrderedDict()
        self._size = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def size(self) -> int:
        """The bytes of the results kept now."""
        return self._size

    def get(self, payload: bytes, extra: Hashable, compute: Callable[[], bytes]) -> bytes:
        """The result for `payload` (and `extra`), computed once while kept."""
        key = (hashlib.sha256(payload).digest(), extra)
        found = self._entries.get(key)
        if found is not None:
            self._entries.move_to_end(key)
            return found
        result = compute()
        if len(result) <= self.max_bytes:
            self._entries[key] = result
            self._size += len(result)
            while self._size > self.max_bytes:
                _key, dropped = self._entries.popitem(last=False)
                self._size -= len(dropped)
        return result

    def clear(self) -> None:
        self._entries.clear()
        self._size = 0
