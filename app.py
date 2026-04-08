import logging
import time

import coloredlogs
import flask

from cache import Cache
from config import Config
from network import NetworkReader
from price import PriceFetcher, PriceReader, init_db, is_db_initialised

log = logging.getLogger("app")


def json_response(body: dict):
    return flask.jsonify({**body, "t": int(time.time())})


PRICE_PERIODS = {
    "1h": 60 * 60,
    "1d": 60 * 60 * 24,
    "7d": 60 * 60 * 24 * 7,
    "30d": 60 * 60 * 24 * 30,
}


def create_app(config: Config) -> flask.Flask:
    coloredlogs.install(level=config.log_level, milliseconds=True, isatty=True)
    log.info("Creating session-network-api-v2 application")

    cache = Cache(default_ttl=config.live_data_ttl)

    if not is_db_initialised(config.prices_sqlite_db):
        log.info(
            f"Initialising prices DB at {config.prices_sqlite_db} "
            f"from {config.prices_sqlite_schema}"
        )
        init_db(config.prices_sqlite_db, config.prices_sqlite_schema)

    if config.enable_price_fetcher:
        fetcher = PriceFetcher(config)
        fetcher.start()
        fetcher._poll()  # immediate blocking poll so the DB has data at startup
    else:
        log.warning(
            "Price fetcher disabled — prices DB must be populated externally")

    price_reader = PriceReader(config, cache)
    network_reader = NetworkReader(config, cache)

    app = flask.Flask(__name__)

    @app.route("/info")
    def route_info():
        price_info = price_reader.get_latest()
        if price_info is None:
            flask.abort(500, "Price data unavailable")

        token_info = network_reader.get_token_info()
        network_info = network_reader.get_network_info()

        # Compute network_staked_usd at the call site so the cached network
        # info stays price-agnostic (avoids unbounded cache key variants).
        usd = price_info.get("usd") or 0.0
        network_info = {
            **network_info,
            "network_staked_usd": network_info["network_staked_tokens"] * usd,
        }

        if not price_info.get("usd_market_cap"):
            circ = token_info.get("circulating_supply") or 0
            calc = circ * usd
            if calc == 0:
                log.warning(
                    "usd_market_cap is zero and fallback calculation also returned 0"
                )
                price_info["usd_market_cap"] = None
            else:
                price_info["usd_market_cap"] = calc

        return json_response(
            {
                "token": token_info,
                "network": network_info,
                "price": price_info,
            }
        )

    @app.route("/prices/<token>/<period>")
    def route_prices(token: str, period: str):
        if period not in PRICE_PERIODS:
            flask.abort(400, f"Invalid period '{
                        period}'. Valid: {list(PRICE_PERIODS)}")

        if token not in config.coingecko_api_token_ids:
            flask.abort(404, f"Token '{token}' not found")

        prices = price_reader.get_range(token, PRICE_PERIODS[period])
        return json_response({"prices": prices})

    if config.enable_onion_requests:
        log.info("Enabling onion requests")
        try:
            from onion_req import handle_onion_requests
            handle_onion_requests(app, key_path=config.onion_req_key_path)
        except ModuleNotFoundError as exc:
            if exc.name == "session_util":
                log.error(
                    "session_util is not installed — install it to use onion requests, "
                    "or set enable_onion_requests = False"
                )
            raise
    else:
        log.warning("Onion requests are disabled")

    log.info("Application ready")

    return app
