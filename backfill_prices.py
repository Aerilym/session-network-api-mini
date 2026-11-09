"""One-off backfill of historical daily prices from CoinGecko.

Fetches the maximum window the free/demo API allows (365 days, one daily
point at 00:00 UTC, with market caps) and inserts ONLY points strictly
older than the database's earliest existing record — periods already
covered by the live poller are never overridden. Re-running is a no-op
once the gap is filled.

Usage (from the repo root, with requirements installed):
    python3 backfill_prices.py            # insert
    python3 backfill_prices.py --dry-run  # report only, no writes
"""
import argparse
import sqlite3
import time
from datetime import datetime, timezone

import requests

from config import config
from price import PriceDB, write_prices


def oldest_price_ts(db_path: str, token: str) -> int | None:
    """Earliest recorded timestamp for THIS token (per-token coverage:
    tokens can start at different times)."""
    with sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True) as conn:
        row = conn.execute(
            "SELECT MIN(updated_at) FROM prices WHERE token = ?",
            (token,)).fetchone()
    return row[0] if row else None


def fetch_daily_history(token: str, api_key: str) -> dict:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/coins/{}/market_chart".format(token),
        params={"vs_currency": "usd", "days": "365"},
        headers={"accept": "application/json", "x-cg-demo-api-key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def to_price_rows(payload: dict, token: str,
                  oldest_ts: int | None) -> list[PriceDB]:
    """Daily points strictly older than oldest_ts (all of them if None),
    joined with market caps by timestamp, deduped, oldest-first."""
    caps = {int(ms // 1000): mc for ms, mc in payload.get("market_caps", [])}
    rows = []
    for ms, price in payload.get("prices", []):
        ts = int(ms // 1000)
        if oldest_ts is not None and ts >= oldest_ts:
            continue
        rows.append(PriceDB(
            token=token,
            price=float(price),
            market_cap=float(caps.get(ts) or 0.0),
            updated_at=ts,
        ))
    seen: set[int] = set()
    out = []
    for row in sorted(rows, key=lambda r: r.updated_at):
        if row.updated_at not in seen:
            seen.add(row.updated_at)
            out.append(row)
    return out


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be inserted; no writes")
    args = parser.parse_args()

    db = config.prices_sqlite_db
    for token in config.coingecko_api_token_ids:
        oldest = oldest_price_ts(db, token)
        payload = fetch_daily_history(token, config.coingecko_api_key)
        rows = to_price_rows(payload, token, oldest)

        if oldest is not None:
            print("{}: DB starts {} — fetched {} daily points, {} older than "
                  "that".format(token, _fmt(oldest), len(payload["prices"]),
                                len(rows)))
        else:
            print("{}: DB empty — fetched {} daily points, {} to insert"
                  .format(token, len(payload["prices"]), len(rows)))

        if not rows:
            print("  nothing to backfill (already covered)")
            continue

        span = "{} -> {}".format(_fmt(rows[0].updated_at),
                                 _fmt(rows[-1].updated_at))
        if args.dry_run:
            print("  [dry-run] would insert {} rows ({})".format(
                len(rows), span))
            continue

        write_prices(db, rows)
        print("  inserted {} rows ({})".format(len(rows), span))
        new_oldest = oldest_price_ts(db, token)
        print("  DB now starts {} for {}".format(_fmt(new_oldest), token))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
