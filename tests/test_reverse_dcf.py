"""역DCF 검증. 알려진 값으로 왕복(round-trip) 검사."""

from __future__ import annotations

import unittest

from src.core.valuation.outliers import detect, normalized_base
from src.core.valuation.reverse_dcf import (ConvergenceError, basis_comparison, cagr,
                                            enterprise_value, gap_summary, growth_axes,
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


class TestEnterpriseValue(unittest.TestCase):
    """FCF 는 채권자·주주 모두에게 귀속되므로 할인하면 기업가치가 나온다.
    시가총액만 쓰면 순부채 기업은 요구 성장률이 과소평가된다."""

    def test_net_debt_raises_required_growth(self) -> None:
        mc = 1_000.0
        base = implied_growth(mc, 50.0, 0.09).value
        levered = implied_growth(enterprise_value(mc, 200.0), 50.0, 0.09).value
        self.assertGreater(levered, base)

    def test_net_cash_lowers_required_growth(self) -> None:
        mc = 1_000.0
        base = implied_growth(mc, 50.0, 0.09).value
        cash_rich = implied_growth(enterprise_value(mc, -200.0), 50.0, 0.09).value
        self.assertLess(cash_rich, base)

    def test_zero_net_debt_is_identity(self) -> None:
        self.assertEqual(enterprise_value(1_000.0, 0.0), 1_000.0)


class TestBasisComparison(unittest.TestCase):
    """기준 FCF 를 최신으로 잡느냐 3년 평균으로 잡느냐가 결론을 가른다."""

    def test_lower_fcf_requires_higher_growth(self) -> None:
        rows = basis_comparison(10_000.0, latest_fcf=500.0, avg_fcf=300.0, wacc=0.09)
        latest, avg = rows[0], rows[1]
        self.assertEqual(latest.label, "최신 FCF")
        self.assertGreater(avg.implied, latest.implied)

    def test_both_rows_always_returned(self) -> None:
        rows = basis_comparison(1e12, latest_fcf=1.0, avg_fcf=1.0, wacc=0.09)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.implied is None and r.note for r in rows))


class TestGrowthAxes(unittest.TestCase):
    """구간에 따라 부호까지 바뀐다 — 하나만 보여주면 오해를 만든다."""

    REV = [(f"FY{y}", v) for y, v in
           zip(range(2015, 2026), [100, 110, 120, 135, 150, 160, 200, 240, 235, 240, 250])]
    FCF = [(f"FY{y}", v) for y, v in
           zip(range(2015, 2026), [20, 22, 25, 30, 33, 40, 55, 70, 60, 65, 62])]

    def test_multiple_spans_present(self) -> None:
        labels = {a.label for a in growth_axes(self.REV, self.FCF)}
        self.assertIn("매출 3년 CAGR", labels)
        self.assertIn("매출 5년 CAGR", labels)
        self.assertIn("매출 10년 CAGR", labels)
        self.assertIn("FCF 3년 CAGR", labels)

    def test_span_note_records_endpoints(self) -> None:
        a = next(x for x in growth_axes(self.REV, self.FCF) if x.label == "매출 3년 CAGR")
        self.assertEqual(a.note, "FY2022→FY2025")

    def test_short_series_skipped(self) -> None:
        self.assertEqual(growth_axes([("FY2024", 1.0)], []), [])

    def test_recent_span_can_differ_in_sign(self) -> None:
        axes = {a.label: a.value for a in growth_axes(self.REV, self.FCF)}
        self.assertLess(axes["FCF 3년 CAGR"], 0)      # 최근 3년은 역성장
        self.assertGreater(axes["FCF 10년 CAGR"], 0)  # 10년은 성장


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
