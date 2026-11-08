"""Characterization-test fixtures.

Purpose: pin the current HTTP API contract exactly (response shapes, values,
nullability, status codes, error bodies) BEFORE any behaviour-changing fixes
are applied. Every assertion here is a deliberate snapshot of today's
behaviour; tests marked TRIPWIRE pin behaviour a planned fix is expected to
change and must be updated consciously, in the same commit as the fix.

Test boundaries:
- session_util (onion crypto) is stubbed: "decryption" is the identity
  function, so a request body IS the plaintext payload. The real crypto is
  session_util's contract, not ours; what we pin is our bencode/framing,
  subrequest dispatch, and error mapping on top of it.
- Upstreams (SSB API, session.observer, Arbitrum RPC via Web3BalanceReader)
  are stubbed with fixed fixture data at their call boundaries.
- Prices run against a real SQLite DB created by the project's own
  schema/init/write code.
"""
import json
import sys
import time
import types
from itertools import count
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# --- fixture data (mirrors README documented examples) ----------------------

TOKEN = "session-token"
PRICE = 0.0512310312
MARKET_CAP = 122402323.182871383
SSB_NETWORK = {
    "staking_requirement": 25000 * 10**9,        # atomic (9 decimals)
    "total_staked": 25_000_000 * 10**9,          # atomic
    "node_count": 1000,
}
CIRCULATING_SUPPLY = 100_000_000.0
WEB3_REWARD_POOL = 45_000_000
CONTRACT_ADDRESS = "0x10Ea9E5303670331Bdddfa66A4cEA47dae4fcF3b"  # config.py default
POLL_RATE = 600  # config default price_poll_rate_seconds
NOW = int(time.time())


# --- session_util stub (installed before app/onion_req is imported) ---------

class _StubOnionReqParser:
    def __init__(self, pubkey: bytes, privkey: bytes, data: bytes):
        if data.startswith(b"FORCE-DECRYPT-FAIL"):
            raise ValueError("stub: decrypt failure")
        self.payload = data

    def encrypt_reply(self, data: bytes) -> bytes:
        return data


def _install_session_util_stub() -> None:
    session_util = types.ModuleType("session_util")
    onionreq = types.ModuleType("session_util.onionreq")
    onionreq.OnionReqParser = _StubOnionReqParser
    session_util.onionreq = onionreq
    sys.modules["session_util"] = session_util
    sys.modules["session_util.onionreq"] = onionreq


_install_session_util_stub()


# --- upstream stubs -----------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if not self.ok:
            import requests
            raise requests.HTTPError("fake {}".format(self.status_code))

    def json(self):
        return self._payload


class Web3Stub:
    """Drop-in replacement for network.Web3BalanceReader."""

    fail = None          # exception instance to raise, or None
    balance = WEB3_REWARD_POOL
    calls = None         # shared counter dict injected per app

    def __init__(self, config):
        self.token_address = config.token_contract_address

    def get_reward_pool_balance(self):
        if self.calls is not None:
            self.calls["web3"] += 1
        if self.fail is not None:
            raise self.fail
        return self.balance


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    """Factory building fully-stubbed apps. Each call gets its own DB/key dir."""
    import network as network_mod

    counter = count()

    def _make(*, price_rows=None, ssb_ok=True, supply_ok=True,
              web3_fail=None, overrides=None):
        from config import Config
        from price import init_db, write_prices

        sub = tmp_path / "app{}".format(next(counter))
        sub.mkdir()
        config = Config(
            prices_sqlite_db=str(sub / "prices.db"),
            prices_sqlite_schema=str(REPO / "schema_prices.sql"),
            onion_req_key_path=str(sub / "key_x25519"),
            **(overrides or {}),
        )

        calls = {"ssb": 0, "supply": 0, "web3": 0}
        Web3Stub.fail = web3_fail
        Web3Stub.balance = WEB3_REWARD_POOL
        Web3Stub.calls = calls

        init_db(config.prices_sqlite_db, config.prices_sqlite_schema)
        if price_rows:
            write_prices(config.prices_sqlite_db, price_rows)

        monkeypatch.setattr(network_mod, "Web3BalanceReader", Web3Stub)

        def fake_get(url, timeout=None, **kwargs):
            if config.ssb_api_url.rstrip("/") in url:
                calls["ssb"] += 1
                if ssb_ok:
                    return FakeResponse({"network": SSB_NETWORK})
                return FakeResponse({}, status_code=503)
            if config.circulating_supply_api_url in url:
                calls["supply"] += 1
                if supply_ok:
                    return FakeResponse({"result": CIRCULATING_SUPPLY})
                return FakeResponse({}, status_code=503)
            raise AssertionError("unexpected url: {}".format(url))

        monkeypatch.setattr(network_mod.requests, "get", fake_get)

        from app import create_app
        return create_app(config), calls

    yield _make
    Web3Stub.fail = None
    Web3Stub.calls = None


@pytest.fixture
def info_app(make_app):
    """Canonical /info fixture: one fresh price row, all upstreams healthy."""
    from price import PriceDB

    row = PriceDB(token=TOKEN, price=PRICE, market_cap=MARKET_CAP,
                  updated_at=NOW - 60)
    return make_app(price_rows=[row])
