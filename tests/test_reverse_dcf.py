"""역DCF 검증. 알려진 값으로 왕복(round-trip) 검사."""

from __future__ import annotations

import unittest

from src.core.valuation.outliers import detect, normalized_base
from src.core.valuation.reverse_dcf import (ConvergenceError, cagr, gap_summary,
                                            implied_growth, present_value,
                                            wacc_sensitivity)


class TestPresentValue(unittest.TestCase):
    def test_zero_growth_perpetuity_identity(self) -> None:
        """성장 0, 영구성장 0이면 PV = FCF/WACC (영구연금 공식)."""
        pv = present_value(100.0, 0.0, 0.10, 0.0, 200)
        self.assertAlmostEqual(pv, 100.0 / 0.10, delta=1.0)

    def test_monotonic_in_growth(self) -> None:
        vals = [present_value(100, g, 0.09, 0.02, 10) for g in (0.0, 0.05, 0.10, 0.15)]
        self.assertEqual(vals, sorted(vals))

    def test_wacc_must_exceed_terminal(self) -> None:
        with self.assertRaises(ValueError):
            present_value(100.0, 0.05, 0.02, 0.03, 10)


class TestImpliedGrowth(unittest.TestCase):
    def test_round_trip(self) -> None:
        """정방향으로 만든 가치를 역산하면 원래 성장률이 나와야 한다."""
        for g in (-0.05, 0.0, 0.07, 0.20):
            target = present_value(100.0, g, 0.09, 0.025, 10)
            r = implied_growth(target, 100.0, 0.09, terminal_growth=0.025, years=10)
            self.assertTrue(r.converged)
            self.assertAlmostEqual(r.value, g, places=4, msg=f"g={g}")

    def test_higher_price_requires_higher_growth(self) -> None:
        base = present_value(100.0, 0.05, 0.09, 0.025, 10)
        lo = implied_growth(base, 100.0, 0.09).value
        hi = implied_growth(base * 1.5, 100.0, 0.09).value
        self.assertGreater(hi, lo)

    def test_higher_wacc_requires_higher_growth(self) -> None:
        target = present_value(100.0, 0.05, 0.09, 0.025, 10)
        a = implied_growth(target, 100.0, 0.08).value
        b = implied_growth(target, 100.0, 0.10).value
        self.assertGreater(b, a)

    def test_negative_fcf_refused(self) -> None:
        """FCF가 음수면 역DCF는 성립하지 않는다. 조용히 숫자를 만들지 않는다."""
        with self.assertRaises(ValueError):
            implied_growth(1000.0, -50.0, 0.09)

    def test_unreachable_price_raises(self) -> None:
        with self.assertRaises(ConvergenceError):
            implied_growth(1e12, 100.0, 0.09, hi=0.30)

    def test_sensitivity_shape(self) -> None:
        target = present_value(100.0, 0.06, 0.09, 0.025, 10)
        rows = wacc_sensitivity(target, 100.0)
        self.assertEqual(len(rows), 5)
        vals = [r.implied for r in rows if r.implied is not None]
        self.assertEqual(vals, sorted(vals))


class TestHistorical(unittest.TestCase):
    def test_cagr(self) -> None:
        self.assertAlmostEqual(cagr(100.0, 200.0, 4), 2 ** 0.25 - 1, places=9)

    def test_cagr_refuses_nonpositive(self) -> None:
        """음수·0은 계산 불가를 None으로 명시한다. 억지로 값을 만들지 않는다."""
        self.assertIsNone(cagr(-10.0, 200.0, 4))
        self.assertIsNone(cagr(100.0, 0.0, 4))

    def test_gap_summary_has_no_verdict(self) -> None:
        text = gap_summary(0.14, 0.09)
        for banned in ("매수", "매도", "저평가", "고평가", "사라", "팔아"):
            self.assertNotIn(banned, text)


class TestOutliers(unittest.TestCase):
    def test_detects_spike(self) -> None:
        s = [("FY1", 100.0), ("FY2", 100.0), ("FY3", 100.0), ("FY4", 150.0)]
        found = detect(s)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].deviation, 0.50, places=6)

    def test_ignores_within_threshold(self) -> None:
        s = [("FY1", 100.0), ("FY2", 100.0), ("FY3", 100.0), ("FY4", 130.0)]
        self.assertEqual(detect(s), [])

    def test_normalized_base_is_mean_of_window(self) -> None:
        s = [("FY1", 90.0), ("FY2", 100.0), ("FY3", 110.0)]
        self.assertAlmostEqual(normalized_base(s), 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
