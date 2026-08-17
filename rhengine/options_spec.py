"""
options_spec.py — the codeable parameters in the 0DTE / wheel source corpus,
and the capital arithmetic that decides which of them this account can hold.

WHAT THIS IS NOT. No entry rule, no signal, no structure selection, nothing that
returns a trade — the same discipline as `market_data_options.py`,
`options_costs.py` and `options_registry.py`. `STATUS` below is `"blocked"` and
`tests/test_options_spec.py` pins it there, so no import of this module can be
mistaken for permission to trade. CLAUDE.md rule 6: autonomy is earned.

WHAT IT IS, in two parts.

  1. A PARAMETER REGISTRY WITH PROVENANCE. Seven sources were supplied on
     2026-08-01 (docs/OPTIONS-SOURCES-2026-08-01.md). Each numeric rule any of
     them states is recorded below with the sources that state it and whether
     they AGREE. Provenance is the entire point: "stop at 2x the credit
     received" appears in two independently-written retail articles, and
     "only trade if VIX is above 14" appears in exactly one. Those are
     different objects. Neither is evidence — a blog is not a result, same as
     CLAUDE.md rule 4 and the video rule in docs/VIDEO-INTAKE-2026-07-31.md —
     but a corpus-wide convention and a single author's number should not be
     stored as though they were the same thing, and once they are separated the
     disagreements become visible. Three are recorded in `CONFLICTS`.

  2. CAPITAL FEASIBILITY. Every strategy in the corpus has a collateral
     requirement, and for a small account that requirement, not the edge, is
     what decides whether it can be run at all. `feasibility()` answers "what
     can $N actually hold" per structure and per underlying, from live
     contract specs. Two results come straight out of the arithmetic and are
     not opinions:

       - THE WHEEL CANNOT BE RUN ON SPX, XSP OR NDX AT ALL. Those settle in
         cash. There is nothing to be assigned and nothing to write a covered
         call against, so the strategy's second leg does not exist. The three
         videos in this corpus are all wheel videos, and the two instruments
         asked about for 0DTE are the ones the wheel cannot use.
       - THE WHEEL ON SPY COSTS ~$74.7k OF COLLATERAL PER CONTRACT (100 x
         746.81), QQQ ~$68.8k. There is no fractional version: the contract
         multiplier is 100 and it does not divide.

THE PRIOR FOR THE WHEEL IS ALREADY PUBLISHED, WHICH IS UNUSUAL AND USEFUL.
The wheel is a cash-secured put that, on assignment, becomes a covered call.
CBOE publishes both halves as indices: PUT (put-write) and BXM (buy-write).
docs/VIDEO-INTAKE-2026-07-31.md already records the relevant number — BXM
returned ~1/3 of the S&P over the last decade, because truncated upside costs
more than the premium pays in a bull market. So unlike every other strategy in
this corpus, the wheel does not need a forward sample to be evaluated: its
return series exists, is survivorship-free, and is the thing any wheel proposal
has to beat. Get PUT/BXM into research_data/external/ and the question is
answerable without waiting.

Usage:
    py -m rhengine.options_spec                     # parameters + provenance
    py -m rhengine.options_spec --conflicts         # only where sources disagree
    py -m rhengine.options_spec --capital 2000      # what this account can hold
    py -m rhengine.options_spec --strikes --spot 746.81 --iv 0.16 --dte 1
"""

from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from statistics import NormalDist

# Nothing in this repo may treat any of the below as tradeable. Pinned by test.
STATUS = "blocked"

# Why, in the repo's own terms. Each line is a standing rule or a measured fact,
# not a preference. All four must clear before an options book can be proposed.
BLOCKERS = (
    "no retrospective sample: expired-contract discovery lapses in weeks, so "
    "options_registry accumulates FORWARD only and nothing has lived and died yet",
    "no realised-vs-implied measurement: market_data_options step 2 is unblocked "
    "but not run",
    "the published prior is bad: CBOE BXM ~1/3 of the S&P over the last decade, "
    "and BXM is literally the covered-call half of the wheel",
    "CLAUDE.md rule 6 — autonomy is earned, and no options path has any track "
    "record here at all",
)

# ------------------------------------------------------------------- sources

SOURCES = {
    "MX": dict(kind="article", host="marketxls.com", topic="0DTE SPY",
               url="https://marketxls.com/blog/how-to-trade-0dte-spy-options-expert-insights"),
    "TA": dict(kind="article", host="tradealgo.com", topic="0DTE SPY",
               url="https://www.tradealgo.com/trading-guides/options/0dte-spy-options-strategy"),
    "TB": dict(kind="article", host="tradingblock.com", topic="0DTE structures",
               url="https://www.tradingblock.com/blog/0dte-options-strategies"),
    "TP": dict(kind="article", host="tradeproacademy.com", topic="SPY day trading",
               url="https://tradeproacademy.com/how-to-day-trade-spy-options/"),
    # All three videos are WHEEL content, not 0DTE. Titles resolved via oEmbed;
    # the transcripts are not fetchable (YouTube renders client-side), so no
    # parameter below is attributed to them. See docs/OPTIONS-SOURCES-2026-08-01.md.
    "V1": dict(kind="video", host="InTheMoney", topic="wheel",
               title="How to Trade the Wheel Strategy",
               url="https://youtu.be/siFsIleNTzk"),
    "V2": dict(kind="video", host="Invest with Henry", topic="wheel",
               title="The Wheel Strategy For Beginners 2026 MASTERCLASS (2+ Hours)",
               url="https://youtu.be/leI79Bjzd5k"),
    "V3": dict(kind="video", host="The Cashflow Academy", topic="wheel",
               title='The "Wheel" Options Strategy I Will Use For Life',
               url="https://youtu.be/-l9qSLAG3dM"),
}

# ---------------------------------------------------------------- parameters
# (name, value as stated, unit, sources, note)
#
# `sources` length is the only quality signal available and it is a weak one:
# four retail articles agreeing is four retail articles, not a finding. It is
# recorded because a corpus-wide convention at least tells you what the genre
# does, whereas a lone number tells you what one author does.

PARAMS = (
    dict(name="short_strike_delta", value=(0.10, 0.15), unit="delta",
         sources=("MX", "TA", "TB"),
         note="premium SELLING: MX 0.10-0.15 credit spreads, TA 0.10 IC / 0.15 "
              "credit spread, TB ~0.15 shorts and ~0.10 strangle legs. The one "
              "parameter every selling source in the corpus agrees on."),
    dict(name="long_strike_delta", value=(0.25, 0.50), unit="delta",
         sources=("TA", "TB", "TP"),
         note="premium BUYING: TA 'ATM or slightly ITM', TB 'high delta', TP "
              "0.25-0.30 and >0.30 for 0-2 DTE. Not a disagreement with the "
              "line above — it is the other side of the same trade."),
    dict(name="spread_width", value=3.0, unit="dollars",
         sources=("TA",),
         note="TA: '0.10 delta put spread and 0.10 delta call spread, both $3 "
              "wide', widening to $4-5 when VIX is 18-25."),
    dict(name="stop_multiple_of_credit", value=2.0, unit="x credit",
         sources=("MX", "TA"),
         note="the most-corroborated single number in the corpus: two "
              "independently written sources both say exit a credit spread at "
              "2x the credit received. TB dissents from stops entirely, warning "
              "that stop orders on options become market orders."),
    dict(name="stop_pct_directional", value=0.40, unit="fraction of premium",
         sources=("TA",), note="TA only."),
    dict(name="time_stop_minutes", value=(60, 90), unit="minutes",
         sources=("MX",),
         note="MX: exit a long option that has not worked within 60-90 minutes. "
              "This is the only rule in the corpus that prices theta directly, "
              "and options_costs.breakeven_move_pct is the repo's version of "
              "the same idea."),
    dict(name="profit_target_pct_of_credit", value=(0.50, 0.80), unit="fraction",
         sources=("TA",),
         note="TA gives both 'close at 50% of credit' and 'capture 80% of "
              "credit' in one page without reconciling them."),
    dict(name="risk_pct_per_trade", value=(0.01, 0.02), unit="fraction of account",
         sources=("MX", "TA"),
         note="agreed. See `min_account_for` — applied to a defined-risk max "
              "loss it is the binding constraint for a small account, and it "
              "does NOT translate to the wheel (see CONFLICTS)."),
    dict(name="max_concurrent_positions", value=(2, 3), unit="count",
         sources=("TA",), note="TA only; 2 conservative, 3 moderate."),
    dict(name="max_scalps_per_day", value=(1, 2), unit="count",
         sources=("TA",), note="TA only."),
    dict(name="min_account", value=(5000, 10000), unit="dollars",
         sources=("MX",),
         note="MX states $5k-$10k as the minimum for 'proper position sizing' "
              "of 0DTE SPY. Nothing in the corpus proposes a method below it."),
    dict(name="entry_window_et", value=("09:35", "12:00"), unit="clock",
         sources=("MX", "TA", "TB"),
         note="MX 10:00-12:00 'most active and liquid', TA 9:35 for the iron "
              "condor, TB 'mid to late morning'. Compatible. Note this is the "
              "same window scripts/rh_spreads.py independently found quotes to "
              "be tightest in, which is corroboration of MECHANISM (liquidity), "
              "the only kind these sources can supply."),
    dict(name="no_new_positions_after_et", value="14:00", unit="clock",
         sources=("MX", "TA", "TB"),
         note="TA 14:00 explicitly; MX and TB both say close before the final "
              "hour (15:00). Gamma risk, which is real arithmetic, not a claim."),
    dict(name="vix_floor", value=14.0, unit="index points",
         sources=("TA",),
         note="TA only, and this repo has ALREADY REJECTED a VIX gate: the "
              "vix_utils term-structure gate was an in-sample mirage. A VIX "
              "level threshold is the same class of object. Do not build it "
              "without out-of-sample evidence that does not exist here."),
    dict(name="iv_ceiling", value=0.30, unit="annualised vol",
         sources=("TP",),
         note="TP: 'avoid trading when IV >30%' with SPY normal 15-20%. This is "
              "an IV-level gate, which market_data_options.py argues is the "
              "wrong quantity (CONFLICT-9): the researched quantity is IV at "
              "entry vs realised vol over the contract's ACTUAL life."),
    dict(name="event_blackout", value=("CPI", "NFP", "FOMC"), unit="calendar",
         sources=("MX", "TB"), note="agreed, and cheap to implement."),
    dict(name="intraday_trend_filter", value="EMA20 on 5-minute bars",
         unit="indicator", sources=("TP",),
         note="BLOCKED ON DATA, not on evidence: this engine runs on DAILY "
              "bars. Every indicator rule in the corpus (TP's EMA20/volume "
              "profile, MX's put-call volume ratio, TB's volume-vs-open-"
              "interest) needs intraday bars the research stack does not have."),
)

# ------------------------------------------------------------------ conflicts

CONFLICTS = (
    dict(id="OC-1", topic="whether to trade 0DTE at all",
         detail="TP — on a page titled 'how to day trade SPY options' — says "
                "avoid same-day expiry, prefer the next expiry, and cap at 7 "
                "DTE, because theta must be compensated with higher delta. "
                "MX, TA and TB all build their entire method on 0DTE. Within "
                "one seven-source corpus the headline question is unresolved.",
         bearing="options_costs.py already measured the thing they are arguing "
                 "about: the 3-DTE 745C needs a 0.11% move in one session to "
                 "cover spread plus decay. That is the number to decide on, "
                 "and it is measured here, not asserted there."),
    dict(id="OC-2", topic="stops",
         detail="MX and TA both specify hard stops (2x credit; 40%). TB says "
                "do not use stop orders on options at all because they trigger "
                "market orders into a wide book.",
         bearing="both can be right — a PRICE rule with a LIMIT execution. The "
                 "corpus never separates the rule from the order type, and an "
                 "unattended executor would have to."),
    dict(id="OC-3", topic="the 1-2% rule against the wheel",
         detail="MX and TA agree on risking 1-2% of the account per trade. The "
                "three videos are wheel videos, and a cash-secured put on SPY "
                "ties up ~$74.7k of collateral with a max loss far above 2% of "
                "any account that can only just post it.",
         bearing="the sizing rule is written for defined-risk day trades and "
                 "does not transfer. `min_account_for` reports collateral and "
                 "max loss separately rather than pretending one rule covers "
                 "both, and refuses to quote a 1-2% account size for the wheel."),
)

# ---------------------------------------------------- instruments, as served
# Verified against the Robinhood MCP chain/quote endpoints on 2026-08-01
# (levels are the 2026-07-31 close; the market was shut). `sellout_secs` and
# `settles` are the fields that decide small-account feasibility, and they are
# the ones no source in the corpus mentions.

INSTRUMENTS = {
    "SPY":  dict(level=746.81, multiplier=100, settles="shares",
                 strike_step=1.0, zero_dte=True, sellout_secs=1800,
                 note="broker force-closes 30 min before expiry if the account "
                      "cannot cover assignment"),
    "QQQ":  dict(level=687.92, multiplier=100, settles="shares",
                 strike_step=1.0, zero_dte=True, sellout_secs=1800,
                 note="daily expirations confirmed M-F; same sellout rule"),
    "SPX":  dict(level=7489.72, multiplier=100, settles="cash",
                 strike_step=5.0, zero_dte=True, sellout_secs=0,
                 note="SPXW chain carries daily expirations. Cash settled, so "
                      "no assignment and no sellout — but also no wheel."),
    "XSP":  dict(level=748.97, multiplier=100, settles="cash",
                 strike_step=1.0, zero_dte=True, sellout_secs=0,
                 note="1/10th SPX. The small-account index route, and the one "
                      "instrument in this list nobody in the corpus mentions."),
    "NDX":  dict(level=28274.20, multiplier=100, settles="cash",
                 strike_step=10.0, zero_dte=False, sellout_secs=0,
                 note="notional ~$2.8M per contract; listed for completeness"),
}

VIX_LEVEL = 15.99   # 2026-07-31 close, for reference only — see `vix_floor`.


# ------------------------------------------------------ when you can get out
# Surveyed 2026-08-01 across 12 chains via get_option_chains. The question was
# "do the larger ones trade until 4:15?" — the answer is yes for some, and the
# rule is NOT what it looks like.
#
#   late_close_state='enabled'   the 4:15 PM ET late close (equity/ETF options)
#   extended_hours_state='enabled'  the CURB session, index chains only, and it
#                                is longer than the late close — the broker
#                                accepts market_hours='regular_curb_hours' and
#                                'regular_curb_overnight_hours'
#   sellout_time_to_extension    seconds before expiry at which the broker
#                                FORCE-CLOSES a position the account cannot
#                                cover. 1800 on every equity/ETF chain, 0 on
#                                every index chain.
#
# THE PATTERN IS NOT SIZE, AND IT IS NOT INDEX-TRACKING. XLF and XLE (sector
# SPDRs) have the late close; TQQQ (leveraged Nasdaq-100) and GDX do not,
# despite tracking indices and trading heavily. ARKK and AAPL do not. It is an
# exchange-designated list, so it CANNOT be inferred — read the flag per chain.
LATE_CLOSE_SURVEY = {
    # symbol: (late_close, curb_extended_hours, sellout_secs, kind)
    "SPY":  (True,  False, 1800, "ETF"),
    "QQQ":  (True,  False, 1800, "ETF"),
    "IWM":  (True,  False, 1800, "ETF"),
    "DIA":  (True,  False, 1800, "ETF"),
    "XLF":  (True,  False, 1800, "sector ETF"),
    "XLE":  (True,  False, 1800, "sector ETF"),
    "GLD":  (True,  False, 1800, "commodity trust"),
    "EEM":  (True,  False, 1800, "ETF"),
    "TQQQ": (False, False, 1800, "leveraged ETF"),
    "GDX":  (False, False, 1800, "miners ETF"),
    "ARKK": (False, False, 1800, "active ETF"),
    "AAPL": (False, False, 1800, "single stock"),
    "SPX":  (False, True,     0, "index (SPXW dailies)"),
    "XSP":  (False, True,     0, "index"),
}

# What this broker's agent interface will actually accept. From the
# review_option_order / place_option_order contract, read 2026-08-01.
EXECUTION_LIMITS = (
    "SINGLE-LEG ONLY. Multi-leg spreads are not supported through this "
    "interface even on an option_level_3 account. Every credit spread and "
    "iron condor in the source corpus is therefore UNEXECUTABLE here — what "
    "is left is long calls/puts, covered calls and cash-secured puts, i.e. "
    "Level 2, i.e. the wheel and naked directional buying.",
    "Extended-hours sessions accept LIMIT + immediate orders only.",
    "stop_market is sell-to-close only, GFD, regular hours only, and its stop "
    "must sit below the current ask — which is the executable form of the "
    "corpus' stop rules, and it is blocked in every extended session (OC-2).",
    "The account must be agentic_allowed=true with option_level_2 or _3.",
)


def session_close(symbol):
    """What the last chance to close is, per symbol. Raises on an unsurveyed
    one rather than guessing — the flag is exchange-designated and cannot be
    inferred from size, issuer or whether it tracks an index."""
    s = symbol.upper()
    if s not in LATE_CLOSE_SURVEY:
        raise KeyError(
            f"{s} not surveyed. Read late_close_state and "
            f"extended_hours_state from get_option_chains; do not assume.")
    late, curb, sellout, kind = LATE_CLOSE_SURVEY[s]
    if curb:
        return dict(symbol=s, kind=kind, last_close="CURB session (beyond "
                    "16:15 ET; overnight accepted)", forced_exit=None,
                    note="cash settled: no assignment, no forced sellout")
    return dict(symbol=s, kind=kind,
                last_close="16:15 ET" if late else "16:00 ET",
                forced_exit=f"{sellout // 60} min before expiry",
                note="broker force-closes an expiring position the account "
                     "cannot cover")


# ------------------------------------------------------------------ arithmetic

def strike_for_delta(spot, iv, dte_days, target_delta, opt_type="call",
                     r=0.04, step=None):
    """The strike whose Black-Scholes delta is ~`target_delta`.

    Inverts delta = N(d1) for calls, N(d1) - 1 for puts:

        d1 = Phi^-1(N)                    N = delta (call) or 1 + delta (put)
        K  = S * exp(-d1*sigma*sqrt(T) + (r + sigma^2/2)*T)

    WHY THIS BELONGS IN A MEASUREMENT MODULE. It is moneyness arithmetic, not a
    signal: it consults no price history and no indicator, so a sample of
    contracts chosen this way stays UNCONDITIONAL in the sense
    options_registry.plan_strikes cares about. It exists so the accumulator can
    capture the contracts this corpus actually trades (0.10 / 0.15 / 0.30
    delta) instead of only a fixed dollar ladder around ATM, which at 30-60 DTE
    misses the short strikes entirely.

    `target_delta` is given as a magnitude (0.10 means a 0.10 put OR call).
    Returns None for T <= 0 — a 0DTE contract at expiry has no delta to invert;
    use the intraday-remaining fraction (e.g. dte_days=0.25) instead.
    """
    if spot <= 0 or iv <= 0:
        raise ValueError("spot and iv must be positive")
    if not 0.0 < target_delta < 1.0:
        raise ValueError("target_delta is a magnitude strictly inside (0, 1)")
    t = dte_days / 365.0
    if t <= 0:
        return None
    n = target_delta if opt_type == "call" else 1.0 - target_delta
    d1 = NormalDist().inv_cdf(n)
    k = spot * math.exp(-d1 * iv * math.sqrt(t) + (r + iv * iv / 2.0) * t)
    if step:
        k = round(k / step) * step
    return round(k, 4)


def decay_days(entry, exit_):
    """Calendar days charged vs trading days of movement bought.

    THE WEEKEND TRAP, made computable. Theta accrues per CALENDAR day; the
    underlying only moves on TRADING days. A Friday-to-Monday hold pays three
    days of decay for one day of movement, which roughly triples the breakeven
    move (docs/VIDEO-INTAKE-2026-07-31.md measured 0.11% -> 0.29% on the 3-DTE
    745C). docs/VIDEO-INTAKE flagged that any unattended short-dated path needs
    an explicit weekend rule; this is the quantity that rule would test.

    Returns (calendar, trading, ratio). Ratio 1.0 is a clean intraweek hold;
    3.0 is a weekend. Holidays are not modelled — it counts weekdays, so it
    UNDERSTATES the penalty around a market holiday.
    """
    a = date.fromisoformat(str(entry)[:10])
    b = date.fromisoformat(str(exit_)[:10])
    cal = (b - a).days
    if cal < 0:
        raise ValueError("exit precedes entry")
    trading = sum(1 for i in range(1, cal + 1)
                  if (a + timedelta(days=i)).weekday() < 5)
    # A same-day (0DTE) hold buys one session and is charged no overnight decay.
    if cal == 0:
        return 0, 1, 0.0
    return cal, trading, (cal / trading if trading else float("inf"))


def collateral(structure, spot=None, strike=None, premium=None, width=None,
               credit=None, multiplier=100):
    """Buying power a broker holds against one unit of `structure`, and the
    worst case, as (collateral, max_loss). Both in dollars, per contract.

    They differ, and conflating them is how a small account gets surprised:
    a cash-secured put on SPY posts $74.5k of collateral but its max loss is
    the strike minus the credit, and its EXPECTED outcome is owning the shares.
    A long 0DTE call posts its premium and can lose all of it by 4pm.
    """
    m = multiplier
    if structure in ("long_call", "long_put"):
        d = premium * m
        return d, d
    if structure in ("credit_spread", "put_credit_spread", "call_credit_spread"):
        loss = (width - credit) * m
        return loss, loss
    if structure == "iron_condor":
        # One side can be tested at a time, so brokers hold the wider wing only.
        loss = (width - credit) * m
        return loss, loss
    if structure == "debit_spread":
        d = (premium) * m
        return d, d
    if structure == "cash_secured_put":
        # Max loss is to zero. Stated honestly rather than as "the premium".
        return strike * m, (strike - (credit or 0.0)) * m
    if structure == "covered_call":
        return spot * m, (spot - (credit or 0.0)) * m
    raise ValueError(f"unknown structure {structure!r}")


def wheel_collateral(symbol, strike=None):
    """Collateral for one wheel cycle on `symbol`, or None if impossible.

    Returns None for cash-settled underlyings, which is not a technicality: the
    wheel's second leg is a covered call on ASSIGNED SHARES. SPX, XSP and NDX
    settle in cash, so assignment delivers a debit and no stock, and there is
    nothing to write a call against. The strategy does not degrade on those
    instruments — it does not exist.
    """
    inst = INSTRUMENTS[symbol]
    if inst["settles"] != "shares":
        return None
    k = strike if strike is not None else inst["level"]
    return k * inst["multiplier"]


def min_account_for(structure, max_loss, risk_pct=0.02):
    """Account the corpus' own 1-2% rule implies, or None where it does not apply.

    Returns None for cash_secured_put and covered_call: MX and TA wrote that
    rule for defined-risk day trades, and applying it to a wheel leg produces a
    number nobody means (2% of a SPY cash-secured put's max loss implies a
    ~$3.7M account). CONFLICT OC-3. Reporting None is the honest output; a
    plausible-looking number here would be an invention.
    """
    if structure in ("cash_secured_put", "covered_call"):
        return None
    if not 0.0 < risk_pct <= 1.0:
        raise ValueError("risk_pct must be in (0, 1]")
    return max_loss / risk_pct


def atm_premium_estimate(spot, iv, dte_days):
    """Rough ATM option premium, for SIZING ONLY — never for pricing a trade.

    The standard small-T approximation C ~ 0.4 * S * sigma * sqrt(T). It is here
    so `feasibility` can answer "can $2,000 buy a 0DTE SPX call" before any real
    quote exists, and it is wrong by enough that it must never be used to decide
    whether a trade is cheap. Once options_registry has captured real 0DTE
    quotes, replace every call to this with the captured mark.
    """
    t = max(dte_days, 0.0) / 365.0
    if t <= 0:
        t = 1.0 / 365.0     # an at-the-open 0DTE contract, not a zero
    return 0.4 * spot * iv * math.sqrt(t)


def feasibility(capital, iv=0.16, dte_days=1.0, risk_pct=0.02):
    """What `capital` can hold, per underlying and per structure.

    The point of the table: for a small account the binding constraint is not
    edge, spread or decay — it is the contract multiplier, which does not
    divide. Every "no" below is arithmetic.
    """
    rows = []
    for sym, inst in INSTRUMENTS.items():
        s, m = inst["level"], inst["multiplier"]
        prem = atm_premium_estimate(s, iv, dte_days)

        # 1. buy one ATM contract
        col, loss = collateral("long_call", premium=prem, multiplier=m)
        tag = "long ATM (0DTE)" if inst["zero_dte"] else "long ATM (no 0DTE)"
        rows.append(dict(symbol=sym, structure=tag,
                         collateral=col, max_loss=loss,
                         min_account=min_account_for("long_call", loss, risk_pct),
                         ok=col <= capital,
                         zero_dte=inst["zero_dte"]))

        # 2. a 0.10-delta credit spread, one width on the symbol's own grid
        width = max(inst["strike_step"] * 3, 1.0)
        cred = width * 0.30      # TA: target 25-35% of width in credit
        col, loss = collateral("credit_spread", width=width, credit=cred,
                               multiplier=m)
        rows.append(dict(symbol=sym, structure=f"credit spread ${width:g} wide",
                         collateral=col, max_loss=loss,
                         min_account=min_account_for("credit_spread", loss,
                                                     risk_pct),
                         ok=col <= capital, zero_dte=inst["zero_dte"]))

        # 3. the wheel
        wc = wheel_collateral(sym)
        rows.append(dict(symbol=sym, structure="wheel (cash-secured put)",
                         collateral=wc,
                         max_loss=None if wc is None else wc - prem * m,
                         min_account=None,
                         ok=(wc is not None and wc <= capital),
                         zero_dte=inst["zero_dte"],
                         impossible=wc is None))
    return rows


# ----------------------------------------------------------------------- cli

def _print_params(only_conflicts=False):
    if not only_conflicts:
        print(f"STATUS: {STATUS}  — nothing here is tradeable. Blockers:")
        for b in BLOCKERS:
            print(f"  - {b}")
        print(f"\n{'parameter':<28}{'stated value':<24}{'src':<14}n")
        print("-" * 74)
        for p in PARAMS:
            v = p["value"]
            v = f"{v[0]} .. {v[1]}" if isinstance(v, tuple) and len(v) == 2 \
                else str(v)
            print(f"{p['name']:<28}{v[:23]:<24}"
                  f"{','.join(p['sources']):<14}{len(p['sources'])}")
        print("\nn is HOW MANY SOURCES SAY IT, not how true it is. Four retail")
        print("articles agreeing is four retail articles. CLAUDE.md rule 4 —")
        print("evidence is the published record or alpha/book.py, never a blog.")

    print(f"\nWHERE THE SOURCES DISAGREE ({len(CONFLICTS)}):")
    for c in CONFLICTS:
        print(f"\n  {c['id']} — {c['topic']}")
        print(f"    {c['detail']}")
        print(f"    bearing here: {c['bearing']}")


def _print_capital(capital, iv, dte, risk_pct):
    print(f"CAPITAL FEASIBILITY — ${capital:,.0f} available, "
          f"IV {iv:.0%}, {dte:g} DTE, risk cap {risk_pct:.0%}\n")
    print(f"{'sym':<5}{'structure':<28}{'collateral':>13}{'max loss':>13}"
          f"{'1-2% acct':>13}  fits")
    print("-" * 78)
    for r in feasibility(capital, iv, dte, risk_pct):
        if r.get("impossible"):
            print(f"{r['symbol']:<5}{r['structure']:<28}"
                  f"  IMPOSSIBLE — cash settled, nothing to assign")
            continue
        ma = f"${r['min_account']:,.0f}" if r["min_account"] else "n/a"
        print(f"{r['symbol']:<5}{r['structure']:<28}"
              f"{'$' + format(r['collateral'], ',.0f'):>13}"
              f"{'$' + format(r['max_loss'], ',.0f'):>13}{ma:>13}"
              f"  {'yes' if r['ok'] else 'NO'}")
    print("\n  collateral = buying power held. max loss = worst case.")
    print("  1-2% acct  = account the corpus' own sizing rule implies; n/a for")
    print("               the wheel, where that rule does not transfer (OC-3).")
    print("  Premiums are atm_premium_estimate() — SIZING ONLY, not a price.")
    print("\n  The wheel needs 100 shares. There is no fractional contract:")
    for sym in ("SPY", "QQQ"):
        print(f"    {sym}: ${wheel_collateral(sym):,.0f} per cycle")
    print("    SPX / XSP / NDX: not available at any account size.")


def _print_strikes(spot, iv, dte, step):
    print(f"DELTA-TARGETED STRIKES — spot {spot:.2f}, IV {iv:.0%}, {dte:g} DTE\n")
    print(f"{'delta':>7}{'call K':>10}{'put K':>10}   from")
    for d, why in ((0.10, "TA iron condor legs / TB strangle"),
                   (0.15, "MX+TB short strikes"),
                   (0.30, "TP directional day trade"),
                   (0.50, "ATM")):
        c = strike_for_delta(spot, iv, dte, d, "call", step=step)
        p = strike_for_delta(spot, iv, dte, d, "put", step=step)
        print(f"{d:>7.2f}{c:>10.2f}{p:>10.2f}   {why}")
    print("\nMoneyness arithmetic, no signal — safe to feed the accumulator.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conflicts", action="store_true")
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--strikes", action="store_true")
    ap.add_argument("--spot", type=float, default=INSTRUMENTS["SPY"]["level"])
    ap.add_argument("--iv", type=float, default=0.16)
    ap.add_argument("--dte", type=float, default=1.0)
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--risk-pct", type=float, default=0.02)
    args = ap.parse_args()

    if args.strikes:
        _print_strikes(args.spot, args.iv, args.dte, args.step)
    elif args.capital is not None:
        _print_capital(args.capital, args.iv, args.dte, args.risk_pct)
    else:
        _print_params(only_conflicts=args.conflicts)


if __name__ == "__main__":
    main()
