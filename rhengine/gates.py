"""
gates.py — external-gate integration for the backtest/scan pipeline.

The external legs (strategies.json external_legs) are scored by pure
functions in the adapter modules; this module is the seam that applies them
to trades. Design follows the repo's ablation pattern: the backtest
ANNOTATES every trade with each enabled gate's outcome (like the `legs`
pass-mask ablate.py slices), so ONE backtest run supports gate-on/off
ablation post-hoc with zero extra API calls. `mode: "filter"` per gate
additionally drops non-passing trades at record time — the
live-representative setting.

config.json block:

  "external_gates": {
    "av_earnings_sue":  {"enabled": true, "mode": "annotate",
                         "driftMin": 1, "driftMax": 10, "minSurprisePct": 5.0},
    "macro_flags":      {"enabled": true, "mode": "annotate",
                         "curveMin": 0.0, "hyOasMax": 4.5, "vixMax": 25.0}
  }

Annotation columns (stable keys on every trade when a gate is enabled):
  sue           "pass" | "fail" | "na"   (na = no cached earnings for ticker)
  sue_pct       surprisePercentage of the in-window report, or ""
  macro_risk_on 1 | 0 | ""               ("" = a series was missing/stale)

Budget rule: GateBook NEVER touches the AlphaVantage API — it reads
av_cache/ only (missing tickers annotate "na"). Backfill deliberately with
  py -m rhengine.gates backfill --max-requests 20
which spends up to that many of the 25/day free-tier calls on the stalest
uncached watchlist tickers. FRED preloads are cheap/cached and fetched at
GateBook construction (network failure degrades to "" annotations).

Filter-mode semantics: sue "na" DROPS the trade (no data = no confirmation
— live would not have the signal either); macro "" DROPS (unknown macro is
not risk-on). Annotate mode never drops anything.
"""

import json
from bisect import bisect_right

DAY_MS = 86_400_000
_MACRO_STALE_MS = 14 * DAY_MS  # observation older than this at signal time = missing


def _earnings_reader(source):
    """Resolve the SUE earnings source module. Both expose the same read
    surface (_cache_read(symbol) -> {"events": [...]}); sue_gate is shared
    (market_data_av). 'av' = AlphaVantage deep retroactive history (25/day);
    'finnhub' = the forward-accumulated bulk-calendar store (report-dated)."""
    if source == "finnhub":
        from . import market_data_finnhub as m
    else:
        from . import market_data_av as m
    return m


class GateBook:
    """Preloaded gate data + pure annotate()/keep(). All I/O lives in
    from_config(); the constructor takes plain data so tests and sweeps can
    build one without network or disk."""

    def __init__(self, sue_events=None, sue_cfg=None,
                 macro_series=None, macro_cfg=None):
        # sue_events: {ticker: [events ascending]} (market_data_av.parse_earnings shape)
        # macro_series: {series_id: [{"time": ms, "value": float} ascending]}
        self.sue_events = sue_events
        self.sue_cfg = sue_cfg or {}
        self.macro_series = macro_series
        self.macro_cfg = macro_cfg or {}
        self._macro_times = {sid: [o["time"] for o in obs]
                             for sid, obs in (macro_series or {}).items()}

    # ------------------------------------------------------------- loading --

    @classmethod
    def from_config(cls, cfg, tickers, lookback_days=400, verbose=True):
        """Build from config.json's external_gates block. Returns None when
        no gate is enabled (backtest runs exactly as before)."""
        gates = cfg.get("external_gates", {})
        sue_cfg = gates.get("av_earnings_sue", {})
        macro_cfg = gates.get("macro_flags", {})
        sue_events = macro_series = None

        if sue_cfg.get("enabled"):
            source = sue_cfg.get("source", "av")  # "av" (deep history) | "finnhub"
            reader = _earnings_reader(source)
            sue_events = {}
            for t in tickers:
                blob = reader._cache_read(t)
                if blob:
                    sue_events[t] = blob["events"]
            if verbose:
                print("  Gates: SUE earnings (source={}) cached for {}/{} tickers "
                      "(missing annotate 'na'; backfill: py -m rhengine.gates "
                      "backfill)".format(source, len(sue_events), len(tickers)))

        if macro_cfg.get("enabled"):
            from .market_data_fred import fetch_series, DEFAULT_SERIES
            macro_series = {}
            for sid in DEFAULT_SERIES:
                try:
                    macro_series[sid] = fetch_series(
                        sid, lookback_days=lookback_days + 60)
                except ConnectionError:
                    pass  # absent series -> "" annotations, never fake risk-on
            if verbose:
                print("  Gates: macro series loaded: {}".format(
                    ", ".join(sorted(macro_series)) or "none (offline?)"))

        if sue_events is None and macro_series is None:
            return None
        return cls(sue_events=sue_events, sue_cfg=sue_cfg,
                   macro_series=macro_series, macro_cfg=macro_cfg)

    # ---------------------------------------------------------- annotation --

    def _sue(self, ticker, time_ms):
        from .market_data_av import sue_gate
        events = (self.sue_events or {}).get(ticker)
        if not events:
            return "na", ""
        g = sue_gate(events, time_ms,
                     drift_min=self.sue_cfg.get("driftMin", 1),
                     drift_max=self.sue_cfg.get("driftMax", 10),
                     min_surprise_pct=self.sue_cfg.get("minSurprisePct", 5.0))
        # Long-only pipeline: a qualifying report must be a positive surprise.
        ok = g["pass"] and g["direction"] == "bull"
        return ("pass" if ok else "fail"), (g["surprise_pct"] if g["pass"] else "")

    def _macro_snapshot_at(self, time_ms):
        snap = {}
        for sid, obs in (self.macro_series or {}).items():
            i = bisect_right(self._macro_times[sid], time_ms) - 1
            if i >= 0 and time_ms - obs[i]["time"] <= _MACRO_STALE_MS:
                snap[sid] = obs[i]
        return snap

    def _macro(self, time_ms):
        from .market_data_fred import macro_flags, DEFAULT_SERIES
        snap = self._macro_snapshot_at(time_ms)
        if len(snap) < len(DEFAULT_SERIES):
            return ""  # incomplete macro state is unknown, not safe
        flags = macro_flags(snap,
                            curve_min=self.macro_cfg.get("curveMin", 0.0),
                            hy_oas_max=self.macro_cfg.get("hyOasMax", 4.5),
                            vix_max=self.macro_cfg.get("vixMax", 25.0))
        return 1 if flags["risk_on"] else 0

    def annotate(self, trade, ticker, time_ms):
        """Mutates the trade dict in place; keys are identical across all
        trades of a run so CSV headers stay stable."""
        if self.sue_events is not None:
            trade["sue"], trade["sue_pct"] = self._sue(ticker, time_ms)
        if self.macro_series is not None:
            trade["macro_risk_on"] = self._macro(time_ms)

    def keep(self, trade):
        """Filter-mode check. Annotate-mode gates always keep."""
        if (self.sue_events is not None
                and self.sue_cfg.get("mode") == "filter"
                and trade.get("sue") != "pass"):
            return False
        if (self.macro_series is not None
                and self.macro_cfg.get("mode") == "filter"
                and trade.get("macro_risk_on") != 1):
            return False
        return True


if __name__ == "__main__":
    # `py -m rhengine.gates backfill ...` delegates to the richer, resumable,
    # liquidity-ranked runner in backfill.py (one source of truth).
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        del sys.argv[1]
        from .backfill import main as backfill_main
        backfill_main()
    else:
        print("usage: py -m rhengine.gates backfill [--target-size N] "
              "[--from-csv f] [--max-requests N] [--loop]\n"
              "   or: py -m rhengine.backfill ...  (same runner)")
