"""Cache semantic pins for the Phase-1 remediation.

Each test pins one behaviour of the reworked stale-while-revalidate cache.
These are internal semantics — the endpoint-facing contract is pinned in
test_info.py / test_prices.py / test_onion.py and must not change.
"""
import threading
import time

import pytest

from cache import Cache


def expired_cache(value="v1", stale_max_age=3600.0):
    """Cache pre-populated with a value whose expiry is already in the past."""
    c = Cache(default_ttl=0, stale_max_age=stale_max_age)
    c.set("k", value, ttl=10)
    c.expiry["k"] = time.time() - 1  # force expiry without sleeping
    return c


def test_swrserves_stale_value_and_refreshes_in_background():
    c = expired_cache("v1")
    getter_started = threading.Event()
    getter_finish = threading.Event()

    def getter():
        getter_started.set()
        getter_finish.wait(timeout=5)  # hold the refresh open
        return "v2"

    result = []
    caller = threading.Thread(target=lambda: result.append(
        c.get("k", getter, ttl=10, stale_ok=True)))
    caller.start()
    assert getter_started.wait(timeout=5)
    caller.join(timeout=5)
    # The caller got the stale value back while the refresh was still running.
    assert result == ["v1"]

    getter_finish.set()
    deadline = time.time() + 5
    while c.get_cached_only("k") != "v2" and time.time() < deadline:
        time.sleep(0.005)
    assert c.get_cached_only("k") == "v2"


def test_stale_served_when_background_refresh_fails():
    c = expired_cache("v1")
    boom = RuntimeError("upstream down")

    def getter():
        raise boom

    for _ in range(3):
        assert c.get("k", getter, ttl=10, stale_ok=True) == "v1"


def test_stale_beyond_max_age_is_not_served_and_raises():
    c = expired_cache("v1", stale_max_age=3600.0)
    c.expiry["k"] = time.time() - 3600.0 - 10  # beyond the bound
    calls = []

    def getter():
        calls.append(1)
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        c.get("k", getter, ttl=10, stale_ok=True)
    assert len(calls) == 1


def test_cold_miss_single_flight():
    c = Cache(default_ttl=60)
    calls = []
    calls_lock = threading.Lock()
    released = threading.Event()

    def getter():
        with calls_lock:
            calls.append(1)
        released.wait(timeout=5)  # hold the first fetch open
        return "v"

    results = []

    def worker():
        results.append(c.get("k", getter, ttl=60))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.1)  # let concurrent misses pile up on the per-key lock
    released.set()
    for t in threads:
        t.join(timeout=5)

    assert results == ["v"] * 8
    assert len(calls) == 1


def test_failure_negative_cached_fails_fast():
    c = Cache(default_ttl=60)
    calls = []

    def getter():
        calls.append(1)
        raise ConnectionRefusedError("refused")

    with pytest.raises(ConnectionRefusedError):
        c.get("k", getter, ttl=60)
    # Immediate retry: negative cache suppresses a second upstream attempt.
    with pytest.raises(ConnectionRefusedError):
        c.get("k", getter, ttl=60)
    assert len(calls) == 1


def test_success_clears_negative_cache():
    c = Cache(default_ttl=60)
    state = {"fail": True}
    calls = []

    def getter():
        calls.append(1)
        if state["fail"]:
            raise ConnectionRefusedError("refused")
        return "ok"

    with pytest.raises(ConnectionRefusedError):
        c.get("k", getter, ttl=60)
    state["fail"] = False
    # Negative TTL is 5s; expire it without sleeping.
    c._negative["k"] = time.time() - 0.001
    assert c.get("k", getter, ttl=60) == "ok"
    assert len(calls) == 2
    # And the failure marker is gone.
    assert "k" not in c._negative
