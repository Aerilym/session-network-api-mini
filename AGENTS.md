# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

Minimal Python/Flask API serving Session network + token price data to the
"Network Page" in the Session apps (Desktop, Android, iOS), reachable
directly and via the Session onion network (`/oxen/v4/lsrpc` and deprecated
v3 endpoints). Deployed under uWSGI + nginx with a mule process
(`mule_price_fetcher.py`) polling CoinGecko.

## The critical rule: the API surface is frozen

Session clients in the field parse these responses and cannot be updated.
Any change to keys, values, nesting, types, or nullability of an endpoint
response is a breaking change.

- Do not add, remove, rename, or reorder meaning of response fields.
- Do not introduce new nullable values. The only permitted nulls are pinned
  by tests:
  - `price.usd_market_cap` — CoinGecko returns 0 and the
    `circulating_supply * usd` fallback is also 0
  - `token.circulating_supply` — the supply fetch fails
  - `token.staking_reward_pool` / `token.contract_address` —
    `disable_web3_client` mode
- Status codes and error bodies are contract too: `/info` aborts 500 when
  no price data exists; `/prices` returns 400 (invalid period) / 404
  (unknown token); onion v3 errors return `{"status_code": N}`; onion v4
  errors return bencoded meta `code` with body `Invalid v4 onion request`.
- Onion protocol quirks are contract, not cleanup targets: the Android
  `body: "null"` GET quirk, the `/loki/v3/lsrpc` alias, v3 base64-encoded
  replies, v4 raw bencode replies.
- `/prices` granularity is contract: `1h`/`1d` raw; `7d`/`30d`/`3mo`/`6mo`/`1y`
  downsampled to the last observation per hour/4h/12h/24h/48h bucket
  respectively (`PRICE_RANGE_BUCKETS` in price.py). Never average or
  fabricate prices when downsampling — every returned row must be a real
  recorded observation.

`tests/` is a characterization suite enforcing this with exact
dict-equality assertions — a red test after a change means you changed the
contract, not that the test is wrong. **TRIPWIRE tests**
(`test_info_500_when_web3_rpc_down`, `test_info_500_when_ssb_api_down`) pin
the cold-path failure contract: upstream failure with **no cached value
ever stored** → `/info` returns 500. The warm path (failure after a value
is cached) serves the last known value — pinned by
`test_info_serves_last_known_values_when_web3_fails_after_warmup`. Change
either behaviour only deliberately, updating the tests in the same commit
with the new contract spelled out. Never weaken assertions to make code
pass.

## Commands

```bash
# Setup (Python 3.11+)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Test suite — run from repo root, before and after every change.
# Fully offline: upstreams (SSB API, session.observer, Arbitrum RPC) and
# session_util are stubbed; prices use a real SQLite DB.
.venv/bin/python -m pytest tests/ -q

# Dev server (requires session_util, or set enable_onion_requests=False
# in config.py)
flask --app wsgi:app run --debug --port 5000
```

No lint/typecheck configuration; the test suite is the gate. All tests must
pass; never commit with skipped or flaky tests.

## Gotchas

- **`session_util` is not on PyPI** (Oxen apt repo or source build — see
  README). Tests never need it: `tests/conftest.py` stubs
  `session_util.onionreq.OnionReqParser` via `sys.modules` before the app
  imports. Do not remove the stub or make tests require the real library.
- **web3 imports are lazy** (inside `Web3BalanceReader.__init__`); tests
  monkeypatch `network.Web3BalanceReader`. Keep imports lazy.
- **Float precision matters.** `simplejson` preserves float precision in
  price values. Response-contract tests assert exact float equality on
  purpose — do not switch to `pytest.approx`.
- **Configure by editing `config.py` directly** — that is the designed
  mechanism, not tech debt to refactor away.
- **Cache semantics are load-bearing** (`cache.py`: stale-while-revalidate,
  single-flight, bounded stale-serving, negative caching; invariants pinned
  by `tests/test_cache.py` and `tests/test_info.py`). `price.py` aligns
  price expiry to the record's own timestamp — `t_stale` is asserted
  exactly as `updated_at + price_poll_rate_seconds`.
- **Warmup threads outlive per-test monkeypatches** — tests disable warmup
  via conftest; keep that default and opt in explicitly
  (tests/test_warmup.py).
- **The mule is the sole SQLite writer**; web workers open the prices DB
  read-only. Do not add writes to request paths.
- Never commit `prices.db*` or `key_x25519` (gitignored). A real
  `key_x25519` is the server's onion identity.
