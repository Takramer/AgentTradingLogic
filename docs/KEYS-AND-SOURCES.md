# Credentials and data sources — status

**Updated:** 2026-08-22. Keys live in `secrets.json`, which is **gitignored and
has never been committed** (verified with `git log --all -- secrets.json`).
File mode is 0600. `.gitignore` was widened the same day to catch
`secrets*.json`, `*secret*.json`, `*.pem`, `*.key`, `*_rsa`, `credentials.json`,
`.envrc`, `*.token`, `api_keys*`.

**Rotate these.** Every credential here was pasted into a chat transcript, which
is a place secrets should not live regardless of how the repo is configured.
The Kalshi RSA private key is the urgent one: it authenticates order placement,
not just reads. Kalshi lets you create a new key pair and delete the old one in
Settings → API Keys; nothing in this repo breaks when you do, only the two
values in `secrets.json` change.

## Working

| source | credential | verified | use |
|---|---|---|---|
| Xweather (Aeris) | `XWEATHER_CLIENT_ID` + `_SECRET` | 200 | **unused** — weather book cut 2026-08-22 |
| Visual Crossing | `VISUALCROSSING_API_KEY` | 200 | **unused** — weather book cut 2026-08-22 |
| Finnhub | `FINNHUB_API_KEY` | 200, 100 news items | general + company news |
| Alpaca | `ALPACA_API_KEY`/`_SECRET` | 200 | real-time Benzinga news |
| Open-Meteo | none | 200 | forecast / ensemble / archive / hindcast |
| Kalshi public REST | none | 200 | markets, settled `expiration_value` |
| Coinbase | none | 200 | BTC spot + history, **2.0 bps from BRTI** — the settlement-grade source |
| Bitfinex `api-pub.bitfinex.com/v2` | none | 200 | **10,000 bars/request back to 2013** — the deep-history source. +8.4 bps from Coinbase (sd 11.2), so history only, never settlement |
| binance.us / Bitstamp | none | 200 | 1,000 bars/request, back to 2019 / 2017 — backups |

The Xweather value supplied as one string is actually two credentials joined by
`_`: everything before the underscore is the client id, everything after is the
secret. Stored split.

Finnhub's `X-Finnhub-Secret` (`FINNHUB_WEBHOOK_SECRET`) is for verifying inbound
**webhooks**, not for outbound calls. It only matters if a public HTTPS endpoint
is stood up for Finnhub to push to; nothing here does that yet.

## Kalshi — live, read-only

**Configured 2026-08-22 and verified.** 2048-bit RSA key stored at
`~/.kalshi/private_key.pem`, mode 0600, directory 0700 — **outside the repo**.
`secrets.json` holds only `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`.

Auth is `RSA-PSS-SHA256` over `timestamp + METHOD + path`, MGF1-SHA256, salt
length = digest length. No bearer token exists.
`market_data_kalshi.kalshi_signed_headers()` implements it;
`kalshi_auth_get()` is GET-only on purpose and there is no signed POST helper
anywhere in the engine.

    GET /portfolio/balance   ->  200. Account balance $0.00 (unfunded).
    WebSocket                ->  connected, subscribed, live orderbook deltas.

**Account is unfunded, so nothing can trade even by accident today.**

### Three things that were wrong before it worked

1. **The stdlib `ssl` module has no CA bundle on this Python.** `requests`
   bundles certifi so REST worked immediately; `websockets` uses the stdlib
   context and failed with `CERTIFICATE_VERIFY_FAILED`, which looks exactly like
   a network outage. `kalshi_ws._ssl_context()` passes certifi explicitly.
2. **`websockets` 12.0 wants `extra_headers`**, renamed to `additional_headers`
   in v13. Detected at runtime.
3. **The live payload field names are not the documented ones.** Snapshots carry
   `yes_dollars_fp` / `no_dollars_fp`; deltas carry `price_dollars`, `delta_fp`,
   `side`, `ts_ms`. Reading `price` / `delta` returns None on every message and
   leaves a book that never updates and never errors.

Also: `ticker_v2` and `trade` channels were rejected with "Unknown channel name"
(code 8). Only `orderbook_delta` is confirmed, so only that is offered.

### Sequence numbers

Every message carries `seq` per subscription, and deltas are increments against
a snapshot. One dropped message leaves a book that is wrong for as long as the
connection lives, with no error. `Book.apply()` returns False on a gap and
`stream()` re-subscribes instead of serving a corrupted book.

Both sides quote in their own terms — a NO level at 0.92 is a YES bid at 0.08.
`Book.best()` converts; comparing raw sides reads the spread backwards.

## Blocked

**Finnhub crypto** — `/crypto/candle` returns 403 on the free tier. Irrelevant:
Coinbase is free, unlimited, and 2 bps from the index Kalshi actually settles on.

## Robinhood MCP — no crypto market data

Checked directly. Crypto appears only as:

- `search(asset_type="currency_pair")` → resolves `BTC-USD` to a UUID
- watchlist add/remove by `currency_pair_ids`
- `get_pnl_trade_history` includes crypto trades
- `get_portfolio` breaks value down by asset type

The tool guide states "the symbol routes to crypto quote/order tools" — **those
tools do not exist in this build.** There is no crypto quote, historical, or
order tool. Nothing here is useful for BTC market data, and Coinbase already
beats what it would give.

Where the MCP IS useful: account state (`get_accounts`, `get_portfolio`,
`get_equity_positions`), realized PnL including crypto and prediction markets,
equity/option quotes and history, and the Level-2 equity book
(`get_equity_price_book`).

## Commands


