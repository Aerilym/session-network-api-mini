import logging
import threading
import time
from copy import copy
from typing import Callable


class Cache:
    def __init__(self, default_ttl: int = 0):
        self.log = logging.getLogger("cache")
        self.default_ttl = default_ttl
        self.store: dict = {}
        self.expiry: dict = {}
        self._refresh_locks: dict[str, threading.Lock] = {}
        self._refresh_locks_lock = threading.Lock()

    def _get_refresh_lock(self, key: str) -> threading.Lock:
        # Use setdefault to make the check-then-set atomic under the GIL.
        # Additionally protected by _refresh_locks_lock for portability.
        with self._refresh_locks_lock:
            self._refresh_locks.setdefault(key, threading.Lock())
            return self._refresh_locks[key]

    def get(
        self,
        key: str,
        getter: Callable,
        getter_args=None,
        ttl: int | None = None,
        invalidate_timestamp: int | None = None,
        stale_ok: bool = False,
    ):
        if ttl is None or ttl < 0:
            ttl = self.default_ttl
        now = time.time()

        if key in self.store and self.expiry.get(key, 0) > now:
            return self.store[key]

        self._evict_stale(now)

        # Stale-while-revalidate: if a stale value exists, return it immediately
        # and refresh in the background. Fall through to blocking fetch on cold start.
        if stale_ok and key in self.store:
            lock = self._get_refresh_lock(key)
            if lock.acquire(blocking=False):
                def _refresh(lock=lock, key=key, getter=getter,
                             getter_args=getter_args, ttl=ttl,
                             invalidate_timestamp=invalidate_timestamp):
                    try:
                        data = getter(
                            getter_args) if getter_args is not None else getter()
                        self._set(key, data, ttl, invalidate_timestamp)
                    except Exception as exc:
                        self.log.warning(
                            "Background refresh failed for '%s': %s", key, exc)
                    finally:
                        lock.release()
                threading.Thread(target=_refresh, daemon=True,
                                 name="cache-refresh-{}".format(key)).start()
            return self.store[key]

        data = getter(getter_args) if getter_args is not None else getter()
        self._set(key, data, ttl, invalidate_timestamp)
        return data

    def get_cached_only(self, key: str):
        now = time.time()
        if key in self.store and self.expiry.get(key, 0) > now:
            return self.store[key]
        return None

    def set(
        self,
        key: str,
        data=None,
        ttl: int | None = None,
        invalidate_timestamp: int | None = None,
    ):
        self._set(key, data, ttl, invalidate_timestamp)

    def get_stale_timestamp(self, key: str) -> float:
        return self.expiry.get(key, 0)

    def _set(
        self,
        key: str,
        data,
        ttl: int | None,
        invalidate_timestamp: int | None,
    ):
        now = time.time()
        if invalidate_timestamp is not None:
            expire = invalidate_timestamp
        else:
            if ttl is None or ttl < 0:
                ttl = self.default_ttl
            expire = now + ttl

        self.store[key] = data
        self.expiry[key] = expire

    def _evict_stale(self, now: float):
        for key, exp in copy(self.expiry).items():
            if exp < now:
                self.store.pop(key, None)
                self.expiry.pop(key, None)
                with self._refresh_locks_lock:
                    self._refresh_locks.pop(key, None)
