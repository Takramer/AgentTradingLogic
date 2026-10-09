"""Order-book maintenance and request signing. No network."""
import unittest

from rhengine.kalshi_ws import CHANNELS, Book, _f

SNAP = {"market_ticker": "KXBTCD-X", "ts_ms": 1787381416667,
        "yes_dollars_fp": [["0.7900", "100.00"], ["0.8000", "3652.00"]],
        "no_dollars_fp": [["0.1900", "190.00"], ["0.1800", "40.00"]]}


class TestFloatCoercion(unittest.TestCase):
    def test_handles_strings_and_junk(self):
        self.assertEqual(_f("0.9200"), 0.92)
        self.assertIsNone(_f(None))
        self.assertIsNone(_f("abc"))


class TestBookSnapshot(unittest.TestCase):
    def test_reads_the_live_field_names(self):
        # The documented names are yes/no; the live feed sends
        # yes_dollars_fp/no_dollars_fp. Reading the wrong ones gives an
        # empty book that never errors.
        b = Book("KXBTCD-X")
        b.snapshot(SNAP, seq=1)
        self.assertEqual(b.yes[0.80], 3652.0)
        self.assertEqual(b.no[0.19], 190.0)
        self.assertEqual(b.seq, 1)

    def test_drops_zero_size_levels(self):
        b = Book("X")
        b.snapshot({"yes_dollars_fp": [["0.50", "0"]], "no_dollars_fp": []})
        self.assertEqual(b.yes, {})


class TestBookBest(unittest.TestCase):
    def test_converts_the_no_side_into_yes_terms(self):
        # Best NO bid 0.19 is a YES ask of 0.81. Comparing raw sides reads the
        # spread backwards.
        b = Book("X")
        b.snapshot(SNAP)
        best = b.best()
        self.assertAlmostEqual(best["yes_bid"], 0.80, places=6)
        self.assertAlmostEqual(best["yes_ask"], 0.81, places=6)
        self.assertAlmostEqual(best["spread"], 0.01, places=6)
        self.assertEqual(best["yes_bid_size"], 3652.0)
        self.assertEqual(best["yes_ask_size"], 190.0)

    def test_empty_book_is_none_not_zero(self):
        best = Book("X").best()
        self.assertIsNone(best["yes_bid"])
        self.assertIsNone(best["yes_ask"])
        self.assertIsNone(best["spread"])


class TestBookDeltas(unittest.TestCase):
    def _seeded(self):
        b = Book("X")
        b.snapshot(SNAP, seq=1)
        return b

    def test_delta_adds_to_a_level(self):
        b = self._seeded()
        self.assertTrue(b.apply({"price_dollars": "0.8000", "delta_fp": "50.00",
                                 "side": "yes"}, seq=2))
        self.assertEqual(b.yes[0.80], 3702.0)

    def test_negative_delta_removes_an_emptied_level(self):
        b = self._seeded()
        b.apply({"price_dollars": "0.7900", "delta_fp": "-100.00", "side": "yes"}, seq=2)
        self.assertNotIn(0.79, b.yes)

    def test_no_side_delta(self):
        b = self._seeded()
        b.apply({"price_dollars": "0.1900", "delta_fp": "-50.00", "side": "no"}, seq=2)
        self.assertEqual(b.no[0.19], 140.0)

    def test_sequence_gap_is_reported(self):
        # One dropped message leaves a book that is wrong and silent. apply()
        # must refuse rather than carry on.
        b = self._seeded()
        self.assertFalse(b.apply({"price_dollars": "0.8000", "delta_fp": "50.00",
                                  "side": "yes"}, seq=4))
        self.assertEqual(b.yes[0.80], 3652.0)   # unchanged

    def test_contiguous_sequence_advances(self):
        b = self._seeded()
        for s in (2, 3, 4):
            self.assertTrue(b.apply({"price_dollars": "0.8000", "delta_fp": "1.00",
                                     "side": "yes"}, seq=s))
        self.assertEqual(b.seq, 4)
        self.assertEqual(b.yes[0.80], 3655.0)

    def test_unknown_side_does_not_corrupt(self):
        b = self._seeded()
        self.assertTrue(b.apply({"price_dollars": "0.80", "delta_fp": "5",
                                 "side": "sideways"}, seq=2))
        self.assertEqual(b.yes[0.80], 3652.0)


class TestChannels(unittest.TestCase):
    def test_only_confirmed_channels_are_offered(self):
        # ticker_v2 and trade were rejected with "Unknown channel name".
        self.assertEqual(CHANNELS, ("orderbook_delta",))


class TestSigning(unittest.TestCase):
    def test_signature_verifies_against_the_public_key(self):
        from rhengine.market_data_kalshi import _kalshi_creds, kalshi_signed_headers
        key_id, priv = _kalshi_creds()
        if not priv:
            self.skipTest("Kalshi key not configured")
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        h = kalshi_signed_headers("GET", "/trade-api/ws/v2", key_id, priv)
        msg = (h["KALSHI-ACCESS-TIMESTAMP"] + "GET" + "/trade-api/ws/v2").encode()
        priv.public_key().verify(              # raises on mismatch
            base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=hashes.SHA256().digest_size),
            hashes.SHA256())
        self.assertEqual(h["KALSHI-ACCESS-KEY"], key_id)

    def test_missing_credentials_raise_not_return_garbage(self):
        # private_key=None deliberately falls back to secrets.json, so stub the
        # loader out to exercise the unconfigured path.
        import rhengine.market_data_kalshi as K
        real = K._kalshi_creds
        K._kalshi_creds = lambda: (None, None)
        try:
            with self.assertRaises(EnvironmentError):
                K.kalshi_signed_headers("GET", "/x")
        finally:
            K._kalshi_creds = real

    def test_signed_message_excludes_the_query_string(self):
        # Signing the query too yields a valid-looking signature Kalshi rejects.
        from rhengine.market_data_kalshi import _kalshi_creds, kalshi_signed_headers
        key_id, priv = _kalshi_creds()
        if not priv:
            self.skipTest("Kalshi key not configured")
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        path = "/trade-api/v2/markets"
        h = kalshi_signed_headers("GET", path, key_id, priv)
        bad = (h["KALSHI-ACCESS-TIMESTAMP"] + "GET" + path + "?limit=5").encode()
        with self.assertRaises(Exception):
            priv.public_key().verify(
                base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), bad,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=hashes.SHA256().digest_size),
                hashes.SHA256())


if __name__ == "__main__":
    unittest.main()
