"""
xvenue_relations.py — what two event contracts mean to each other.

The first version of `xvenue.py` assumed every matched pair was EQUIVALENT and
priced a hold-to-resolution hedge. That was wrong twice over.

**Wrong once:** most pairs are not equivalent. Kalshi asks "will X *run* for the
nomination", Polymarket asks "will X *win* it". The scanner read the price gap
between them as a 24 c edge. It is not an edge; it is the market correctly
pricing the gap between running and winning.

**Wrong twice, and this is the useful half:** the non-equivalent pairs are not
garbage. When one contract logically implies the other, the pair admits an
arbitrage whose worst case is bounded *by the logic*, not by the two venues
agreeing on how to resolve. That is a much weaker requirement than identical
rules, and it is checkable. Those pairs were being thrown away.

The relations
-------------
Write B => A for "B implies A" (B is narrower). For a pair (K, P):

  EQUIVALENT   K <=> P.  Buy YES one, NO the other. Min payoff 1 ONLY IF the
               two venues resolve identically. This is the fragile relation --
               the one where breakeven-divergence q* applies.

  K_BROADER    P => K.  Then p <= k must hold. If p_bid > k_ask the bound is
               violated: buy YES K + buy NO P.
                   P=1 (so K=1): 1 + 0 = 1
                   P=0, K=1    : 1 + 1 = 2
                   P=0, K=0    : 0 + 1 = 1
               Min payoff 1 BY LOGIC. Profit >= p_bid - k_ask.

  K_NARROWER   K => P. Mirror of the above.

  DISJOINT     K and P cannot both be true. Then k + p <= 1. If k_bid + p_bid > 1:
               buy NO K + buy NO P. Min payoff 1 by logic.
               Profit >= k_bid + p_bid - 1.

  UNKNOWN      Anything the classifier cannot justify. Priced as no trade.
               This is the default and it must stay the default.

Why the bounded relations survive rules differences
---------------------------------------------------
An EQUIVALENT hedge needs Kalshi and Polymarket to answer the same question the
same way. A K_BROADER hedge needs only that *Polymarket resolving YES implies
Kalshi resolves YES* -- under each venue's own rules, whatever they are. Nobody
winning a nomination on Polymarket's reading fails to have run for it on
Kalshi's. That implication tolerates disagreement about sources, cutoffs and
timezones in a way an equivalence never can.

It is not free. The implication itself must hold under both rule sets, and the
classifier can only propose it from text. `--audit` still exists and is still
the step that decides.

What the classifier does
------------------------
Parse each side into (claim_type, party, year, office) and compare on a lattice:

    RUN (broadest)  >  ADVANCE  >  NOMINEE  >  WIN_ELECTION (narrowest)

with VP_NOMINEE disjoint from NOMINEE/WIN_ELECTION of the same party and year,
and ENDORSE / TICKET always UNKNOWN because neither implies the other.

The lattice only applies when party and year agree on both sides. Where a side
names no party -- "win the 2028 US Presidential Election" -- the implication to
a *party* nomination is not guaranteed (a winner could run as an independent),
so the pair is downgraded to UNKNOWN rather than quietly assumed.
"""
from __future__ import annotations

import re

EQUIVALENT = "EQUIVALENT"
K_BROADER = "K_BROADER"      # P => K
K_NARROWER = "K_NARROWER"    # K => P
DISJOINT = "DISJOINT"
UNKNOWN = "UNKNOWN"

#: Broader claims sit higher. Comparable only within the same party and year.
CLAIM_RANK = {"RUN": 4, "ADVANCE": 3, "NOMINEE": 2, "WIN_ELECTION": 1}

#: Claim types that carry no implication either way. Always UNKNOWN.
OPAQUE = {"ENDORSE", "TICKET", "OTHER"}

_RE = [
    # Order matters. Disqualifiers first, then the most specific claim.
    ("ENDORSE",      re.compile(r"\bendors", re.I)),
    ("TICKET",       re.compile(r"\bticket\b", re.I)),
    ("VP_NOMINEE",   re.compile(r"\b(vice[\s-]?presid\w*|vp)\b", re.I)),
    ("RUN",          re.compile(r"\brun(ning)? for\b|\bbe a candidate\b|"
                                r"\bdeclare\w* (a )?candidacy\b|\benter the race\b|"
                                r"\bfile\w* to run\b", re.I)),
    ("ADVANCE",      re.compile(r"\badvanc\w*\b|\brun[\s-]?off\b|\bsecond round\b", re.I)),
    # "win the ... election" but NOT "win the ... nomination"
    ("WIN_ELECTION", re.compile(r"(\belection winner\b|\bwin\w*\s+the\b[^?]*\belection\b|"
                                r"\bbe elected\b|\bnext president\b)", re.I)),
    ("NOMINEE",      re.compile(r"\bnomin(ee|ation|ated|ate)\b", re.I)),
    ("OFFICE",       re.compile(r"\b(next|succeed\w*)\b[^?]*\b(prime minister|president|"
                                r"pope|chair|secretary|governor|mayor|ceo)\b", re.I)),
]

#: The office being contested. Two nomination claims for DIFFERENT offices are
#: not the same contract, however identical their wording -- "Fetterman is the
#: Democratic nominee for the Senate in Pennsylvania" and "Fetterman wins the
#: Democratic presidential nomination" are both NOMINEE/DEM/2028 and share no
#: outcome at all. Found by --audit on live data, 2026-08-21.
_OFFICE = [
    ("VICE_PRESIDENCY", re.compile(r"\b(vice[\s-]?presid\w*|vp)\b", re.I)),
    ("SENATE",          re.compile(r"\bsenate\b|\bsenator\b", re.I)),
    ("HOUSE",           re.compile(r"\bhouse of representatives\b|\bcongression\w*\b", re.I)),
    ("GOVERNOR",        re.compile(r"\bgovernor\b|\bgubernatorial\b", re.I)),
    ("MAYOR",           re.compile(r"\bmayor\b", re.I)),
    ("PRIME_MINISTER",  re.compile(r"\bprime minister\b", re.I)),
    ("POPE",            re.compile(r"\bpope\b|\bpapal\b", re.I)),
    ("PRESIDENCY",      re.compile(r"\bpresiden\w*\b", re.I)),
]

#: A claim whose subject is a SET ("a Trump family member", "any Democrat")
#: strictly contains a claim about one member of that set. Same trap as run
#: versus win, in a different costume, and it produced the largest apparent
#: edge in the first relation-aware scan.
_SET_SUBJECT = re.compile(
    r"\b(a|any|anyone|someone)\s+(\w+\s+){0,3}(family member|member|candidate|"
    r"democrat|republican|woman|man|person)\b|\bfamily member\b|"
    r"\ba member of\b|\bany other\b", re.I)

_PARTY = [("REP", re.compile(r"\brepublican\b|\bgop\b", re.I)),
          ("DEM", re.compile(r"\bdemocrat", re.I))]

_YEAR = re.compile(r"\b(20\d{2})\b")


def parse_claim(text):
    """Text -> {claim, party, year}. `claim` is UNKNOWN-safe: OTHER when nothing
    matches, never a guess."""
    t = text or ""
    claim = "OTHER"
    for name, rx in _RE:
        if rx.search(t):
            # A nomination question mentioning "win" is still about the
            # nomination, not the general election. WIN_ELECTION only wins the
            # tie-break when no nomination language is present.
            if name == "WIN_ELECTION" and re.search(r"\bnomin", t, re.I):
                continue
            claim = name
            break
    party = next((p for p, rx in _PARTY if rx.search(t)), None)
    office = next((o for o, rx in _OFFICE if rx.search(t)), None)
    years = _YEAR.findall(t)
    return {"claim": claim, "party": party, "office": office,
            "subject": "set" if _SET_SUBJECT.search(t) else "individual",
            "year": years[0] if years else None, "text": t}


def classify(kalshi_text, poly_text):
    """(relation, confidence, reason) for one candidate pair.

    Defaults to UNKNOWN. Every non-UNKNOWN answer carries the reason that
    produced it so a human reviewing `--classify` output can reject it fast.
    """
    k, p = parse_claim(kalshi_text), parse_claim(poly_text)
    kc, pc = k["claim"], p["claim"]

    if kc in OPAQUE or pc in OPAQUE:
        return UNKNOWN, 0.0, "opaque claim type ({} vs {})".format(kc, pc)

    if k["year"] and p["year"] and k["year"] != p["year"]:
        return UNKNOWN, 0.0, "different years ({} vs {})".format(k["year"], p["year"])

    # Different offices are different contracts no matter how alike the wording.
    if k["office"] and p["office"] and k["office"] != p["office"]:
        # VP versus P of the same party and year is the one exclusive pair we
        # can price; everything else is simply a different race.
        if {k["office"], p["office"]} == {"VICE_PRESIDENCY", "PRESIDENCY"} and \
           k["party"] == p["party"] and k["party"] is not None:
            return DISJOINT, 0.7, ("VP and presidential nomination of the same "
                                   "party and year are mutually exclusive")
        return UNKNOWN, 0.0, "different offices ({} vs {})".format(
            k["office"], p["office"])

    # An office race with no party or nomination language on either side.
    if kc == "OFFICE" and pc == "OFFICE":
        return EQUIVALENT, 0.5, "both name the next holder of the same office"
    if "OFFICE" in (kc, pc):
        return UNKNOWN, 0.0, "one side is an office-succession claim ({} vs {})".format(kc, pc)

    # Vice-presidential vs presidential, same party and year, cannot both hold.
    if (kc == "VP_NOMINEE") != (pc == "VP_NOMINEE"):
        other = pc if kc == "VP_NOMINEE" else kc
        if other in ("NOMINEE", "WIN_ELECTION"):
            if k["party"] and p["party"] and k["party"] != p["party"]:
                return UNKNOWN, 0.0, "VP/P across different parties"
            return DISJOINT, 0.7, "VP nominee and presidential nominee of the " \
                                  "same party and year are mutually exclusive"
        return UNKNOWN, 0.0, "VP claim against {}".format(other)
    if kc == "VP_NOMINEE" and pc == "VP_NOMINEE":
        return EQUIVALENT, 0.6, "both are VP nomination claims"

    if kc not in CLAIM_RANK or pc not in CLAIM_RANK:
        return UNKNOWN, 0.0, "unranked claim ({} vs {})".format(kc, pc)

    # The lattice needs the party fixed on both sides. "Win the 2028 US
    # Presidential Election" names no party, so it does not imply winning any
    # particular party's nomination -- a winner could run as an independent.
    if k["party"] != p["party"]:
        return UNKNOWN, 0.0, ("party not fixed on both sides ({} vs {}); the "
                              "implication does not hold for an independent"
                              .format(k["party"], p["party"]))

    # A set-subject claim contains an individual-subject claim of the same type.
    if k["subject"] != p["subject"]:
        if k["subject"] == "set":
            return K_BROADER, 0.7, ("Kalshi names a set of people, Polymarket one "
                                    "of them: the individual claim implies the set")
        return K_NARROWER, 0.7, ("Polymarket names a set of people, Kalshi one of "
                                 "them: the individual claim implies the set")

    kr, pr = CLAIM_RANK[kc], CLAIM_RANK[pc]
    if kr == pr:
        return EQUIVALENT, 0.6, "same claim type ({}) same party and year".format(kc)
    if kr > pr:
        return K_BROADER, 0.75, "{} implies {}: Polymarket is the narrower leg".format(pc, kc)
    return K_NARROWER, 0.75, "{} implies {}: Kalshi is the narrower leg".format(kc, pc)

