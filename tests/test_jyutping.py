import unittest

from muse.jyutping import JyutpingError, parse_jyutping


class JyutpingParserTests(unittest.TestCase):
    def test_parses_common_cantonese_syllables_and_normalizes_case(self):
        syllables = parse_jyutping("Nei5 hou2, M4 goi1!")

        self.assertEqual(
            [(item.text, item.onset, item.rime, item.tone) for item in syllables],
            [
                ("nei5", "n", "ei", 5),
                ("hou2", "h", "ou", 2),
                ("m4", "", "m", 4),
                ("goi1", "g", "oi", 1),
            ],
        )

    def test_marks_checked_syllables_by_stop_coda(self):
        syllables = parse_jyutping("sik6 faat3 guk1")

        self.assertEqual([item.checked for item in syllables], [True, True, True])
        self.assertEqual([item.coda for item in syllables], ["k", "t", "k"])

    def test_accepts_ng_syllabic_nasal_and_longest_onsets(self):
        syllables = parse_jyutping("ng5 gwong2 kwai4")

        self.assertEqual(syllables[0].rime, "ng")
        self.assertEqual(syllables[1].onset, "gw")
        self.assertEqual(syllables[2].onset, "kw")

    def test_accepts_only_standard_jyutping_tones_one_through_six(self):
        for token in ("nei0", "nei7", "nei8", "nei9", "nei"):
            with self.subTest(token=token):
                with self.assertRaises(JyutpingError):
                    parse_jyutping(token)

    def test_rejects_unknown_rimes_and_non_jyutping_tokens(self):
        for text in ("nei5x", "foo5", "gwxyz2", "1234"):
            with self.subTest(text=text):
                with self.assertRaises(JyutpingError):
                    parse_jyutping(text)

    def test_requires_explicit_syllable_boundaries(self):
        with self.assertRaises(JyutpingError):
            parse_jyutping("nei5hou2")

    def test_rejects_empty_or_overlong_input(self):
        with self.assertRaises(JyutpingError):
            parse_jyutping("   ")
        with self.assertRaises(JyutpingError):
            parse_jyutping("nei5 " * 600)

    def test_rejects_unsupported_separators_instead_of_silently_dropping_text(self):
        with self.assertRaises(JyutpingError):
            parse_jyutping("nei5 @ hou2")


if __name__ == "__main__":
    unittest.main()
