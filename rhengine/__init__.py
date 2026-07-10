"""rhengine — shared trading signal engine (single source of truth).

Consumed by RobinhoodAgent (Python app) directly and by Nydex (JS app) via
the canonical spec (strategies.json) + golden-vector fixtures.
"""

from .indicators import (  # noqa: F401
    score_ticker,
    score_all_tickers,
    classify_spy_regime,
    estimate_vix_regime,
    select_strategy_for_regime,
    resolve_thresholds,
    load_spec,
    spec_defaults,
    drop_partial_bar,
)
