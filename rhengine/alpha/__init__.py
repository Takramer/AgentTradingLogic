"""alpha/ — the post-audit research stack.

Separate from the legacy `rhengine` signal engine on purpose. The audit
(docs/AUDIT-2026-07-24.md) found the old stack's performance numbers
structurally unrecoverable: R-sums with no capital constraint, IID t-stats on
overlapping trades, a survivorship-biased universe, and zero cost. Rather than
patch those in place, this package rebuilds the measurement layer around the
three rules the audit demanded:

1. a **point-in-time universe** — the members of date D are decided by data <= D;
2. a **mark-to-market daily book** as the only source of performance numbers;
3. every result **benchmarked** against SPY and against an equal-weight version
   of its own universe, with clustered standard errors.

See docs/RMX-PREREGISTRATION.md for the frozen strategy specification.
"""
