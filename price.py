import logging
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from math import trunc

import requests

from cache import Cache
from config import Config

log = logging.getLogger("price")


@dataclass
class PriceDB:
    token: str
    price: float
    market_cap: float
    updated_at: int


def _connect_ro(db_path: str):
    return sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)


def _connect_rw(db_path: str):
    return sqlite3.connect(db_path)


def init_db(db_path: str, schema_path: str) -> None:
    with _connect_rw(db_path) as conn:
        with open(schema_path) as fh:
            conn.executescript(fh.read())
    log.info("Prices DB initialised at %s", db_path)


def is_db_initialised(db_path: str) -> bool:
    try:
        with _connect_ro(db_path) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
            return len(cur.fetchall()) > 0
    except Exception:
        return False


def get_latest_price(db_path: str, token: str) -> PriceDB | None:
    with closing(_connect_ro(db_path)) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute(
                "SELECT token, price, market_cap, updated_at "
                "FROM prices WHERE token = ? ORDER BY updated_at DESC LIMIT 1",
                (token,),
            )
            row = cur.fetchone()
            return PriceDB(*row) if row else None


def get_prices_since(db_path: str, token: str, since: int) -> list[PriceDB]:
    with closing(_connect_ro(db_path)) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute(
                "SELECT token, price, market_cap, updated_at "
                "FROM prices WHERE token = ? AND updated_at >= ? "
                "ORDER BY updated_at DESC",
                (token, since),
            )
            return [PriceDB(*row) for row in cur.fetchall()]


def write_prices(db_path: str, prices: list[PriceDB]) -> None:
    with closing(_connect_rw(db_path)) as conn:
        conn.execute("BEGIN")
        with closing(conn.cursor()) as cur:
            cur.executemany(
                "INSERT INTO prices (token, price, market_cap, updated_at) "
                "VALUES (?, ?, ?, ?)",
                [(p.token, p.price, p.market_cap, p.updated_at)
                 for p in prices],
            )
        conn.commit()


class CoinGeckoClient:
    def __init__(self, config: Config):
        self._token_ids = config.coingecko_api_token_ids
        ids_param = "%2C".join(config.coingecko_api_token_ids)
        self._url = (
            "{}/v3/simple/price"
            "?ids={}"
            "&vs_currencies=usd"
            "&include_market_cap=true"
            "&include_last_updated_at=true"
            "&precision={}"
        ).format(config.coingecko_api_url, ids_param, config.coingecko_precision)
        self._headers = {
            "accept": "application/json",
            "x-cg-demo-api-key": config.coingecko_api_key,
        }

    def fetch(self) -> list[PriceDB] | None:
        try:
            resp = requests.get(self._url, headers=self._headers, timeout=15)
        except Exception as exc:
            log.warning("CoinGecko request failed: %s", exc)
            return None

        if not resp.ok:
            log.warning("CoinGecko HTTP %s: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        result: list[PriceDB] = []
        for token in self._token_ids:
            entry = data.get(token)
            if entry is None:
                log.warning("CoinGecko: token '%s' missing from response", token)
                continue
            result.append(
                PriceDB(
                    token=token,
                    price=entry.get("usd", 0.0),
                    market_cap=entry.get("usd_market_cap", 0.0),
                    updated_at=entry.get("last_updated_at", int(time.time())),
                )
            )
        return result


class PriceFetcher(threading.Thread):
    def __init__(self, config: Config):
        super().__init__(daemon=True, name="price-fetcher")
        self._db_path = config.prices_sqlite_db
        self._interval = config.price_poll_rate_seconds
        self._client = CoinGeckoClient(config)

    def run(self) -> None:
        log.info("Price fetcher started (interval=%ss, db=%s)", self._interval, self._db_path)
        while True:
            self._poll()
            time.sleep(self._interval)

    def _poll(self) -> None:
        log.debug("Polling CoinGecko...")
        prices = self._client.fetch()
        if prices:
            write_prices(self._db_path, prices)
            log.info("Wrote %d price record(s) to DB", len(prices))
        else:
            log.warning("No price data received from CoinGecko")


class PriceReader:
    def __init__(self, config: Config, cache: Cache):
        self._db_path = config.prices_sqlite_db
        self._token_ids = config.coingecko_api_token_ids
        self._default_token = config.prices_api_default_token
        self._poll_rate = config.price_poll_rate_seconds
        self._cache = cache

    def _fetch_latest(self, token: str) -> PriceDB | None:
        return get_latest_price(self._db_path, token)

    def get_latest(self, token: str | None = None) -> dict | None:
        if token is None:
            token = self._default_token

        if token not in self._token_ids:
            return None

        key = "price-{}-latest".format(token)
        value: PriceDB | None = self._cache.get(
            key,
            getter=self._fetch_latest,
            getter_args=token,
            ttl=self._poll_rate,
            stale_ok=True,
        )

        if value is None:
            return None

        # Align expiry to the price record's own timestamp so the cache
        # invalidates when a new poll cycle is due, not on a fixed TTL.
        self._cache.set(
            key, value, invalidate_timestamp=value.updated_at + self._poll_rate)

        stale_at = trunc(self._cache.get_stale_timestamp(key))
        return {
            "usd": value.price,
            "usd_market_cap": value.market_cap,
            "t_price": value.updated_at,
            "t_stale": stale_at,
        }

    def get_range(self, token: str, range_seconds: int) -> list[dict]:
        key = "price-range-{}-{}".format(token, range_seconds)

        # The getter recomputes `since` at refresh time so background refreshes
        # always query the correct window rather than the window from cold-start.
        def fetch() -> list[PriceDB]:
            since = trunc(time.time()) - range_seconds
            return get_prices_since(self._db_path, token, since)

        cached: list[PriceDB] = self._cache.get(
            key,
            getter=fetch,
            ttl=self._poll_rate,
            stale_ok=True,
        ) or []

        if cached:
            # Align expiry to the newest record's timestamp.
            self._cache.set(
                key, cached,
                invalidate_timestamp=cached[0].updated_at + self._poll_rate,
            )

        return [
            {
                "token": p.token,
                "price": p.price,
                "market_cap": p.market_cap,
                "updated_at": p.updated_at,
            }
            for p in cached
        ]
