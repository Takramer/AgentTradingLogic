# CLAUDE.md — intraday (day trading)

A separate project inside AgentTradingLogic: backtesting **day-trading** rules on
one-minute futures bars, MES first. It is not the swing-trading research in
`rhengine/`, and the parent CLAUDE.md's research content does not apply here.

**No day-trading rule has cleared.** The leading candidate is T10, the noise-area
rule with a VWAP trailing stop, long-only on Robinhood SPY, paper-trading from
2026-10-28; see [docs/RESULTS.md](docs/RESULTS.md) for every
trial run and why it stopped where it did. Do not carry over the swing work's
rejected signals, verdicts, holdouts, trial count or performance figures as
rules or priors. Ideas live in [docs/IDEAS.md](docs/IDEAS.md); the workflow and
checks in [docs/GUIDE.md](docs/GUIDE.md).

## Layout

| path | what |
|---|---|
| `data.py` | load, validate, RTH cut, back-adjusted continuous futures |
| `instruments.py` | MES/ES (assumed IBKR fees, margin), `SPY_RH` (Robinhood cash account: long only, settled cash), `MES_ON_SPY` (MES costs on SPY bars) |
| `engine.py` | the backtest and `causality_check` |
| `strategies.py` | `SessionDrift` baseline, ORB example, `IntradayMomentum`, `NoiseArea`, `DayFilter` |
| `validate.py` | the 12 checks and the verdict |
| `__main__.py` | `python -m intraday log/report/list` — pinned to this ledger |
| `data/` | `SPY_1m.csv`, `SPY_rth.pkl`, `VIX_daily.csv` (all gitignored), `trials.jsonl`, `holdout.jsonl`, `results.jsonl` |
| `paper.py` | forward paper record: append-once rows, rule hash, summary. No orders, no network |
| `scripts/` | `fetch_alpaca_bars.py` (SPY one-minute bars), `run_trials.py` (the pre-registered trials), `paper_trade.py` + `paper_trade.plist` (daily T10 paper record) |
| `docs/RESULTS.md` | pre-registration and every result |
| `notebooks/lab.ipynb` | the lab |
| `tests/` | `python -m unittest discover -s intraday/tests -t .` |

The only imports from `rhengine` are statistics helpers (`rhengine.trials`
maths, `rhengine.alpha.stats`). A test enforces that, and that the ledger is
`intraday/data/trials.jsonl`, never `research_data/trials.jsonl`.

## Rules

1. **Log every trial before running it:** `python -m intraday log --label "<idea>"`.
   Log attempts, not conclusions; record the outcome afterwards with `--resolves`.
2. **Quote numbers only from `validate.evaluate`**, on daily net PnL, with the
   Sharpe's 95% CI and the family-wise p beside any t-stat.
3. **Holdout once.** `validate.holdout()` logs the look before it runs; a second
   look is not clean. As of 2026-10-27 it is unspent; T10 is paper-trading first.
4. **Costs are assumed** (`instruments.py`): MES at IBKR $4.28 round trip and
   $2,500 intraday margin; SPY at Robinhood $0 commission + SEC fee + 1 cent per
   side, long only, one full-size entry per session (settled cash). Raise
   freely; lower only with executed fills.
5. **No order code in this package, ever.** A test bans it. CLEARS means paper
   trade, not trade. Never place a trade to test anything.
6. Every signal must pass `engine.causality_check` before its numbers count.

## Commands

```bash
python -m intraday log --label "<idea>"                 # before any run
python -m intraday report                               # trials so far, required t
python -m unittest discover -s intraday/tests -t .      # 40 tests
python intraday/scripts/paper_trade.py status          # T10 paper record
python intraday/scripts/run_trials.py T5               # re-run one trial
# then open intraday/notebooks/lab.ipynb
```

On this Mac Python is `.venv/bin/python` (python3 3.14); on Windows it is `py`.
