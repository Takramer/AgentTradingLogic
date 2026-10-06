"""Run the paper account. From the repo root:

    python3 -m polymarket_paper --once
    python3 -m polymarket_paper
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from polymarket_paper.bot import PaperBot
from polymarket_paper.market import MarketFeed
from polymarket_paper.rules import HaltTrading
from polymarket_paper.settings import default_config_path, load_raw, settings_from, validate


def _emit(line: str, log_path: Path | None) -> None:
    print(line, flush=True)
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _sleep_s(settings, rng: random.Random) -> float:
    jitter = rng.randrange(0, settings.poll_jitter_max_ms + 1) if settings.poll_jitter_max_ms else 0
    return (settings.poll_interval_ms + jitter) / 1000.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Paper-trade Polymarket crypto up/down markets. No orders are sent.",
    )
    parser.add_argument("--config", type=Path, default=None, help="JSON settings. Default: polymarket_paper/config.json")
    parser.add_argument("--state", type=Path, default=None, help="Paper account file. Default: beside the config, state.json")
    parser.add_argument("--log", type=Path, default=None, help="Append-only log. Default: beside the config, paper.log")
    parser.add_argument("--once", action="store_true", help="Read the current market, step a few times, and exit")
    parser.add_argument("--reset", action="store_true", help="Delete the saved paper account and start at initialBalance")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = args.config or default_config_path()
    try:
        raw = load_raw(config_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: could not read {config_path}: {exc}", file=sys.stderr)
        return 2
    errors = validate(raw)
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        print("ERROR: Fix the settings listed above and restart.", file=sys.stderr)
        return 2
    settings = settings_from(raw)

    state_path = args.state or (config_path.parent / "state.json")
    log_path = args.log or (config_path.parent / "paper.log")
    if args.reset and state_path.exists():
        state_path.unlink()

    feed = MarketFeed(settings)
    payload = None
    if state_path.exists():
        try:
            payload = _load_state(state_path)
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"ERROR: {state_path} could not be read ({exc}). "
                f"Move it aside, or run with --reset.",
                file=sys.stderr,
            )
            return 2
        if not isinstance(payload, dict):
            print(
                f"ERROR: {state_path} is not a paper account. "
                "Move it aside, or run with --reset.",
                file=sys.stderr,
            )
            return 2
        if payload.get("fingerprint") != settings.fingerprint():
            print(
                f"ERROR: {state_path} belongs to configuration {payload.get('fingerprint')}, "
                f"and the current settings are {settings.fingerprint()}. "
                "Strategy edits keep a separate paper account. "
                f"Run: python3 -m polymarket_paper --reset",
                file=sys.stderr,
            )
            return 2
        try:
            bot = PaperBot.from_state(settings, feed, payload)
        except (KeyError, TypeError, ValueError) as exc:
            print(
                f"ERROR: {state_path} is not a paper account ({exc}). "
                "Move it aside, or run with --reset.",
                file=sys.stderr,
            )
            return 2
    else:
        bot = PaperBot(settings, feed)

    _emit(f"{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}  {settings.banner()}", log_path)
    feed.start()
    rng = random.Random()
    try:
        if args.once:
            return _run_once(bot, state_path, log_path)
        return _run_forever(bot, settings, state_path, log_path, rng)
    finally:
        feed.stop()


def _run_once(bot: PaperBot, state_path: Path, log_path: Path) -> int:
    deadline = time.time() + 20
    seen = 0
    while time.time() < deadline and seen < 3:
        try:
            logs = bot.step(time.time())
        except HaltTrading as halt:
            for line in halt.logs:
                _emit(line, log_path)
            _save(state_path, bot.export_state())
            return 1
        for line in logs:
            _emit(line, log_path)
        if bot.saw_snapshot:
            seen += 1
        _save(state_path, bot.export_state())
        if seen >= 3:
            break
        time.sleep(0.4)
    if seen == 0:
        _emit(
            f"{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}  "
            "No Polymarket snapshot in 20s. The current window may be inside its "
            "last 30 seconds, or gamma-api.polymarket.com did not answer.",
            log_path,
        )
    return 0


def _run_forever(bot, settings, state_path, log_path, rng) -> int:
    while True:
        try:
            logs = bot.step(time.time())
            for line in logs:
                _emit(line, log_path)
            _save(state_path, bot.export_state())
            time.sleep(_sleep_s(settings, rng))
        except HaltTrading as halt:
            for line in halt.logs:
                _emit(line, log_path)
            _save(state_path, bot.export_state())
            return 1
        except KeyboardInterrupt:
            _emit(
                f"{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}  Stopped.",
                log_path,
            )
            _save(state_path, bot.export_state())
            return 0


if __name__ == "__main__":
    sys.exit(main())
