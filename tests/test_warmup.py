"""Phase 2: boot warmup — caches populated before first request."""
import time

from conftest import MARKET_CAP, NOW, PRICE, TOKEN
from price import PriceDB


def test_warmup_populates_caches_before_first_request(make_app):
    row = PriceDB(token=TOKEN, price=PRICE, market_cap=MARKET_CAP,
                  updated_at=NOW - 60)
    app, calls = make_app(price_rows=[row],
                          overrides={"enable_warmup": True})

    assert app.config["WARMUP_DONE"].wait(timeout=5)

    # Warmup consumed exactly one fetch of each upstream.
    warmed = dict(calls)
    assert warmed == {"ssb": 1, "supply": 1, "web3": 1}

    # First request served entirely from warmed caches: no further calls.
    r = app.test_client().get("/info")
    assert r.status_code == 200
    assert calls == warmed
    body = r.get_json()
    assert body["token"]["staking_reward_pool"] == 45_000_000
    assert body["price"]["usd"] == PRICE


def test_warmup_retries_until_upstream_recovers(make_app, monkeypatch):
    row = PriceDB(token=TOKEN, price=PRICE, market_cap=MARKET_CAP,
                  updated_at=NOW - 60)
    from conftest import Web3Stub

    app, calls = make_app(
        price_rows=[row],
        overrides={
            "enable_warmup": True,
            "warmup_retry_seconds": 0.05,
            "cache_negative_ttl_seconds": 0.05,
        },
        web3_fail=ConnectionRefusedError("cold boot, RPC down"),
    )
    try:
        # Warmup is failing (web3 down) — event not set, but it keeps retrying.
        assert not app.config["WARMUP_DONE"].is_set()
        # Upstream recovers; warmup completes on its next attempt.
        Web3Stub.fail = None
        assert app.config["WARMUP_DONE"].wait(timeout=5)
        assert app.test_client().get("/info").status_code == 200
    finally:
        Web3Stub.fail = None
