"""News parsing, query building, the subject gate and the four boxes. No network."""
import unittest

from rhengine.news import (
    _when_days, classify_divergence, extract_names, on_subject, parse_rss,
    query_for_contract,
)

RSS = """<rss><channel>
<item><title>Nikki Haley draws a crowd</title>
 <pubDate>Fri, 21 Aug 2026 12:27:00 GMT</pubDate>
 <link>http://a</link><source url="x">Independent Mail</source></item>
<item><title>Russian drones hit Moldova crossing</title>
 <pubDate>Fri, 21 Aug 2026 10:07:00 GMT</pubDate>
 <link>http://b</link><source url="y">Reuters</source></item>
<item><title>Broken item with no date</title><link>http://c</link></item>
</channel></rss>"""


class TestParseRss(unittest.TestCase):
    def test_parses_and_skips_undated(self):
        items = parse_rss(RSS)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["source"], "Independent Mail")
        self.assertTrue(items[0]["ts"].startswith("2026-08-21T12:27"))

    def test_empty_input(self):
        self.assertEqual(parse_rss(""), [])
        self.assertEqual(parse_rss(None), [])


class TestNames(unittest.TestCase):
    def test_particles_stay_inside_the_name(self):
        # Without this the query becomes generic NATO coverage and the spike
        # detector reports Ukraine headlines as von der Leyen news.
        self.assertIn("Ursula von der Leyen", extract_names(
            "Who will be the next Secretary General of NATO? Ursula von der Leyen"))

    def test_hyphens_and_apostrophes(self):
        self.assertEqual(extract_names("2028 nominee Alexandria Ocasio-Cortez"),
                         ["Alexandria Ocasio-Cortez"])
        self.assertEqual(extract_names("Will Beto O'Rourke win?"), ["Beto O'Rourke"])

    def test_question_scaffolding_is_not_a_first_name(self):
        self.assertNotIn("Will Nikki", extract_names("Will Nikki Haley win?"))
        self.assertEqual(extract_names("Will Nikki Haley win?"), ["Nikki Haley"])

    def test_query_quotes_the_name(self):
        q = query_for_contract("Will Nikki Haley win the 2028 Republican nomination?")
        self.assertIn('"Nikki Haley"', q)


class TestSubjectGate(unittest.TestCase):
    def test_keeps_only_on_subject_headlines(self):
        items = parse_rss(RSS)
        keep, gated = on_subject(items, ["Nikki Haley"])
        self.assertTrue(gated)
        self.assertEqual(len(keep), 1)
        self.assertIn("Haley", keep[0]["title"])

    def test_no_subject_means_no_gate_and_says_so(self):
        items = parse_rss(RSS)
        keep, gated = on_subject(items, [])
        self.assertFalse(gated)
        self.assertEqual(len(keep), len(items))


class TestWhenDays(unittest.TestCase):
    def test_parses_hours_and_days(self):
        self.assertAlmostEqual(_when_days("6h"), 0.25, places=6)
        self.assertAlmostEqual(_when_days("7d"), 7.0, places=6)
        self.assertAlmostEqual(_when_days("garbage"), 1.0, places=6)


class TestBoxes(unittest.TestCase):
    HOT = {"ratio": 6.0, "breaking": False}
    COLD = {"ratio": 1.0, "breaking": False}
    MOVED = {"shift": 0.08}
    FLAT = {"shift": 0.001}

    def test_four_boxes(self):
        self.assertEqual(classify_divergence(self.HOT, self.MOVED)[0], "PRICED")
        self.assertEqual(classify_divergence(self.HOT, self.FLAT)[0], "UNPRICED")
        self.assertEqual(classify_divergence(self.COLD, self.MOVED)[0], "SILENT_MOVE")
        self.assertEqual(classify_divergence(self.COLD, self.FLAT)[0], "QUIET")

    def test_breaking_counts_as_hot(self):
        self.assertEqual(
            classify_divergence({"ratio": None, "breaking": True}, self.FLAT)[0],
            "UNPRICED")

    def test_missing_price_tape_is_not_a_move(self):
        self.assertEqual(classify_divergence(self.HOT, None)[0], "UNPRICED")

    def test_direction_of_move_does_not_matter(self):
        down = classify_divergence(self.HOT, {"shift": -0.08})[0]
        self.assertEqual(down, "PRICED")


if __name__ == "__main__":
    unittest.main()
