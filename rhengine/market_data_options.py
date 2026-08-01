"""
market_data_options.py — the volatility-risk-premium measurement kit.

WHY THIS EXISTS, AND WHAT IT IS NOT

docs/AGENT-SPEC-VERDICT.md rejected every alpha claim in the tactical corpus
except one: the options overlay is "the only module with a documented economic
mechanism". That mechanism is the volatility risk premium — on S&P 500 options
implied volatility exceeds SUBSEQUENTLY realised volatility roughly 85% of the
time, by 2-4 vol points, which is the structural reason selling premium can pay.

This module is the arithmetic for measuring that, and nothing else. It contains
no strategy, no structure selection, no entry rule. Per the verdict, the work
is sequenced measurement-first precisely because steps that only measure cannot
produce a false positive the way a backtest can:

  1. get option price history                     (data — see DATA below)
  2. back out IV, compare to realised vol over the contract's ACTUAL life,
     net of the options spread, on the ~96 names RMX-B actually holds
  3. only if 2 shows a premium, model upside truncation against RMX-B's
     +1.78%/yr alpha. CBOE BXM returned ~1/3 of the S&P over the last decade;
     that is the prior to beat, and it is a bad one.
  4. structure-selection code after all of that, or never.

This module covers step 2's arithmetic. Steps 3 and 4 are not started.

THIS MODULE DOES NOT RESOLVE `CONFLICT-9` IN THE SOURCES' TERMS — IT REPLACES IT.
The spec's two candidate gates are IV Rank (IV against its own 52-week range)
and IV-vs-HV (IV against PAST realised vol). Neither is the researched
quantity. `vrp()` below compares IV at entry against realised vol over the
period the contract was actually alive, which is the quantity with a mechanism
attached. Do not add an IV-Rank function here.

DATA — WHAT IS AND IS NOT AVAILABLE (spiked 2026-07-30, Robinhood)

  get_option_quotes        CORRECTED 2026-07-31: `implied_volatility`, delta,
                           gamma, theta, vega, rho, open_interest AND volume are
                           all POPULATED via the Robinhood MCP server, plus
                           high/low_fill_rate_buy/sell_price (a fill model).
                           The 2026-07-30 line here said they returned NULL;
                           that was wrong, or the field was transiently empty.
                           Sample: research_data/options/spy_quotes_2026-07-31.json,
                           pinned by tests/test_options_costs.py.
  get_option_historicals   daily OHLC per contract. No IV, no greeks, and
                           option bars carry no volume either. THIS part of the
                           original spike still holds, and it is why implied_vol
                           below is still needed — for HISTORY, not live quotes.
  expired contracts        get_option_instruments state='expired' resolves
                           2026-07-17 but NOT 2026-06-19 — the instrument
                           listing has a short retention window (weeks). Once a
                           contract falls out of it you cannot DISCOVER it by
                           expiration date any more.
  expired price history    fully retrievable given the instrument UUID. The
                           2026-07-17 $250 AAPL call returned its complete life
                           from 2026-06-15 to expiry.

Read those four lines together and the build order falls out. IV is available
LIVE but not HISTORICALLY, so for any retrospective study it still has to be
DERIVED from the option price — which is what this module does. (Before the
2026-07-31 correction this paragraph said IV "is not purchasable from this
broker at any latency"; that overstated it. Live IV is served; historical IV is
not.) Price history is retroactive, so
no waiting is required for it. The only thing that expires is the ability to
enumerate old contracts, so the accumulator that has to start early is a
registry of contract UUIDs, NOT a daily IV snapshot. That is a much cheaper
standing job than notebooklm-findings.md §3.4 assumed when it specified an
"IV Rank provider (chain snapshots or an IV history feed)".

That accumulator is not built here. It is a scheduled job and wants a decision
about cadence before it starts writing.

WHAT WILL BITE, IN ORDER OF SIZE

1. **The options spread.** Equity option books are far wider than the 29.1 bps
   this repo measured on the underlying — frequently several hundred bps of the
   premium. A VRP of 2-4 vol points can be entirely inside it. Any VRP measured
   from a MARK is a gross number; the net one is the only one that decides
   anything. Measure with `vrp(...)` from bid and ask separately and report the
   band, never the midpoint alone.
2. **These are American options.** Black-Scholes prices European ones. For calls
   on a non-dividend-paying underlying early exercise is never optimal and the
   two agree; for puts, and for calls across a dividend, BS understates value
   and therefore OVERSTATES the IV backed out of a given price. That biases a
   VRP estimate in the direction of finding a premium. Never report a
   single-name put VRP from this module without saying so.
3. **Dividends.** `q` defaults to 0.0, which is wrong for most of the RMX-B
   book. Pass a real continuous yield.
4. **Close-to-close realised vol** understates true variation because it ignores
   the overnight and intraday path. It is the conventional choice and it is what
   `realised_vol` implements; it is not the only defensible one.

Implementation notes: no scipy dependency (not guaranteed present here) — the
normal CDF comes from `math.erf`. Inversion is bisection rather than
Newton-Raphson: BS price is strictly monotone in sigma, so bisection cannot
diverge, while Newton does at extreme moneyness where vega underflows.
"""

import math

# Bisection bracket for implied vol, in annualised decimal terms.
# 500% is above any equity option that is not a takeover rumour; 0.01% is below
# any real quote. A root outside the bracket returns None rather than a clamp,
# because a clamped vol looks like a measurement and is not one.
_VOL_LO = 1e-4
_VOL_HI = 5.0
_MAX_ITER = 200
# Convergence tolerance on the VOL bracket, not on the price residual. See the
# loop in implied_vol() for why that distinction is load-bearing.
_TOL = 1e-10

TRADING_DAYS = 252


def _norm_cdf(x):
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, r, sigma, kind="call", q=0.0):
    """Black-Scholes-Merton price of a EUROPEAN option.

    S spot, K strike, T years to expiry, r continuous risk-free rate, sigma
    annualised vol, q continuous dividend yield. `kind` is "call" or "put".

    At T <= 0 or sigma <= 0 returns discounted intrinsic value, which is the
    correct limit and keeps the inverter's bracket well-defined.
    """
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put', got {!r}".format(kind))
    if S <= 0 or K <= 0:
        raise ValueError("S and K must be positive")
    fwd = S * math.exp(-q * T) if T > 0 else S
    disc_k = K * math.exp(-r * T) if T > 0 else K
    if T <= 0 or sigma <= 0:
        return max(0.0, fwd - disc_k) if kind == "call" else max(0.0, disc_k - fwd)
    vol_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
    d2 = d1 - vol_t
    if kind == "call":
        return fwd * _norm_cdf(d1) - disc_k * _norm_cdf(d2)
    return disc_k * _norm_cdf(-d2) - fwd * _norm_cdf(-d1)


def has_vol_information(price, intrinsic):
    """Is there enough TIME VALUE in this price to back a vol out of it?

    All of an option's volatility information lives in `price - intrinsic`.
    When that difference shrinks to the floating-point noise floor of `price`
    itself, the price is numerically indistinguishable from a zero-vol price
    and NO inversion method can recover sigma — bisection will happily return
    a precise-looking number that is determined by rounding.

    This bites on deep in-the-money short-dated contracts: a 100/70 call with
    two weeks to run is worth ~30.04, of which the time value at 35% vol is
    ~1e-12 — roughly 300 ULPs of 30. Inverting that recovers 0.3488 against a
    true 0.35. The failure is silent, which is what makes it worth a named
    guard rather than a comment.

    The 1e-12 relative floor is well above double precision (~2.2e-16) and well
    below any time value a tradeable contract actually carries.
    """
    if price is None or intrinsic is None:
        return False
    return (price - intrinsic) > max(abs(price), 1.0) * 1e-12


def implied_vol(price, S, K, T, r=0.0, kind="call", q=0.0):
    """Back the annualised implied volatility out of an option price.

    Returns None — never a guess — when the price carries no volatility
    information:

      * it sits at or below intrinsic value (all the premium is moneyness, so
        any sigma near zero fits and the answer is undefined);
      * it sits at or above the theoretical maximum (S*e^-qT for a call,
        K*e^-rT for a put), which means the quote is stale, crossed, or the
        contract is being priced on something other than this model;
      * the root lies outside [0.01%, 500%];
      * T <= 0.

    None is a real answer here and callers must propagate it rather than
    substituting a default. A VRP study that silently fills unresolvable
    contracts with, say, 30% vol is measuring its own default.

    ACCURACY CAVEAT, restated because it decides sign: US equity options are
    American. BS is European. For puts and for calls spanning a dividend, the
    true option is worth MORE than BS at the same sigma, so inverting a real
    price through BS returns a vol that is too HIGH — which inflates any VRP
    computed from it. This function is honest about the model it is inverting
    and cannot correct for that; the correction is to use an American pricer or
    to restrict the study to calls on non-payers.
    """
    if T is None or T <= 0 or price is None or price < 0:
        return None
    if S <= 0 or K <= 0:
        return None

    fwd = S * math.exp(-q * T)
    disc_k = K * math.exp(-r * T)
    if kind == "call":
        lo_bound, hi_bound = max(0.0, fwd - disc_k), fwd
    elif kind == "put":
        lo_bound, hi_bound = max(0.0, disc_k - fwd), disc_k
    else:
        raise ValueError("kind must be 'call' or 'put', got {!r}".format(kind))
    if price >= hi_bound:
        return None
    # lo_bound is the zero-vol (discounted intrinsic) price, so this covers
    # both "at or below intrinsic" and "time value below the float noise
    # floor". See has_vol_information for why the second case needs saying.
    if not has_vol_information(price, lo_bound):
        return None

    lo, hi = _VOL_LO, _VOL_HI
    p_lo = bs_price(S, K, T, r, lo, kind, q)
    p_hi = bs_price(S, K, T, r, hi, kind, q)
    if not (p_lo <= price <= p_hi):
        return None                       # root outside the bracket

    # BS price is strictly increasing in sigma, so bisection converges
    # unconditionally. Slower than Newton and immune to vega underflow.
    #
    # Convergence is tested on the VOL bracket, never on the price residual.
    # An absolute price tolerance is the obvious way to write this and it is
    # wrong, because option prices span many orders of magnitude while the
    # tolerance does not: a deep-OTM contract worth ~1e-5 has vega so small
    # that a 1e-8 price match still leaves the vol off by ~0.0017 — sixth of a
    # vol point, against a VRP whose entire published magnitude is 2-4 points.
    # It biases hardest on cheap far-from-money options, which is exactly where
    # a put-side VRP study would live. Bisecting to 1e-10 in vol costs ~36
    # halvings and removes the problem.
    for _ in range(_MAX_ITER):
        if (hi - lo) < _TOL:
            break
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, r, mid, kind, q) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def realised_vol(closes, trading_days=TRADING_DAYS):
    """Annualised close-to-close realised volatility from a price series.

    Uses the sample standard deviation of log returns (ddof=1) scaled by
    sqrt(trading_days). Needs at least 3 closes — two give one return and a
    zero-variance answer that is not a measurement.

    Returns None on insufficient or non-positive data. See the module docstring
    for why close-to-close understates true variation; it is the conventional
    denominator for a VRP and switching to Parkinson or Garman-Klass would make
    this number incomparable to the published VRP figures.
    """
    if closes is None or len(closes) < 3:
        return None
    rets = []
    for prev, cur in zip(closes, closes[1:]):
        if prev is None or cur is None or prev <= 0 or cur <= 0:
            return None
        rets.append(math.log(cur / prev))
    n = len(rets)
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(trading_days)


def vrp(iv, rv):
    """The volatility risk premium: implied minus SUBSEQUENTLY realised vol.

    Both in annualised decimal terms; the result is in the same units, so
    multiply by 100 for the "2-4 volatility points" the literature quotes.

    Positive = the option was priced above what actually happened = the seller
    was paid. Returns None if either input is None, so an unresolvable contract
    propagates as unknown instead of contaminating an average.

    THE ORDER OF OPERATIONS IS THE WHOLE POINT. `rv` must be measured over the
    contract's life AFTER the moment `iv` was observed. Feeding it trailing
    realised vol computes IV-vs-HV, which is the source construct the evidence
    audit rejected (CONFLICT-9), not the VRP. Nothing in this signature can
    enforce that — it is the caller's responsibility and it is the single
    easiest way to get a look-ahead-free-looking number that means nothing.
    """
    if iv is None or rv is None:
        return None
    return iv - rv


def year_fraction(start_ms, end_ms):
    """Calendar-time year fraction between two epoch-millisecond timestamps.

    CALENDAR time, not trading time, because that is what the discounting and
    the diffusion in Black-Scholes both use. Do not swap in a 252-day count to
    "match" `realised_vol` — the two quantities are deliberately measured on
    different clocks, and reconciling them by changing this would misprice
    every contract.

    Returns None for a non-positive span.
    """
    if start_ms is None or end_ms is None:
        return None
    days = (end_ms - start_ms) / 86_400_000.0
    return days / 365.0 if days > 0 else None
