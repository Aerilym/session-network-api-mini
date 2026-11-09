"""GET /prices/<token>/<period> — exact response contract.

Granularity is contract: 1h/1d are served raw; 7d is downsampled to the
last real observation per epoch-aligned hour bucket; 30d to the last per
4-hour bucket. All returned rows are genuine recorded observations — no
averaged or fabricated prices.
"""
import time

from conftest import NOW, TOKEN
from price import PriceDB

HOUR = 3600


def _seed():
    u = int(time.time())
    return [
        PriceDB(TOKEN, 0.05, 1.0, u - 30),          # inside 1h
        PriceDB(TOKEN, 0.04, 0.9, u - HOUR - 60),   # outside 1h, inside 7d
        PriceDB(TOKEN, 0.03, 0.8, u - HOUR * 24 * 8),  # outside 7d, inside 30d
    ]


def test_prices_1h_window_exact(make_app):
    app, _ = make_app(price_rows=_seed())
    r = app.test_client().get("/prices/{}/1h".format(TOKEN))
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"prices", "t"}
    assert isinstance(body["t"], int)
    assert abs(body["t"] - time.time()) <= 5
    row = body["prices"][0]
    assert set(row) == {"token", "price", "market_cap", "updated_at"}
    assert row["price"] == 0.05
    assert abs(row["updated_at"] - (NOW - 30)) <= 5


def test_prices_window_filtering_and_newest_first_order(make_app):
    # Timestamps anchored to bucket boundaries so results are deterministic
    # regardless of where "now" falls within an hour / 4-hour bucket.
    u = int(time.time())
    # Anchor to the 4-hour boundary: rows must be deterministic under BOTH
    # the hourly (7d) and 4-hourly (30d) bucket sizes.
    four = (u // (4 * HOUR)) * (4 * HOUR)
    rows = [
        PriceDB(TOKEN, 0.05, 1.0, four - HOUR),
        PriceDB(TOKEN, 0.04, 0.9, four - 4 * HOUR - HOUR),
        PriceDB(TOKEN, 0.03, 0.8, four - 8 * 24 * HOUR - HOUR),  # beyond 7d, inside 30d
    ]
    app, _ = make_app(price_rows=rows)
    client = app.test_client()

    seven_d = client.get("/prices/{}/7d".format(TOKEN)).get_json()["prices"]
    assert [p["price"] for p in seven_d] == [0.05, 0.04]

    thirty_d = client.get("/prices/{}/30d".format(TOKEN)).get_json()["prices"]
    assert [p["price"] for p in thirty_d] == [0.05, 0.04, 0.03]


def test_prices_7d_downsampled_last_observation_per_hour_bucket(make_app):
    u = int(time.time())
    hour = (u // HOUR) * HOUR
    rows = [
        PriceDB(TOKEN, 0.01, 1.0, hour - HOUR - 120),  # bucket H-1, older
        PriceDB(TOKEN, 0.02, 2.0, hour - HOUR - 60),   # bucket H-1, newest
        PriceDB(TOKEN, 0.03, 3.0, hour - 2 * HOUR - 60),  # bucket H-2, newest
        PriceDB(TOKEN, 0.04, 4.0, hour - 2 * HOUR - 120),  # bucket H-2, older
    ]
    app, _ = make_app(price_rows=rows)
    body = app.test_client().get("/prices/{}/7d".format(TOKEN)).get_json()
    # One real row per bucket — the newest one — newest-first.
    assert [p["price"] for p in body["prices"]] == [0.02, 0.03]
    assert body["prices"][0]["updated_at"] == hour - HOUR - 60


def test_prices_30d_downsampled_last_observation_per_4h_bucket(make_app):
    u = int(time.time())
    four = (u // (4 * HOUR)) * (4 * HOUR)
    rows = [
        PriceDB(TOKEN, 0.01, 1.0, four - 4 * HOUR - 120),  # bucket B-1, older
        PriceDB(TOKEN, 0.02, 2.0, four - 4 * HOUR - 60),   # bucket B-1, newest
        PriceDB(TOKEN, 0.03, 3.0, four - 60),              # bucket B, newest
        PriceDB(TOKEN, 0.04, 4.0, four - 6000),            # bucket B, older
    ]
    app, _ = make_app(price_rows=rows)
    body = app.test_client().get("/prices/{}/30d".format(TOKEN)).get_json()
    assert [p["price"] for p in body["prices"]] == [0.03, 0.02]
    assert body["prices"][0]["updated_at"] == four - 60


def test_prices_1d_served_raw(make_app):
    u = int(time.time())
    hour = (u // HOUR) * HOUR
    prices = [1.1, 1.2, 1.3, 1.4, 1.5]
    rows = [PriceDB(TOKEN, prices[i], float(i), hour - HOUR + i * 60)
            for i in range(5)]  # all within one hour bucket
    app, _ = make_app(price_rows=rows)
    body = app.test_client().get("/prices/{}/1d".format(TOKEN)).get_json()
    # 1d is raw: every row returned, newest first, nothing bucketed away.
    assert [p["price"] for p in body["prices"]] == [1.5, 1.4, 1.3, 1.2, 1.1]


def test_prices_extended_periods_window_filtering(make_app):
    u = int(time.time())
    day = 24 * HOUR
    rows = [
        PriceDB(TOKEN, 0.05, 1.0, u - 30 * day),    # inside 3mo
        PriceDB(TOKEN, 0.04, 0.9, u - 100 * day),   # outside 3mo, inside 6mo
        PriceDB(TOKEN, 0.03, 0.8, u - 200 * day),   # outside 6mo, inside 1y
        PriceDB(TOKEN, 0.02, 0.7, u - 400 * day),   # outside 1y
    ]
    app, _ = make_app(price_rows=rows)
    client = app.test_client()

    for period, expected in [
        ("3mo", [0.05]),
        ("6mo", [0.05, 0.04]),
        ("1y", [0.05, 0.04, 0.03]),
    ]:
        body = client.get("/prices/{}/{}".format(TOKEN, period)).get_json()
        assert [p["price"] for p in body["prices"]] == expected, period


def test_prices_extended_periods_downsampled(make_app):
    # One pinned bucket test per new period: newest real observation per
    # 12h (3mo) / 24h (6mo) / 48h (1y) bucket, newest-first.
    u = int(time.time())
    for period, bucket in [("3mo", 12 * HOUR), ("6mo", 24 * HOUR),
                           ("1y", 48 * HOUR)]:
        b = (u // bucket) * bucket
        rows = [
            PriceDB(TOKEN, 0.01, 1.0, b - bucket - 120),  # older bucket, older row
            PriceDB(TOKEN, 0.02, 2.0, b - bucket - 60),   # older bucket, newest
            PriceDB(TOKEN, 0.03, 3.0, b - 60),            # latest bucket, newest
            PriceDB(TOKEN, 0.04, 4.0, b - 6000),          # latest bucket, older
        ]
        app, _ = make_app(price_rows=rows)
        body = app.test_client().get(
            "/prices/{}/{}".format(TOKEN, period)).get_json()
        assert [p["price"] for p in body["prices"]] == [0.03, 0.02], period
        assert body["prices"][0]["updated_at"] == b - 60, period


def test_prices_no_data_for_configured_token(make_app):
    app, _ = make_app()  # no rows at all
    body = app.test_client().get("/prices/{}/30d".format(TOKEN)).get_json()
    assert body["prices"] == []


def test_prices_invalid_period_400(make_app):
    app, _ = make_app(price_rows=_seed())
    r = app.test_client().get("/prices/{}/1w".format(TOKEN))
    assert r.status_code == 400
    assert b"Invalid period" in r.data


def test_prices_unknown_token_404(make_app):
    app, _ = make_app(price_rows=_seed())
    r = app.test_client().get("/prices/not-a-token/1h")
    assert r.status_code == 404
    assert b"not found" in r.data
