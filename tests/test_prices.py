"""GET /prices/<token>/<period> — exact response contract."""
import time

from conftest import NOW, TOKEN
from price import PriceDB

HOUR = 3600
DAY = 86400


def _seed():
    u = int(time.time())
    return [
        PriceDB(TOKEN, 0.05, 1.0, u - 30),          # inside 1h
        PriceDB(TOKEN, 0.04, 0.9, u - HOUR - 60),   # outside 1h, inside 7d
        PriceDB(TOKEN, 0.03, 0.8, u - DAY * 8),     # outside 7d, inside 30d
    ]


def test_prices_1h_window_exact(make_app):
    app, _ = make_app(price_rows=_seed())
    r = app.test_client().get("/prices/{}/1h".format(TOKEN))
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"prices", "t"}
    assert isinstance(body["t"], int)
    assert abs(body["t"] - time.time()) <= 5
    assert body["prices"] == [
        {"token": TOKEN, "price": 0.05, "market_cap": 1.0,
         "updated_at": body["prices"][0]["updated_at"]},
    ]
    row = body["prices"][0]
    assert set(row) == {"token", "price", "market_cap", "updated_at"}
    assert abs(row["updated_at"] - (NOW - 30)) <= 5


def test_prices_window_filtering_and_newest_first_order(make_app):
    app, _ = make_app(price_rows=_seed())
    client = app.test_client()

    seven_d = client.get("/prices/{}/7d".format(TOKEN)).get_json()["prices"]
    assert [p["price"] for p in seven_d] == [0.05, 0.04]

    thirty_d = client.get("/prices/{}/30d".format(TOKEN)).get_json()["prices"]
    assert [p["price"] for p in thirty_d] == [0.05, 0.04, 0.03]


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
