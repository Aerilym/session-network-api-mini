import logging
import threading
import time
from copy import copy
from typing import Callable


class Cache:
    """In-process TTL cache with stale-while-revalidate semantics.

    - Fresh hit: return immediately, no upstream call.
    - Expired but within ``stale_max_age``: serve the stale value and
      refresh in a single background thread.
    - Cold: blocking single-flight fetch — one getter runs, concurrent
      misses wait on the same per-key lock.
    - Getter failure: negative-cached for ``negative_ttl`` seconds; any
      stale value keeps being served. Without one, the exception
      propagates to the caller.
    - Stale values past ``stale_max_age`` are not servable and are
      evicted; requests then take the blocking path again.
    """

    def __init__(self, default_ttl: int = 0, stale_max_age: float | None = 3600.0,
                 negative_ttl: float = 5.0):
        self.log = logging.getLogger("cache")
        self.default_ttl = default_ttl
        # Seconds past expiry a stale value may still be served. None = forever.
        self.stale_max_age = stale_max_age
        # Seconds a failed fetch is remembered before the upstream is retried.
        self.negative_ttl = negative_ttl
        self.store: dict = {}
        self.expiry: dict = {}
        self._negative: dict = {}        # key -> retry-not-before timestamp
        self._negative_errors: dict = {}  # key -> exception from last failure
        self._refresh_locks: dict[str, threading.Lock] = {}
        self._refresh_locks_lock = threading.Lock()

    def _get_refresh_lock(self, key: str) -> threading.Lock:
        # Use setdefault to make the check-then-set atomic under the GIL.
        # Additionally protected by _refresh_locks_lock for portability.
        with self._refresh_locks_lock:
            self._refresh_locks.setdefault(key, threading.Lock())
            return self._refresh_locks[key]

    def _stale_servable(self, key: str, now: float) -> bool:
        if key not in self.store:
            return False
        exp = self.expiry.get(key, 0)
        if self.stale_max_age is None:
            return True
        return now < exp + self.stale_max_age

    def _negative_active(self, key: str, now: float) -> Exception | None:
        if self._negative.get(key, 0) > now:
            return self._negative_errors.get(key)
        return None

    def _record_failure(self, key: str, exc: Exception) -> None:
        self._negative[key] = time.time() + self.negative_ttl
        self._negative_errors[key] = exc

    def _clear_failure(self, key: str) -> None:
        self._negative.pop(key, None)
        self._negative_errors.pop(key, None)

    def _spawn_refresh(self, key, getter, getter_args, ttl, invalidate_timestamp) -> None:
        # Retry pacing: if the upstream just failed, don't spawn a refresh
        # per request; the next attempt happens after negative_ttl expires.
        if self._negative.get(key, 0) > time.time():
            return
        lock = self._get_refresh_lock(key)
        if not lock.acquire(blocking=False):
            return  # a refresh is already in flight

        def _refresh(lock=lock, key=key, getter=getter,
                     getter_args=getter_args, ttl=ttl,
                     invalidate_timestamp=invalidate_timestamp):
            try:
                data = getter(
                    getter_args) if getter_args is not None else getter()
                self._clear_failure(key)
                self._set(key, data, ttl, invalidate_timestamp)
            except Exception as exc:
                self._record_failure(key, exc)
                self.log.warning(
                    "Background refresh failed for '%s': %s", key, exc)
            finally:
                lock.release()

        threading.Thread(target=_refresh, daemon=True,
                         name="cache-refresh-{}".format(key)).start()

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

        # Fresh hit.
        if key in self.store and self.expiry.get(key, 0) > now:
            return self.store[key]

        # Stale-while-revalidate: serve the stale value immediately and
        # refresh in the background. Checked BEFORE any eviction so expired
        # keys remain servable (the original implementation evicted first,
        # making this branch unreachable).
        if stale_ok and self._stale_servable(key, now):
            self._spawn_refresh(key, getter, getter_args, ttl,
                                invalidate_timestamp)
            return self.store[key]

        self._evict_stale(now)

        # Cold (or past the stale bound): single-flight blocking fetch.
        lock = self._get_refresh_lock(key)
        with lock:
            now = time.time()
            # Another thread may have populated the key while we waited.
            if key in self.store and self.expiry.get(key, 0) > now:
                return self.store[key]

            # A recent failure: fail fast instead of hammering the upstream.
            recent_error = self._negative_active(key, now)
            if recent_error is not None:
                raise recent_error

            try:
                data = getter(
                    getter_args) if getter_args is not None else getter()
            except Exception as exc:
                self._record_failure(key, exc)
                raise
            self._clear_failure(key)
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
        # Evict only values beyond the stale-serving bound; expired-but-
        # servable values must survive so SWR/stale-on-error can use them.
        for key, exp in copy(self.expiry).items():
            if self.stale_max_age is not None and now >= exp + self.stale_max_age:
                self.store.pop(key, None)
                self.expiry.pop(key, None)
                with self._refresh_locks_lock:
                    self._refresh_locks.pop(key, None)
        for key, until in copy(self._negative).items():
            if until <= now:
                self._negative.pop(key, None)
                self._negative_errors.pop(key, None)
