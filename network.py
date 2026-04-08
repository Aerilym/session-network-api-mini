import logging
import math
import sqlite3
from contextlib import closing

import requests

from cache import Cache
from config import Config

log = logging.getLogger("network")

TOKEN_DECIMALS = 9


def token_from_atomic(amount: int) -> float:
    return amount / (10 ** TOKEN_DECIMALS)


def sqlite_connect_ro(db_path: str):
    return sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)


def read_network_info_sqlite(db_path: str) -> dict:
    with closing(sqlite_connect_ro(db_path)) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute("SELECT * FROM network_info LIMIT 1")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("network_info table is empty")
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))


def read_network_info_api(api_endpoint: str) -> dict:
    resp = requests.get(api_endpoint, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("network")
    if data is None:
        raise RuntimeError("SSB API /info response missing 'network' key")
    return data



ERC20_BALANCEOF_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
    }
]


class Web3BalanceReader:
    def __init__(self, config: Config):
        from web3 import Web3, HTTPProvider
        import eth_utils

        self.reward_pool_address = eth_utils.to_checksum_address(
            config.reward_rate_pool_contract_address
        )
        self.token_address = eth_utils.to_checksum_address(
            config.token_contract_address
        )
        self.w3 = Web3(HTTPProvider(config.web3_provider_url))
        self.contract = self.w3.eth.contract(
            address=self.token_address, abi=ERC20_BALANCEOF_ABI)
        log.info(
            "Web3 client connected to %s (token=%s)",
            config.web3_provider_url,
            self.token_address,
        )

    def get_reward_pool_balance(self) -> int:
        raw = self.contract.functions.balanceOf(
            self.reward_pool_address).call()
        return math.trunc(token_from_atomic(raw))


class NetworkReader:
    def __init__(self, config: Config, cache: Cache):
        self.config = config
        self.cache = cache
        self.ttl = config.live_data_ttl
        self._db_path = config.sqlite_db_ssb
        self._api_endpoint = "{}/info".format(config.ssb_api_url.rstrip('/'))
        self.circulating_supply_api_url = config.circulating_supply_api_url

        if self._db_path:
            log.info("Staking reader: using local SQLite DB at %s", self._db_path)
        else:
            log.info("Staking reader: using remote SSB API at %s", self._api_endpoint)

        if not config.disable_web3_client:
            self.web3 = Web3BalanceReader(config)
        else:
            self.web3 = None
            log.warning(
                "Web3 client disabled; staking_reward_pool will be None")

    def _get_network_info_raw(self) -> dict:
        if self._db_path:
            return read_network_info_sqlite(self._db_path)
        return read_network_info_api(self._api_endpoint)

    def get_raw_network_info(self) -> dict:
        return self.cache.get(
            "network_info_raw",
            getter=self._get_network_info_raw,
            ttl=self.ttl,
            stale_ok=True,
        )

    def get_circulating_supply(self) -> float | None:
        return self.cache.get(
            "circulating_supply",
            getter=self.fetch_circulating_supply,
            ttl=self.ttl,
            stale_ok=True,
        )

    def fetch_circulating_supply(self) -> float | None:
        try:
            resp = requests.get(
                self.circulating_supply_api_url, timeout=10)
            if not resp.ok:
                log.warning(
                    "circulating_supply API returned %s: %s",
                    resp.status_code, resp.text[:200],
                )
                return None
            return float(resp.json()["result"])
        except Exception as exc:
            log.warning("Failed to fetch circulating supply: %s", exc)
            return None

    def get_reward_pool_balance(self) -> int | None:
        if self.web3 is None:
            return None
        return self.cache.get(
            "reward_pool_balance",
            getter=self.web3.get_reward_pool_balance,
            ttl=self.ttl,
            stale_ok=True,
        )

    def get_token_info(self) -> dict:
        return self.cache.get(
            "token_info",
            getter=self.build_token_info,
            ttl=self.ttl,
            stale_ok=True,
        )

    def build_token_info(self) -> dict:
        net = self.get_raw_network_info()
        return {
            "staking_requirement": int(token_from_atomic(net["staking_requirement"])),
            "staking_reward_pool": self.get_reward_pool_balance(),
            "contract_address": self.web3.token_address if self.web3 is not None else None,
            "circulating_supply": self.get_circulating_supply(),
        }

    def get_network_info(self) -> dict:
        return self.cache.get(
            "network_info_basic",
            getter=self.build_network_info,
            ttl=self.ttl,
        )

    def build_network_info(self) -> dict:
        net = self.get_raw_network_info()
        staked_tokens = token_from_atomic(net["total_staked"])
        return {
            "network_size": net["node_count"],
            "network_staked_tokens": staked_tokens,
        }
