"""trials.py — the append-only experiment ledger, and the price of having searched.

`alpha/stats.py` has had the Deflated Sharpe Ratio since the audit, and it takes
a `trials=` argument. Nothing ever counted the trials. The callers pass hand-set
constants — `trials=1` in `alpha/buywrite.py`, `trials=50` in `alpha/rmxd.py`,
`trials=60` in `alpha/rmxq.py` — and `docs/AUTOSTRAT-2026-07-31.md` §G1-9 records
the consequence in one line: *"not computed, and the trial count for this project
is not tracked."* The 2026-07-24 audit prescribed the fix (FC-14 remediation, §8
Phase 0: "Create append-only experiment ledger; backfill the ≥12 known trials")
and it was never built. This module is that ledger.

**Why now.** The 2026-08-14 video intake included a channel reporting that an
agent had backtested **25,000** variants of one chart methodology and published
the winners. That is not a strategy result; it is a draw from the maximum of
25,000 nulls, and this module computes what that draw is worth (`--expected-max
25000` → the best of 25,000 coin-flip strategies scores t ≈ 4.2 by luck alone).
The same arithmetic applies to this repo, which is also an agent that can try
hundreds of things. See `docs/VIDEO-INTAKE-2026-08-14.md`.

**What it does not do.** It does not detect trials. Nothing here can see an
experiment that was run and not written down, so the count is always a **lower
bound** and every threshold it prints is therefore a **floor**. The ledger is
honest only to the degree the person appending to it is.

**Families, and why the count is not global.** Bonferroni over every trial the
repo has ever run would charge the RMX-B legs for the legacy engine's ETF
exclusion, which shares no data, no universe and no null with it. Trials are
counted **within a family** (`legacy-engine`, `rmx`, `sleeves`, `options`, ...)
and thresholds are quoted per family. Cross-family aggregation is available
(`--family all`) and is the right number only when the question itself is
"did this repo, searching everywhere, find anything?"

Usage:

    py -m rhengine.trials report                    # counts + required t, per family
    py -m rhengine.trials report --family rmx
    py -m rhengine.trials list --family sleeves
    py -m rhengine.trials expected-max 25000        # what luck alone scores
    py -m rhengine.trials log --family sleeves --label "add short-interest block" \\
        --kind structural --outcome rejected --source docs/RMX-C.md
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict, field
from datetime import date

import numpy as np
from scipy import stats as sps

from ._paths import app_path

EULER = 0.5772156649015329

LEDGER_NAME = os.path.join("research_data", "trials.jsonl")

#: A trial is one *decision point* that could have gone the other way and was
#: resolved by looking at a number. Re-running the same config on the same
#: window is not a trial; changing the window because the first one disappointed
#: is.
KINDS = ("structural", "threshold", "universe", "window", "cost", "timing")
OUTCOMES = ("kept", "rejected", "retired", "inconclusive")


def ledger_path() -> str:
    """Absolute path to the ledger, whether or not it exists yet."""
    p = app_path(LEDGER_NAME)
    if os.path.exists(p):
        return p
    root = app_path("research_data")
    if os.path.isdir(root):
        return os.path.join(root, "trials.jsonl")
    return p


# ------------------------------------------------------------------ the record
@dataclass
class Trial:
    family: str
    label: str
    kind: str = "structural"
    outcome: str = "inconclusive"
    #: Where the trial is written down. A ledger entry with no source is an
    #: assertion; with one it is a citation. Backfilled rows all carry one.
    source: str = ""
    date: str = field(default_factory=lambda: date.today().isoformat())
    config_hash: str = ""
    note: str = ""
    #: Label of an EARLIER row whose outcome this row records. Rule 8 says log
    #: the trial before you run it, which means the outcome arrives later — and
    #: appending a second full row for it would count one trial twice. A row
    #: with `resolves` set contributes **0** to every count; it carries only the
    #: verdict. The ledger stays append-only, and the arithmetic stays honest.
    resolves: str = ""
    #: How many trials this row *represents*. One row per trial is the norm, but
    #: some documented searches were recorded only in aggregate — "8 price-only
    #: cross-sectional signals, best |t| = 0.89" is eight trials whose individual
    #: names were not written down. Counting that row as 1 would understate the
    #: burden by 7, so the count sums `n` rather than rows.
    n: int = 1

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {self.outcome!r}")
        if not self.family or not self.label:
            raise ValueError("family and label are required")
        if int(self.n) < 1:
            raise ValueError(f"n must be >= 1, got {self.n}")
        self.n = int(self.n)


def load(path: str | None = None) -> list[Trial]:
    path = path or ledger_path()
    if not os.path.exists(path):
        return []
    out: list[Trial] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(Trial(**json.loads(line)))
    return out


def append(trial: Trial, path: str | None = None) -> str:
    """Append one trial. The ledger is append-only by convention; nothing here
    rewrites or deletes, because a ledger you can edit down is not a ledger."""
    path = path or ledger_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(trial), sort_keys=True) + "\n")
    return path


def count(family: str | None = None, kind: str | None = None,
          path: str | None = None) -> int:
    """Trials logged, summing `Trial.n`. `family=None`/`"all"` counts everything.

    Rows with `resolves` set are outcome records for a trial already counted,
    so they add nothing — otherwise pre-logging a trial and then recording how
    it turned out would charge the search twice.
    """
    rows = [t for t in load(path) if not t.resolves]
    if family and family != "all":
        rows = [t for t in rows if t.family == family]
    if kind:
        rows = [t for t in rows if t.kind == kind]
    return sum(t.n for t in rows)


def families(path: str | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in load(path):
        if t.resolves:
            continue
        out[t.family] = out.get(t.family, 0) + t.n
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def outcomes(path: str | None = None) -> dict[str, str]:
    """label -> latest known outcome, following `resolves` rows."""
    out: dict[str, str] = {}
    for t in load(path):
        out[t.resolves or t.label] = t.outcome
    return out


# ----------------------------------------------------- what the search cost you
def expected_max_t(trials: int, rho: float = 0.0) -> float:
    """E[max] of `trials` standard normals — the t-stat luck alone delivers.

    The Bailey & Lopez de Prado (2014) approximation, the same one
    `alpha/stats.deflated_sharpe` uses to set its rejection threshold:

        E[max] ~ (1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N*e))

    One trial scores 0.00. Twelve score **1.66** — so the audit's FC-14 headline
    alpha t of 1.78 is barely above what luck alone delivers at 12 trials, and
    nowhere near the 2.86 bar it would have to clear. That is arithmetic, not
    opinion. Twenty-five thousand trials score **4.08**.

    **`rho` is the pairwise correlation between trials, and it matters a lot.**
    A grid sweep over one idea's parameters is not N independent tests. For
    equicorrelated normals the decomposition is exact —
    `X_i = sqrt(rho)*Z + sqrt(1-rho)*eps_i`, so the common factor Z has mean zero
    and drops out of the expectation:

        E[max | rho] = sqrt(1 - rho) * E[max | iid]

    At rho=0.9 a 143,000-variant sweep has the same expected best t as ~35
    independent tries. This cuts **both** ways: it deflates the "I tested
    143,000 strategies" genre, and it also means this repo's own correlated
    variants should not all be charged at full price. State the rho you assumed.
    """
    n = int(trials)
    if n <= 1:
        return 0.0
    r = float(rho)
    if not 0.0 <= r < 1.0:
        raise ValueError(f"rho must be in [0, 1), got {rho}")
    z1 = sps.norm.ppf(1.0 - 1.0 / n)
    z2 = sps.norm.ppf(1.0 - 1.0 / (n * np.e))
    return float(np.sqrt(1.0 - r) * ((1.0 - EULER) * z1 + EULER * z2))


def effective_trials(trials: int, rho: float) -> int:
    """How many INDEPENDENT tries a correlated sweep of `trials` is worth.

    The N' whose iid expected-max equals the correlated sweep's. Reported as an
    integer >= 1 because it is quoted, not chained into further arithmetic.

    Use it to answer "the sweep was 143,000 variants of one idea — what is the
    honest trial count?" and then feed *that* to `sidak_t`. A parameter grid over
    one signal is rho ~ 0.8-0.95; genuinely different signals on the same
    universe are more like rho ~ 0.2-0.4, because they still share one market
    history.

    **rho is an assumption, not a measurement, and it is the most abusable
    number in this module.** Raising it makes any sweep look cheap — at
    rho=0.95, 143,000 variants price like 4 independent tries. So the rule when
    reporting is: **quote the rho=0 figure alongside whatever rho you assumed**,
    and justify the assumption. If you can compute the trials' return
    correlations, do that instead of guessing (Lopez de Prado's own advice is to
    cluster the trial returns and count clusters).
    """
    n = int(trials)
    if n <= 1:
        return 1
    target = expected_max_t(n, rho)
    if target <= 0.0:
        return 1
    lo, hi = 1.0, float(n)
    for _ in range(200):                     # bisect on N', monotone in N'
        mid = np.sqrt(lo * hi)
        if expected_max_t(int(round(mid))) < target:
            lo = mid
        else:
            hi = mid
    return max(int(round(hi)), 1)


def sidak_t(trials: int, alpha: float = 0.05, two_sided: bool = True) -> float:
    """Two-sided t a single result must clear for family-wise error <= `alpha`.

    Sidak rather than Bonferroni: exact under independence instead of merely
    conservative, and the two agree to two decimals at these trial counts.
    Independence is optimistic — correlated variants of one idea are fewer
    effective trials than they look — so this is a floor, like everything here.
    """
    n = max(int(trials), 1)
    per = 1.0 - (1.0 - alpha) ** (1.0 / n)
    return float(sps.norm.ppf(1.0 - (per / 2.0 if two_sided else per)))


def bonferroni_t(trials: int, alpha: float = 0.05) -> float:
    """The conservative sibling of `sidak_t`, kept because the audit quotes it."""
    n = max(int(trials), 1)
    return float(sps.norm.ppf(1.0 - alpha / (2.0 * n)))


def family_wise_p(t: float, trials: int) -> float:
    """P(some null among `trials` reaches |t|) — 'how surprising, after N tries?'

    A t of 2.54 is p=0.011 on its own and p=0.12 after 12 tries. This is the
    number to quote next to any result the repo selected rather than
    pre-registered.
    """
    n = max(int(trials), 1)
    p1 = 2.0 * sps.norm.sf(abs(float(t)))
    return float(1.0 - (1.0 - p1) ** n)


def verdict(t: float, trials: int, alpha: float = 0.05, rho: float = 0.0) -> dict:
    """One dict with every number needed to state a result and its burden.

    With `rho > 0` the burden is computed at the EFFECTIVE trial count, and the
    dict reports both counts so the assumption stays visible in whatever quotes
    it. A verdict printed without its rho is not interpretable.
    """
    n_eff = effective_trials(trials, rho) if rho else max(int(trials), 1)
    req = sidak_t(n_eff, alpha)
    return {
        "t": float(t),
        "trials": int(trials),
        "rho": float(rho),
        "effective_trials": int(n_eff),
        "expected_max_t": expected_max_t(trials, rho),
        "required_t": req,
        "family_wise_p": family_wise_p(t, n_eff),
        "clears": bool(abs(float(t)) >= req),
    }


# ------------------------------------------------------------------------- CLI
def _print_report(family: str | None, alpha: float, path: str | None,
                  t: float | None, rho: float = 0.0) -> None:
    fams = families(path)
    total = sum(fams.values())
    if not total:
        print(f"ledger empty: {ledger_path()}")
        print("nothing has been logged, so every threshold below would be a lie.")
        return

    print(f"ledger: {path or ledger_path()}   ({total} trials logged)")
    print("counts are a LOWER BOUND — an unlogged trial is invisible here.\n")
    print("{:<18} {:>7} {:>12} {:>12}".format(
        "family", "trials", "E[max t]", f"required t@{alpha:g}"))
    rows = [(f, n) for f, n in fams.items()] + [("all", total)]
    for f, n in rows:
        if family and family != "all" and f not in (family, "all"):
            continue
        print("{:<18} {:>7d} {:>12.2f} {:>12.2f}".format(
            f, n, expected_max_t(n), sidak_t(n, alpha)))

    if t is not None:
        n = count(family or "all", path=path)
        v = verdict(t, n, alpha, rho)
        print(f"\nobserved t = {v['t']:+.2f} against {n} logged trials"
              f" in family {family or 'all'!r}:")
        if rho:
            print(f"  assumed trial correlation rho = {rho:.2f}"
                  f"  ->  {v['effective_trials']} effective independent trials")
        print(f"  luck alone delivers t = {v['expected_max_t']:.2f}")
        print(f"  required t          = {v['required_t']:.2f}")
        print(f"  family-wise p       = {v['family_wise_p']:.3f}")
        print(f"  {'CLEARS' if v['clears'] else 'DOES NOT CLEAR'} its own trial burden")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rhengine.trials", description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=None, help="override the ledger path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="counts and required t per family")
    r.add_argument("--family", default=None)
    r.add_argument("--alpha", type=float, default=0.05)
    r.add_argument("--t", type=float, default=None,
                   help="judge this observed t-stat against the logged count")
    r.add_argument("--rho", type=float, default=0.0,
                   help="assumed pairwise correlation between trials (0 = independent)")

    ls = sub.add_parser("list", help="print the ledger")
    ls.add_argument("--family", default=None)

    lg = sub.add_parser("log", help="append one trial")
    lg.add_argument("--family", required=True)
    lg.add_argument("--label", required=True)
    lg.add_argument("--kind", default="structural", choices=KINDS)
    lg.add_argument("--outcome", default="inconclusive", choices=OUTCOMES)
    lg.add_argument("--source", default="")
    lg.add_argument("--config-hash", default="")
    lg.add_argument("--note", default="")
    lg.add_argument("--date", default=None)
    lg.add_argument("-n", type=int, default=1,
                    help="how many trials this row represents (default 1)")
    lg.add_argument("--resolves", default="",
                    help="label of an earlier row this one records the OUTCOME "
                         "of; such a row counts as 0 trials, not a second one")

    em = sub.add_parser("expected-max", help="what the best of N nulls scores")
    em.add_argument("trials", type=int)
    em.add_argument("--alpha", type=float, default=0.05)
    em.add_argument("--rho", type=float, default=0.0,
                    help="pairwise correlation between the N variants; a grid "
                         "sweep over one idea is ~0.8-0.95, distinct signals ~0.2-0.4")

    a = ap.parse_args(argv)

    if a.cmd == "report":
        _print_report(a.family, a.alpha, a.ledger, a.t, a.rho)
    elif a.cmd == "list":
        for t in load(a.ledger):
            if a.family and t.family != a.family:
                continue
            tag = f"{t.label} (x{t.n})" if t.n > 1 else t.label
            if t.resolves:
                tag = f"-> outcome of: {t.resolves}"
            print("{:<11} {:<14} {:<13} {:<9} {}".format(
                t.date, t.family, t.kind, t.outcome, tag))
            if t.source:
                print(" " * 12 + f"source: {t.source}")
    elif a.cmd == "log":
        kw = {k: v for k, v in vars(a).items()
              if k not in ("cmd", "ledger") and v is not None}
        kw["config_hash"] = kw.pop("config_hash", "")
        p = append(Trial(**kw), a.ledger)
        print(f"logged -> {p}  ({count(a.family, path=a.ledger)} in family {a.family!r})")
    elif a.cmd == "expected-max":
        n, rho = a.trials, a.rho
        n_eff = effective_trials(n, rho) if rho else max(n, 1)
        print(f"trials              : {n}")
        if rho:
            print(f"assumed rho         : {rho:.2f}")
            print(f"effective trials    : {n_eff}")
        print(f"E[max t] under null : {expected_max_t(n, rho):.2f}")
        print(f"required t @ {a.alpha:g}    : {sidak_t(n_eff, a.alpha):.2f}")
        print(f"a t of 2.00 has family-wise p = {family_wise_p(2.0, n_eff):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
