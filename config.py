import logging
from dataclasses import dataclass, field


@dataclass
class Config:
    log_level: int = logging.INFO

    # Staking data source — exactly one of sqlite_db_ssb or ssb_api_url must be set.
    # Uncomment sqlite_db_ssb to read from a local SQLite DB instead of the SSB API.
    sqlite_db_ssb: str | None = None
    # sqlite_db_ssb: str | None = "/var/lib/ssb/staking.db"
    ssb_api_url: str = "https://stake.getsession.org/api/ssb/"

    prices_sqlite_db: str = "prices.db"
    prices_sqlite_schema: str = "schema_prices.sql"

    enable_price_fetcher: bool = True
    coingecko_api_key: str = ""  # leave empty for free tier
    coingecko_api_url: str = "https://api.coingecko.com/api"
    coingecko_api_token_ids: list[str] = field(
        default_factory=lambda: ["session-token"]
    )
    coingecko_precision: int = 9
    price_poll_rate_seconds: int = 600
    prices_api_default_token: str = "session-token"

    token_contract_address: str = "0x10Ea9E5303670331Bdddfa66A4cEA47dae4fcF3b"
    reward_rate_pool_contract_address: str = "0x11f040E89dFAbBA9070FFE6145E914AC68DbFea0"

    # Arbitrum RPC endpoint. Set disable_web3_client=True to skip Web3
    # (staking_reward_pool and contract_address will be null in responses).
    web3_provider_url: str = "http://10.24.0.1/arb"
    disable_web3_client: bool = False

    circulating_supply_api_url: str = (
        "https://session.observer/api/sesh_circulating_supply"
    )

    # Requires session_util and PyNaCl to be installed.
    # Set enable_onion_requests=False to disable (avoids needing session_util).
    enable_onion_requests: bool = True
    # Auto-generated on first startup if the file does not exist.
    onion_req_key_path: str = "key_x25519"

    live_data_ttl: int = 60


config = Config()
