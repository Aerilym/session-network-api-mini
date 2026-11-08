"""GET /info — exact response contract.

Exact dict equality is deliberate: it catches added keys, removed keys,
type changes, and new nullable values in one assertion.
"""
import time

from conftest import (
    CIRCULATING_SUPPLY, CONTRACT_ADDRESS, MARKET_CAP, NOW, POLL_RATE, PRICE,
    TOKEN, WEB3_REWARD_POOL,
)
from price import PriceDB


def _client(app):
    return app.test_client()


def test_info_exact_shape_and_values(info_app):
    app, _ = info_app
    r = _client(app).get("/info")
    assert r.status_code == 200
    assert r.content_type.startswith("application/json")
    body = r.get_json()

    assert set(body) == {"token", "network", "price", "t"}
    assert body["token"] == {
        "staking_requirement": 25000,
        "staking_reward_pool": WEB3_REWARD_POOL,
        "contract_address": CONTRACT_ADDRESS,
        "circulating_supply": CIRCULATING_SUPPLY,
    }
    assert body["network"] == {
        "network_size": 1000,
        "network_staked_tokens": 25_000_000.0,
        "network_staked_usd": 25_000_000.0 * PRICE,
    }
    # t_price/t_stale align to the price record's own timestamp
    assert body["price"] == {
        "usd": PRICE,
        "usd_market_cap": MARKET_CAP,
        "t_price": NOW - 60,
        "t_stale": NOW - 60 + POLL_RATE,
    }
    assert isinstance(body["t"], int)
    assert abs(body["t"] - time.time()) <= 5


def test_info_upstream_fetch_counts_and_cache_within_ttl(info_app):
    app, calls = info_app
    client = _client(app)
    assert client.get("/info").status_code == 200
    assert calls == {"ssb": 1, "supply": 1, "web3": 1}
    # Second hit within the 60s TTL: served from cache, no upstream calls.
    assert client.get("/info").status_code == 200
    assert calls == {"ssb": 1, "supply": 1, "web3": 1}


def test_info_market_cap_fallback_when_cointgecko_returns_zero(make_app):
    row = PriceDB(token=TOKEN, price=PRICE, market_cap=0.0, updated_at=NOW - 60)
    app, _ = make_app(price_rows=[row])
    body = _client(app).get("/info").get_json()
    assert body["price"]["usd_market_cap"] == CIRCULATING_SUPPLY * PRICE


def test_info_market_cap_null_when_fallback_also_zero(make_app):
    # Existing nullability (the only one) — pinned deliberately.
    row = PriceDB(token=TOKEN, price=0.0, market_cap=0.0, updated_at=NOW - 60)
    app, _ = make_app(price_rows=[row])
    body = _client(app).get("/info").get_json()
    assert body["price"]["usd_market_cap"] is None
    assert body["network"]["network_staked_usd"] == 0.0


def test_info_500_when_no_price_data(make_app):
    # Empty prices DB: current behaviour is a hard 500 via flask.abort.
    app, _ = make_app()
    assert _client(app).get("/info").status_code == 500


def test_info_500_when_web3_rpc_down(make_app):
    # TRIPWIRE: pins the incident behaviour (err.log) where an Arbitrum RPC
    # failure 500s the whole endpoint. The planned degradation fix is
    # expected to change this to 200 with staking_reward_pool served from
    # cache/null — update this test consciously alongside that fix.
    app, _ = make_app(web3_fail=ConnectionRefusedError("[Errno 111] refused"))
    assert _client(app).get("/info").status_code == 500


def test_info_500_when_ssb_api_down(make_app):
    # TRIPWIRE: same class as above — upstream staking-data failure currently
    # 500s /info.
    app, _ = make_app(ssb_ok=False)
    assert _client(app).get("/info").status_code == 500


def test_info_circulating_supply_null_when_fetch_fails(make_app):
    # Existing graceful path: supply failure degrades to null, 200 stays.
    from price import PriceDB
    row = PriceDB(token=TOKEN, price=PRICE, market_cap=MARKET_CAP, updated_at=NOW - 60)
    app, _ = make_app(price_rows=[row], supply_ok=False)
    r = _client(app).get("/info")
    assert r.status_code == 200
    body = r.get_json()
    assert body["token"]["circulating_supply"] is None
    # market-cap fallback then also uses supply=0 -> usd_market_cap stays as-is
    assert body["price"]["usd_market_cap"] == MARKET_CAP


def test_info_web3_disabled_shape(make_app):
    from price import PriceDB
    row = PriceDB(token=TOKEN, price=PRICE, market_cap=MARKET_CAP, updated_at=NOW - 60)
    app, _ = make_app(price_rows=[row],
                      overrides={"disable_web3_client": True})
    body = _client(app).get("/info").get_json()
    assert body["token"]["staking_reward_pool"] is None
    assert body["token"]["contract_address"] is None
    assert body["network"] == {
        "network_size": 1000,
        "network_staked_tokens": 25_000_000.0,
        "network_staked_usd": 25_000_000.0 * PRICE,
    }
