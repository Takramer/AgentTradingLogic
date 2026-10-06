"""Load and check the paper-trading knobs.

The checks follow the published video script: a bad TimeFrame, an inverted
entry range, or an order below 5 contracts stops the process before any
poll starts. Poll intervals are not part of the strategy fingerprint, so
changing them keeps the saved paper account.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "BNB")

TIMEFRAMES = {
    "5M": {"seconds": 300, "variant": "fiveminute", "max_delay": 5, "label": "5-minute"},
    "15M": {"seconds": 900, "variant": "fifteen", "max_delay": 15, "label": "15-minute"},
    "1H": {"seconds": 3600, "variant": "hourly", "max_delay": 60, "label": "1-hour"},
}

# Slug prefixes from the published script. 5m/15m append the window start
# (unix seconds). 1H uses an America/New_York clock label instead.
SLUG_PREFIX = {
    "5M": {
        "BTC": "btc-updown-5m",
        "ETH": "eth-updown-5m",
        "SOL": "sol-updown-5m",
        "XRP": "xrp-updown-5m",
        "DOGE": "doge-updown-5m",
        "HYPE": "hype-updown-5m",
        "BNB": "bnb-updown-5m",
    },
    "15M": {
        "BTC": "btc-updown-15m",
        "ETH": "eth-updown-15m",
        "SOL": "sol-updown-15m",
        "XRP": "xrp-updown-15m",
        "DOGE": "doge-updown-15m",
        "HYPE": "hype-updown-15m",
        "BNB": "bnb-updown-15m",
    },
    "1H": {
        "BTC": "bitcoin-up-or-down",
        "ETH": "ethereum-up-or-down",
        "SOL": "solana-up-or-down",
        "XRP": "xrp-up-or-down",
        "DOGE": "dogecoin-up-or-down",
        "HYPE": "hype-up-or-down",
        "BNB": "bnb-up-or-down",
    },
}

CHAINLINK_SYMBOL = {
    "BTC": "btc/usd",
    "ETH": "eth/usd",
    "SOL": "sol/usd",
    "XRP": "xrp/usd",
    "DOGE": "doge/usd",
    "HYPE": "hype/usd",
    "BNB": "bnb/usd",
}

BINANCE_SYMBOL = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
    "DOGE": "dogeusdt",
    "HYPE": "hypeusdt",
    "BNB": "bnbusdt",
}

COINBASE_PAIR = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "DOGE": "DOGE-USD",
    "HYPE": "HYPE-USD",
    "BNB": "BNB-USD",
}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _order_size_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return str(value).strip()


def _order_size_ok(text: str) -> bool:
    if not text or text[0] == "-":
        return False
    if "." not in text:
        return text.isdigit() and int(text) > 0
    whole, frac = text.split(".", 1)
    return (
        whole.isdigit()
        and frac.isdigit()
        and 1 <= len(frac) <= 2
        and float(text) > 0
    )


@dataclass(frozen=True)
class Settings:
    initial_balance: float
    assets: tuple[str, ...]
    timeframe: str
    time_delay: float
    entry_min: float
    entry_max: float
    exit: float
    take_profit_pts: float
    delta: float
    order_size: float
    poll_interval_ms: int
    poll_jitter_max_ms: int
    market_refresh_ms: int
    http_timeout_ms: int
    feed_max_age_ms: int

    @property
    def timeframe_seconds(self) -> int:
        return TIMEFRAMES[self.timeframe]["seconds"]

    @property
    def price_variant(self) -> str:
        return TIMEFRAMES[self.timeframe]["variant"]

    def fingerprint(self) -> str:
        payload = {
            "initialBalance": self.initial_balance,
            "assets": list(self.assets),
            "TimeFrame": self.timeframe,
            "timeDelay": self.time_delay,
            "EntryRange": [self.entry_min, self.entry_max],
            "Exit": self.exit,
            "TakeProfitPts": self.take_profit_pts,
            "Delta": self.delta,
            "orderSize": self.order_size,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def banner(self) -> str:
        hold = ""
        if self.take_profit_pts == 1:
            hold = " | TakeProfitPts = 1, winner will be held to resolution"
        return (
            "Polymarket PAPER TRADING Started"
            f" | TimeFrame: {self.timeframe}"
            f" | EntryRange: [{self.entry_min:.2f}, {self.entry_max:.2f}]"
            f" | Contracts: {_order_size_text(self.order_size)}"
            f" | Exit: {self.exit:.2f}"
            f" | TakeProfitPts: {self.take_profit_pts:.2f}"
            f" | Delta: {self.delta:g}"
            f" | Window: last {self.time_delay:g}m"
            f" | InitialBalance: {self.initial_balance:.2f}"
            f"{hold}"
        )


def validate(raw: dict) -> list[str]:
    """Return human-readable errors. An empty list means the dict can run."""
    if not isinstance(raw, dict):
        return ["ERROR: config must be a JSON object."]
    errors: list[str] = []

    tf = str(raw.get("TimeFrame", "")).strip().upper()
    if tf not in TIMEFRAMES:
        errors.append(
            f"ERROR: TimeFrame is '{raw.get('TimeFrame')}'. Set it to 5M, 15M, or 1H."
        )

    balance = raw.get("initialBalance")
    if not _is_number(balance) or float(balance) <= 0:
        errors.append(
            f"ERROR: initialBalance is '{balance}'. "
            "It needs to be a number greater than zero, for example 1000."
        )

    entry = raw.get("EntryRange")
    entry_min = entry_max = None
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        errors.append(
            "ERROR: EntryRange must be [Min, Max], for example [0.60, 0.80]. "
            "Both values need to be decimals between 0 and 1."
        )
    else:
        entry_min, entry_max = entry[0], entry[1]
        min_ok = _is_number(entry_min) and 0 < float(entry_min) < 1
        max_ok = _is_number(entry_max) and 0 < float(entry_max) < 1
        if not min_ok:
            errors.append(
                f"ERROR: EntryRange Min is '{entry_min}'. "
                "It needs to be a decimal between 0 and 1, for example 0.60."
            )
        if not max_ok:
            errors.append(
                f"ERROR: EntryRange Max is '{entry_max}'. "
                "It needs to be a decimal between 0 and 1, for example 0.80."
            )
        if min_ok and max_ok and float(entry_min) > float(entry_max):
            errors.append(
                f"ERROR: EntryRange Min {float(entry_min)} is greater than "
                f"Max {float(entry_max)}. Put the smaller decimal first, "
                "for example [0.60, 0.80]."
            )

    exit_px = raw.get("Exit")
    if not _is_number(exit_px) or not 0 < float(exit_px) < 1:
        errors.append(
            f"ERROR: Exit is '{exit_px}'. "
            "It needs to be a decimal between 0 and 1, for example 0.40."
        )

    tp = raw.get("TakeProfitPts")
    if not _is_number(tp) or not 0 < float(tp) <= 1:
        errors.append(
            f"ERROR: TakeProfitPts is '{tp}'. "
            "It needs to be greater than 0 and no more than 1, for example 0.10 or 1."
        )

    delay = raw.get("timeDelay")
    if not _is_number(delay) or float(delay) <= 0:
        errors.append(
            f"ERROR: timeDelay is '{delay}'. "
            "It needs to be a number greater than zero, measured in minutes."
        )
    elif tf in TIMEFRAMES and float(delay) > TIMEFRAMES[tf]["max_delay"]:
        spec = TIMEFRAMES[tf]
        errors.append(
            f"ERROR: timeDelay is {delay}, but a {spec['label']} window needs "
            f"timeDelay less than or equal to {spec['max_delay']}."
        )

    size_text = _order_size_text(raw.get("orderSize", ""))
    if not _order_size_ok(size_text):
        errors.append(
            f"ERROR: orderSize is '{raw.get('orderSize')}'. "
            "Set it to a positive contract count with no more than two decimal "
            "places, for example 5 or 5.50."
        )
    elif float(size_text) < 5:
        errors.append(
            "ERROR: Paper trading requires orderSize of at least 5 to match "
            "Polymarket's live-trading minimum."
        )

    delta = raw.get("Delta", 0)
    if not _is_number(delta) or float(delta) < 0:
        errors.append(
            f"ERROR: Delta is '{delta}'. It needs to be zero or a positive number. "
            "It is the minimum |open - live| price gap, in the underlying's own units."
        )

    assets = raw.get("assets")
    if not isinstance(assets, (list, tuple)) or not assets:
        errors.append('ERROR: assets is empty. Set it to a list such as ["BTC"].')
    else:
        for asset in assets:
            name = str(asset).strip().upper()
            if name not in ASSETS:
                errors.append(
                    f"ERROR: assets includes '{asset}'. "
                    f"Options: {', '.join(ASSETS)}."
                )

    for key, label in (
        ("pollIntervalMs", "pollIntervalMs"),
        ("pollJitterMaxMs", "pollJitterMaxMs"),
        ("marketRefreshMs", "marketRefreshMs"),
        ("polymarketHttpTimeoutMs", "polymarketHttpTimeoutMs"),
        ("feedMaxAgeMs", "feedMaxAgeMs"),
    ):
        if key not in raw:
            continue
        value = raw[key]
        if not _is_number(value) or float(value) < 0 or float(value) != int(value):
            errors.append(f"ERROR: {label} is '{value}'. It needs to be a whole number of milliseconds, zero or more.")
    return errors


def settings_from(raw: dict) -> Settings:
    errors = validate(raw)
    if errors:
        raise ValueError("\n".join(errors))
    assets = tuple(dict.fromkeys(str(a).strip().upper() for a in raw["assets"]))
    timeout = int(raw.get("polymarketHttpTimeoutMs", 2000))
    return Settings(
        initial_balance=float(raw["initialBalance"]),
        assets=assets,
        timeframe=str(raw["TimeFrame"]).strip().upper(),
        time_delay=float(raw["timeDelay"]),
        entry_min=float(raw["EntryRange"][0]),
        entry_max=float(raw["EntryRange"][1]),
        exit=float(raw["Exit"]),
        take_profit_pts=float(raw["TakeProfitPts"]),
        delta=float(raw.get("Delta", 0)),
        order_size=float(_order_size_text(raw["orderSize"])),
        poll_interval_ms=int(raw.get("pollIntervalMs", 600)),
        poll_jitter_max_ms=int(raw.get("pollJitterMaxMs", 600)),
        market_refresh_ms=int(raw.get("marketRefreshMs", 3000)),
        http_timeout_ms=timeout,
        feed_max_age_ms=int(raw.get("feedMaxAgeMs", 60000)),
    )


def load_raw(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("ERROR: config must be a JSON object.")
    return data


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config.json"
