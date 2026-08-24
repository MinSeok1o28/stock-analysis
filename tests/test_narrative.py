"""문구 비교 검증. '변화 없으면 변화 없음'이 지켜지는지 포함."""

from __future__ import annotations

import unittest

from src.core.narrative.hedging import (hedge_delta, risk_terms_appeared,
                                        tone_downgrades)
from src.core.narrative.sections import split_us_items
from src.core.narrative.sentence_diff import compare, split_sentences


class TestSentenceDiff(unittest.TestCase):
    def test_identical_reports_no_change(self) -> None:
        t = "We will expand capacity. Demand remains strong across regions."
        d = compare(t, t)
        self.assertFalse(d.is_material)
        self.assertIn("변화 없음", d.summary())

    def test_detects_addition(self) -> None:
        a = "Demand remains strong across all of our regions."
        b = a + " We recorded an impairment on long-lived assets."
        self.assertEqual(len(compare(a, b).added), 1)

    def test_detects_removal(self) -> None:
        a = "Demand remains strong. We committed to a dividend increase this year."
        b = "Demand remains strong."
        self.assertEqual(len(compare(a, b).removed), 1)

    def test_near_identical_counts_as_modified(self) -> None:
        a = "Demand for our products remains strong across all regions worldwide."
        b = "Demand for our products remains stable across most regions worldwide."
        d = compare(a, b)
        self.assertEqual(len(d.modified), 1)
        self.assertEqual(d.added, [])

    def test_split_drops_fragments(self) -> None:
        self.assertEqual(split_sentences("Yes. No."), [])


class TestHedging(unittest.TestCase):
    def test_will_to_may(self) -> None:
        axes = [t.axis for t in tone_downgrades("We will grow.", "We may grow.")]
        self.assertIn("확약→가능성", axes)

    def test_strong_to_stable(self) -> None:
        axes = [t.axis for t in tone_downgrades("Growth is strong.", "Growth is stable.")]
        self.assertIn("강함→안정", axes)

    def test_no_false_positive(self) -> None:
        t = "Growth is strong and we will continue."
        self.assertEqual(tone_downgrades(t, t), [])

    def test_hedge_increase(self) -> None:
        got = dict((w, (a, b)) for w, a, b in hedge_delta("Revenue will rise.",
                                                          "Revenue may rise, subject to demand."))
        self.assertIn("may", got)
        self.assertIn("subject to", got)

    def test_risk_terms_new_only(self) -> None:
        self.assertEqual(risk_terms_appeared("all good", "we noted an impairment"), ["impairment"])
        self.assertEqual(risk_terms_appeared("impairment noted", "impairment noted again"), [])

    def test_korean_pairs(self) -> None:
        axes = [t.axis for t in tone_downgrades("강력한 성장을 기대합니다.", "안정적 성장을 유지합니다.")]
        self.assertIn("강력→안정", axes)


class TestSections(unittest.TestCase):
    def test_splits_items_and_keeps_longest(self) -> None:
        doc = ("Item 1A. Risk Factors\n" + "x" * 50 + "\n"
               "Item 7. MD&A\n" + "y" * 500 + "\n"
               "Item 1A. Risk Factors\n" + "z" * 800)
        out = split_us_items(doc)
        self.assertIn("1A", out)
        self.assertIn("7", out)
        self.assertGreater(len(out["1A"]), 700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
