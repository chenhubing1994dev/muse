from __future__ import annotations

import unittest

from muse.yijing import HEXAGRAM_BY_BITS, TRIGRAM_BY_BITS, analyze_yijing_lines, lookup_hexagram, seeded_three_coin_cast


class YijingCastTests(unittest.TestCase):
    def test_seeded_three_coin_cast_is_reproducible_and_explicitly_symbolic(self) -> None:
        first = seeded_three_coin_cast("muse-demo-2026-11-10")
        second = seeded_three_coin_cast("muse-demo-2026-11-10")
        self.assertEqual(first, second)
        self.assertEqual(len(first["lines"]), 6)
        self.assertTrue(all(line["value"] in (6, 7, 8, 9) for line in first["lines"]))
        self.assertEqual(first["input_origin"], "seeded_simulation")
        self.assertFalse(first["is_physical_cast"])
        self.assertFalse(first["is_evidence"])
        self.assertFalse(first["is_prediction"])
        self.assertEqual(first["calculation_status"], "calculated")
        self.assertEqual(first["king_wen_catalog_status"], "secondary_structural_reference_loaded")
        self.assertEqual(first["king_wen_number"], lookup_hexagram(first["primary"]["bits_bottom_to_top"])["king_wen_number"])
        self.assertEqual(first["seed"], "muse-demo-2026-11-10")
        self.assertEqual(len(first["seed_sha256"]), 64)

    def test_line_values_generate_primary_relating_hexagrams_and_moving_lines(self) -> None:
        result = analyze_yijing_lines([6, 7, 8, 9, 6, 9])
        self.assertEqual(result["primary"]["bits_bottom_to_top"], "010101")
        self.assertEqual(result["relating"]["bits_bottom_to_top"], "110010")
        self.assertEqual(result["moving_lines"], [1, 4, 5, 6])
        self.assertEqual(result["primary"]["lower_trigram"], {"bits_bottom_to_top": "010", "name": "坎"})
        self.assertEqual(result["primary"]["upper_trigram"], {"bits_bottom_to_top": "101", "name": "离"})
        self.assertEqual(result["relating"]["lower_trigram"], {"bits_bottom_to_top": "110", "name": "兑"})
        self.assertEqual(result["relating"]["upper_trigram"], {"bits_bottom_to_top": "010", "name": "坎"})
        self.assertEqual(result["input_origin"], "user_supplied_line_values")
        self.assertEqual(result["king_wen_number"], 64)
        self.assertEqual(result["king_wen_name"], "未濟")
        self.assertEqual(result["primary"]["unicode_symbol"], "䷿")
        self.assertEqual(result["relating"]["king_wen_number"], 60)
        self.assertEqual(result["relating"]["king_wen_name"], "節")
        self.assertFalse(result["is_physical_cast"])

    def test_all_eight_trigram_binary_patterns_are_explicit(self) -> None:
        self.assertEqual(TRIGRAM_BY_BITS, {
            "111": "乾", "110": "兑", "101": "离", "100": "震",
            "011": "巽", "010": "坎", "001": "艮", "000": "坤",
        })

    def test_complete_king_wen_catalog_maps_all_64_unique_line_patterns(self) -> None:
        self.assertEqual(len(HEXAGRAM_BY_BITS), 64)
        self.assertEqual({item["king_wen_number"] for item in HEXAGRAM_BY_BITS.values()}, set(range(1, 65)))
        self.assertEqual(len({item["king_wen_name"] for item in HEXAGRAM_BY_BITS.values()}), 64)
        self.assertEqual(lookup_hexagram("111111")["king_wen_number"], 1)
        self.assertEqual(lookup_hexagram("111111")["king_wen_name"], "乾")
        self.assertEqual(lookup_hexagram("100010")["king_wen_number"], 3)
        self.assertEqual(lookup_hexagram("010101")["king_wen_number"], 64)
        self.assertEqual(lookup_hexagram("010101")["unicode_symbol"], "䷿")
        with self.assertRaises(ValueError):
            lookup_hexagram("123456")

    def test_nuclear_opposite_and_inverse_are_reproducible_structural_relations(self) -> None:
        result = analyze_yijing_lines([7, 7, 7, 8, 8, 8])
        relations = result["structural_relations"]
        self.assertEqual(relations["nuclear"]["bits_bottom_to_top"], "110100")
        self.assertEqual(relations["nuclear"]["king_wen_number"], 54)
        self.assertEqual(relations["opposite"]["bits_bottom_to_top"], "000111")
        self.assertEqual(relations["opposite"]["king_wen_number"], 12)
        self.assertEqual(relations["inverse"]["bits_bottom_to_top"], "000111")
        self.assertEqual(relations["inverse"]["king_wen_number"], 12)
        self.assertTrue(result["structural_relations_are_interpretations" ] is False)

    def test_invalid_seed_and_line_inputs_fail_closed(self) -> None:
        for seed in ("", " ", "x" * 257, None):
            with self.subTest(seed=repr(seed)):
                with self.assertRaises(ValueError):
                    seeded_three_coin_cast(seed)
        for lines in ([], [7] * 5, [7] * 7, [True, 7, 8, 9, 6, 7], [5, 7, 8, 9, 6, 7], "678967"):
            with self.subTest(lines=repr(lines)):
                with self.assertRaises(ValueError):
                    analyze_yijing_lines(lines)

    def test_user_supplied_lines_do_not_claim_a_seed_or_external_verdict(self) -> None:
        result = analyze_yijing_lines([7, 8, 7, 8, 7, 8])
        self.assertNotIn("seed", result)
        self.assertEqual(result["epistemic_status"], "symbolic_structure_only")
        self.assertEqual(result["interpretation_status"], "not_generated")
        self.assertTrue(result["source_refs"])
        self.assertEqual(result["rule_source_status"], "secondary_structural_reference_not_primary_textual_citation")


if __name__ == "__main__":
    unittest.main()
