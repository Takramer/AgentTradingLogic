"""intraday — research harness for day-trading rules on one-minute bars.

A separate project from the swing-trading research in `rhengine/`. Nothing
decided there (its rejected signals, verdicts, holdouts or trial ledger) is
assumed here; no day-trading rule has been chosen yet. The only things shared
are statistics helpers (`rhengine.trials` maths, `rhengine.alpha.stats`).
See intraday/CLAUDE.md and intraday/docs/GUIDE.md.

The harness backtests a rule, then runs the checks that catch a backtest
lying: look-ahead, multiple testing, random-entry nulls, cost stress,
sub-period stability, parameter plateaus, survivorship, and a once-only
holdout.

**There is no execution code in this package, and there must never be.** A rule
that clears here has earned a paper-trading run, nothing more.

    from intraday import data, instruments, engine, validate, strategies
"""
