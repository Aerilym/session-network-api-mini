# Session Network API

Minimal Python/Flask API serving Session network and token price data. This API is compatible with the "Network Page" in the Desktop, Android, and iOS Session Apps.

## Endpoints

### `GET /info`

Returns a combined snapshot of token info, network state, and current price.

```json
{
  "token": {
    "staking_requirement": 25000,
    "staking_reward_pool": 45000000,
    "contract_address": "0x10Ea9E5303670331Bdddfa66A4cEA47dae4fcF3b",
    "circulating_supply": 100000000.0
  },
  "network": {
    "network_size": 1000,
    "network_staked_tokens": 25000000.0,
    "network_staked_usd": 12345678.9289283
  },
  "price": {
    "usd": 0.0512310312,
    "usd_market_cap": 122402323.182871383,
    "t_price": 1737685901,
    "t_stale": 1737686501
  },
  "t": 1737686000
}
```

| Field | Description |
|---|---|
| `token.staking_requirement` | Minimum tokens required to run a node |
| `token.staking_reward_pool` | Current balance of the reward rate pool contract (whole number) |
| `token.contract_address` | Token ERC-20 contract address on Arbitrum |
| `token.circulating_supply` | Current circulating supply from session.observer |
| `network.network_size` | Total node count |
| `network.network_staked_tokens` | Total tokens staked across all nodes |
| `network.network_staked_usd` | `network_staked_tokens * usd` |
| `price.usd` | Current USD price |
| `price.usd_market_cap` | USD market cap (falls back to `circulating_supply * usd` if CoinGecko returns 0) |
| `price.t_price` | Unix timestamp when the price was last fetched from CoinGecko |
| `price.t_stale` | Unix timestamp when the cached price expires |
| `t` | Unix timestamp of when the response was served |

---

### `GET /prices/<token>/<period>`

Returns price history for a token over a rolling time window.

**Parameters**

| Parameter | Values |
|---|---|
| `token` | Any token ID configured in `coingecko_api_token_ids` (e.g. `session-token`) |
| `period` | `1h`, `1d`, `7d`, `30d` |

**Example:** `GET /prices/session-token/7d`

```json
{
  "prices": [
    {
      "token": "session-token",
      "price": 0.0512310312,
      "market_cap": 122402323.182871383,
      "updated_at": 1737685901
    }
  ],
  "t": 1737686000
}
```

Prices are ordered newest first. An empty array is returned if no data exists for the requested window.

---

### Onion request endpoints

When `enable_onion_requests = True` (the default), the following endpoints are registered to allow the API to be reached via the Session onion routing network:

| Endpoint | Version | Notes |
|---|---|---|
| `POST /oxen/v4/lsrpc` | v4 | Current protocol |
| `POST /oxen/v3/lsrpc` | v3 | Deprecated, retained for compatibility |
| `POST /loki/v3/lsrpc` | v3 | Deprecated alias |

Onion requests are decrypted using the server's X25519 keypair, then dispatched as internal subrequests against the same Flask app (identical to the original API). The response is re-encrypted before being returned.

The server's X25519 private key is stored in `key_x25519` (a 32-byte raw binary file). It is auto-generated on first startup if the file does not exist. The corresponding public key is logged at startup — this is the pubkey that Session clients need to reach the server.

Onion request support requires `PyNaCl` (in `requirements.txt`) and `session_util`. `session_util` is **not** a PyPI package — it comes from [`libsession-python`](https://github.com/session-foundation/libsession-python). See [Installing session_util](#installing-session_util) below.

---

## Data sources

| Data | Source | Cache TTL |
|---|---|---|
| Current price + history | Local SQLite (`prices.db`), written by background CoinGecko polling thread | Expires at `updated_at + price_poll_rate_seconds` |
| Node count, staked tokens, staking requirement | Local staking SQLite DB **or** remote SSB API (config toggles) | 60s |
| Staking reward pool balance | Arbitrum RPC `balanceOf()` call | 60s |
| Circulating supply | `session.observer` HTTP GET | 60s |
| `network_staked_usd` | Derived at response time from cached `network_staked_tokens * usd` | Not cached separately |

---

## Configuration

Edit `config.py` directly. The key options are:

```python
from config import Config

config = Config(
    # Staking data source — pick one:
    ssb_api_url="https://stake.getsession.org/api/ssb/",  # remote (default)
    # sqlite_db_ssb="/var/lib/ssb/staking.db",            # local SQLite

    # CoinGecko
    coingecko_api_key="",           # leave empty for free tier
    price_poll_rate_seconds=600,    # poll interval in seconds

    # Arbitrum RPC
    web3_provider_url="http://10.24.0.1/arb",
    disable_web3_client=False,      # set True to skip Web3 (no reward pool balance)

    # Onion requests
    enable_onion_requests=True,     # set False to disable
    onion_req_key_path="key_x25519",
)
```

See `config.py` for the full list of options with inline comments.

---

## Installing session_util

`session_util` is part of [`libsession-python`](https://github.com/session-foundation/libsession-python) and is **not** available on PyPI. Install it using one of the two methods below before running `pip install -r requirements.txt`.

### Option A: Pre-built Debian/Ubuntu package (recommended)

Available for supported Debian/Ubuntu releases via the Oxen apt repository:

```bash
sudo curl -so /etc/apt/trusted.gpg.d/oxen.gpg https://deb.oxen.io/pub.gpg
echo "deb https://deb.oxen.io $(lsb_release -sc) main" | sudo tee /etc/apt/sources.list.d/oxen.list
sudo apt-get update
sudo apt-get install python3-session-util
```

### Option B: Build from source

Required if your distro release is not supported by the Oxen apt repository.

The `local-dev-environment-setup.sh` script automates the full build: it clones and compiles oxen-encoding, oxen-pyoxenc, libsodium, libzmq, oxen-mq, spdlog, libsession-util, libsession-python, and oxen-pyoxenmq into an isolated virtual environment alongside `requirements.txt`.

Install build dependencies first:

```bash
sudo apt-get install build-essential cmake python3-dev python3-venv pybind11-dev
```

Then run the script from the directory where you want the build artefacts placed (`Builds/` and `Code/` subdirectories will be created there):

```bash
bash local-dev-environment-setup.sh
```

On success the script prints the path to activate the virtual environment. Use that environment when running the API.

---

## Running in development

**Requirements:** Python 3.11+

```bash
# 1. Install session_util (see above)

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Configure — edit config.py (at minimum set web3_provider_url or disable_web3_client=True)

# 5. Run the Flask dev server
flask --app wsgi:app run --debug --port 5000
```

The API will be available at `http://localhost:5000`.

To skip the Arbitrum RPC requirement (useful for local dev without an RPC endpoint), set `disable_web3_client=True` in `config.py`. `staking_reward_pool` and `contract_address` will be `null` in responses.

To disable onion requests in dev (avoids needing `session_util` installed), set `enable_onion_requests=False` in `config.py`.

---

## Running in production (uWSGI)

**Requirements:** Python 3.11+, uWSGI

```bash
# 1. Install session_util (see above)

# 2. Install Python dependencies
pip install -r requirements.txt
pip install uwsgi

    # 3. Configure — edit config.py
    $EDITOR config.py

    # 4. Run with uWSGI
uwsgi \
  --manage-script-name \
  --mount /=wsgi:app \
  --master \
  --processes 4 \
  --socket /home/network-api/api/network-api.wsgi \
  --chmod-socket=666 \
  --vacuum
```

See [`uswgi-template.ini`](uswgi-template.ini) for an example uWSGI vassal configuration.

### nginx reverse proxy

See [`nginx-template`](nginx-template) for a full nginx server block configuration supporting both HTTP (port 80) and HTTPS (port 443).

The essential location block for proxying to uWSGI is:

```nginx
location / {
    include    /etc/nginx/uwsgi_params;
    uwsgi_pass unix:///home/network-api/api/network-api.wsgi;
}
```

---

## File structure

```
session-network-api-v2/
├── app.py              Flask application factory and route handlers
├── cache.py            In-process TTL cache
├── config.py           Configuration — edit directly to configure the API
├── network.py          Staking data reader, Web3 balance calls, circulating supply
├── onion_req.py        Onion request handling (v3 + v4), crypto, subrequest dispatch
├── price.py            SQLite price reads/writes, CoinGecko client, background polling thread
├── schema_prices.sql   SQLite schema for the prices database
├── wsgi.py             WSGI entry point
├── uswgi-template.ini  Example uWSGI vassal configuration
├── nginx-template      Example nginx server block (HTTP + HTTPS)
└── requirements.txt    Python dependencies
```
