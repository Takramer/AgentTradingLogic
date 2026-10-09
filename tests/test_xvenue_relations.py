"""Relation classification and relation-aware hedge pricing. No network."""
import unittest

from rhengine.xvenue import HEDGES, relation_edge, unwind
from rhengine.xvenue_relations import (
    DISJOINT, EQUIVALENT, K_BROADER, K_NARROWER, UNKNOWN, classify, parse_claim,
)

RUN_R = "Who will run for the 2028 Republican presidential nomination? Marco Rubio"
WIN_R = "Will Marco Rubio win the 2028 Republican presidential nomination?"


class TestParseClaim(unittest.TestCase):
    def test_nomination_beats_election_wording(self):
        # "win the ... nomination" is a NOMINEE claim, not WIN_ELECTION.
        self.assertEqual(parse_claim(WIN_R)["claim"], "NOMINEE")

    def test_run_beats_nomination_wording(self):
        self.assertEqual(parse_claim(RUN_R)["claim"], "RUN")

    def test_office_and_party_and_year(self):
        c = parse_claim(WIN_R)
        self.assertEqual((c["party"], c["year"], c["office"]),
                         ("REP", "2028", "PRESIDENCY"))

    def test_set_subject_detected(self):
        self.assertEqual(
            parse_claim("Will a Trump family member be the 2028 nominee?")["subject"],
            "set")
        self.assertEqual(parse_claim(WIN_R)["subject"], "individual")


class TestClassify(unittest.TestCase):
    def test_run_is_broader_than_win(self):
        rel, conf, _ = classify(RUN_R, WIN_R)
        self.assertEqual(rel, K_BROADER)
        self.assertGreater(conf, 0.5)

    def test_same_claim_is_equivalent(self):
        rel, _, _ = classify(
            "Will Elon Musk be the nominee for the Presidency for the Republican party in 2028?",
            "Will Elon Musk win the 2028 Republican presidential nomination?")
        self.assertEqual(rel, EQUIVALENT)

    def test_vp_versus_president_is_disjoint(self):
        rel, _, _ = classify("2028 Democratic VP nominee Cory Booker",
                             "Will Cory Booker win the 2028 Democratic presidential nomination?")
        self.assertEqual(rel, DISJOINT)

    def test_set_contains_individual(self):
        # The largest apparent edge of the first relation-aware scan.
        rel, _, _ = classify(
            "Will a Trump family member the 2028 Republican presidential nominee?",
            "Will Eric Trump win the 2028 Republican presidential nomination?")
        self.assertEqual(rel, K_BROADER)

    def test_different_office_is_not_equivalent(self):
        rel, _, why = classify(
            "Will John Fetterman be the Democratic nominee for the Senate in Pennsylvania in 2028?",
            "Will John Fetterman win the 2028 Democratic presidential nomination?")
        self.assertEqual(rel, UNKNOWN)
        self.assertIn("office", why)

    def test_unfixed_party_refuses_the_implication(self):
        # Winning the election does not imply winning a PARTY nomination --
        # the winner could run as an independent.
        rel, _, _ = classify("2028 Republican presidential nominee Eric Trump",
                             "Will Eric Trump win the 2028 US Presidential Election?")
        self.assertEqual(rel, UNKNOWN)

    def test_endorsement_implies_nothing(self):
        rel, _, _ = classify(
            "Who will Donald Trump endorse in the 2028 presidential election? Marco Rubio",
            WIN_R)
        self.assertEqual(rel, UNKNOWN)

    def test_different_years(self):
        rel, _, why = classify("Who will run for the 2024 Republican nomination? X",
                               "Will X win the 2028 Republican nomination?")
        self.assertEqual(rel, UNKNOWN)
        self.assertIn("year", why)


class TestHedgeTable(unittest.TestCase):
    def test_nested_relations_admit_exactly_one_direction(self):
        self.assertEqual(len(HEDGES[K_BROADER]), 1)
        self.assertEqual(len(HEDGES[K_NARROWER]), 1)
        self.assertEqual(len(HEDGES[EQUIVALENT]), 2)
        self.assertEqual(HEDGES[UNKNOWN], [])

    def test_broader_hedge_is_long_broad_short_narrow(self):
        # P => K, so the only bounded trade is YES on K and NO on P. The mirror
        # pays zero in the middle state and must never be offered.
        self.assertEqual(HEDGES[K_BROADER], [("yes", "no")])
        self.assertEqual(HEDGES[K_NARROWER], [("no", "yes")])
        self.assertEqual(HEDGES[DISJOINT], [("no", "no")])


class TestRelationEdge(unittest.TestCase):
    K = {"yes_ask": 0.40, "yes_bid": 0.38, "no_ask": 0.62, "no_bid": 0.60,
         "yes_ask_size": 500, "no_ask_size": 500, "close_time": None}
    P = {"yes_ask": 0.50, "yes_bid": 0.48, "no_ask": 0.52, "no_bid": 0.50,
         "yes_ask_size": 300, "no_ask_size": 300}

    def test_unknown_admits_no_trade(self):
        self.assertIsNone(relation_edge(self.K, self.P, UNKNOWN))

    def test_broader_violation_is_priced(self):
        # poly (narrow) bid 0.48 > kalshi (broad) ask 0.40 -> bound violated.
        row = relation_edge(self.K, self.P, K_BROADER, contracts=1000)
        self.assertEqual(row["direction"], "kalshi_yes/poly_no")
        self.assertGreater(row["net_edge"], 0)

    def test_logic_guarantee_has_no_q_star(self):
        # q* answers "how often may the venues resolve differently". A hedge
        # guaranteed by implication does not have that failure mode.
        row = relation_edge(self.K, self.P, K_BROADER, contracts=1000)
        self.assertEqual(row["guarantee"], "logic")
        self.assertIsNone(row["breakeven_divergence"])
        self.assertFalse(row["mismatch_suspect"])

    def test_equivalent_keeps_q_star(self):
        row = relation_edge(self.K, self.P, EQUIVALENT, contracts=1000)
        self.assertEqual(row["guarantee"], "rules")
        self.assertIsNotNone(row["breakeven_divergence"])
        self.assertAlmostEqual(row["breakeven_divergence"], row["net_edge"], places=4)

    def test_nested_and_disjoint_have_an_upside_state(self):
        self.assertEqual(relation_edge(self.K, self.P, EQUIVALENT)["max_payoff"], 1.0)
        self.assertEqual(relation_edge(self.K, self.P, K_BROADER)["max_payoff"], 2.0)
        self.assertEqual(relation_edge(self.K, self.P, DISJOINT)["max_payoff"], 2.0)

    def test_disjoint_needs_both_legs_expensive(self):
        cheap_k = dict(self.K, no_ask=0.99, yes_ask=0.01, yes_bid=0.005)
        cheap_p = dict(self.P, no_ask=0.99, yes_ask=0.01, yes_bid=0.005)
        self.assertLess(relation_edge(cheap_k, cheap_p, DISJOINT)["net_edge"], 0)


class TestUnwind(unittest.TestCase):
    def test_convergence_needed_covers_two_fees_and_both_spreads(self):
        k = {"yes_bid": 0.38, "yes_ask": 0.42, "close_time": None}
        p = {"yes_bid": 0.49, "yes_ask": 0.51}
        u = unwind(k, p, contracts=1000)
        self.assertAlmostEqual(u["gap"], 0.10, places=4)
        # 2 * 0.07 * 0.4 * 0.6 = 0.0336, plus 0.04 and 0.02 of spread.
        self.assertAlmostEqual(u["roundtrip_cost"], 0.0336 + 0.04 + 0.02, places=3)
        self.assertAlmostEqual(u["unwind_net_if_full_convergence"],
                               u["gap"] - u["roundtrip_cost"], places=6)

    def test_states_the_risk(self):
        u = unwind({"yes_bid": 0.38, "yes_ask": 0.42, "close_time": None},
                   {"yes_bid": 0.49, "yes_ask": 0.51})
        self.assertIn("unbounded", u["unwind_risk"])

    def test_one_sided_book(self):
        self.assertIsNone(unwind({"yes_bid": None, "yes_ask": 0.4}, {"yes_bid": 0.4,
                                                                     "yes_ask": 0.5}))


if __name__ == "__main__":
    unittest.main()
