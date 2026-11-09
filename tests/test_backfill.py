"""Backfill script logic pins (pure functions + real SQLite; no network)."""
import time

from backfill_prices import oldest_price_ts, to_price_rows
from price import PriceDB, get_prices_since, init_db, write_prices

from conftest import REPO, TOKEN


def _db(tmp_path):
    path = str(tmp_path / "prices.db")
    init_db(path, str(REPO / "schema_prices.sql"))
    return path


def _payload(rows):
    """market_chart-style payload: [[ms, value], ...] with 0:00 UTC days."""
    return {
        "prices": [[ts * 1000, price] for ts, price, _mc in rows],
        "market_caps": [[ts * 1000, mc] for ts, _p, mc in rows],
    }


def test_to_price_rows_keeps_only_points_older_than_db():
    oldest = int(time.time()) - 10
    payload = _payload([
        (oldest - 86400, 0.10, 1.0),      # older than DB start -> kept
        (oldest - 86400 * 2, 0.09, 0.9),  # older -> kept
        (oldest, 0.11, 1.1),              # equal to DB start -> dropped
        (oldest + 600, 0.12, 1.2),        # newer (live-poller territory) -> dropped
    ])
    rows = to_price_rows(payload, TOKEN, oldest)
    assert [r.price for r in rows] == [0.09, 0.10]  # oldest-first
    assert all(isinstance(r.market_cap, float) for r in rows)
    assert rows[0].market_cap == 0.9


def test_to_price_rows_empty_db_keeps_everything():
    payload = _payload([
        (1750000000, 0.05, 5.0),
        (1750086400, 0.06, 6.0),
    ])
    rows = to_price_rows(payload, TOKEN, None)
    assert len(rows) == 2


def test_to_price_rows_missing_market_cap_becomes_zero():
    payload = {"prices": [[1750000000000, 0.05]], "market_caps": []}
    rows = to_price_rows(payload, TOKEN, None)
    assert rows[0].market_cap == 0.0


def test_backfill_end_to_end_no_overlap_with_existing(tmp_path):
    db = _db(tmp_path)
    now = int(time.time())
    existing = PriceDB(TOKEN, 0.50, 50.0, now - 10)
    write_prices(db, [existing])

    payload = _payload([
        (now - 86400, 0.40, 40.0),
        (now - 10, 0.50, 50.0),   # same ts as existing -> filtered, not duped
        (now - 5, 0.51, 51.0),    # newer than existing -> filtered
    ])
    rows = to_price_rows(payload, TOKEN, oldest_price_ts(db))
    write_prices(db, rows)

    stored = sorted(get_prices_since(db, TOKEN, 0), key=lambda r: r.updated_at)
    assert [(r.price, r.updated_at) for r in stored] == [(0.40, now - 86400),
                                                         (0.50, now - 10)]
    assert oldest_price_ts(db) == now - 86400
