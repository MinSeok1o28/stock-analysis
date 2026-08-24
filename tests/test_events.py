"""이벤트 감지·반응 통계 검증. 네트워크 없이 돈다.

실제 발생한 측정 오류를 고정한다: 마감 후 발표인데 제출일 당일 수익률을 재면
발표 *전날* 을 재는 셈이라 반응 크기가 실제의 1/3 로 나온다 (NVDA ±1.4% vs ±3.2%).
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date

from src.core.events import (Bars, Event, ReactionStat, detect, reaction_stats,
                             scenarios)


def bars(closes, volumes=None):
    volumes = volumes or [1000] * len(closes)
    return Bars([{"date": f"2026-01-{i+1:02d}", "open": c, "high": c * 1.01,
                  "low": c * 0.99, "close": c, "volume": v}
                 for i, (c, v) in enumerate(zip(closes, volumes))])


@dataclass(frozen=True)
class FakeEvent:
    filed_on: date
    after_close: bool


class TestBars(unittest.TestCase):
    def test_change_uses_previous_close(self) -> None:
        self.assertAlmostEqual(bars([100, 110]).change(), 0.10)

    def test_volume_ratio_against_20day_mean(self) -> None:
        b = bars([100] * 21, [100] * 20 + [300])
        self.assertAlmostEqual(b.volume_ratio(), 3.0)

    def test_volume_ratio_none_when_short(self) -> None:
        self.assertIsNone(bars([100, 101]).volume_ratio())

    def test_high_low_window(self) -> None:
        hi, lo = bars([100, 150, 80, 120]).high_low()
        self.assertAlmostEqual(hi, 150 * 1.01)
        self.assertAlmostEqual(lo, 80 * 0.99)


class TestReactionTiming(unittest.TestCase):
    """마감 후 발표는 다음 거래일이 반응일이다."""

    def setUp(self) -> None:
        # 1/01~1/05, 1/03 에 8-K. 1/03 종가는 전일 대비 +1%, 1/04 는 +10%.
        self.b = bars([100, 100, 101, 111.1, 111])

    def test_after_close_uses_next_session(self) -> None:
        st = reaction_stats(self.b, [FakeEvent(date(2026, 1, 3), True)])
        self.assertEqual(st.n, 1)
        self.assertAlmostEqual(st.moves[0][2], 0.10, places=6)
        self.assertEqual(st.moves[0][1], "2026-01-04")

    def test_intraday_uses_same_session(self) -> None:
        st = reaction_stats(self.b, [FakeEvent(date(2026, 1, 3), False)])
        self.assertAlmostEqual(st.moves[0][2], 0.01, places=6)
        self.assertEqual(st.moves[0][1], "2026-01-03")

    def test_weekend_filing_rolls_to_next_session(self) -> None:
        st = reaction_stats(self.b, [FakeEvent(date(2026, 1, 2), True)])
        self.assertEqual(st.moves[0][1], "2026-01-03")

    def test_event_outside_range_skipped(self) -> None:
        self.assertEqual(reaction_stats(self.b, [FakeEvent(date(2025, 1, 1), True)]).n, 0)


class TestReactionStat(unittest.TestCase):
    ST = ReactionStat([("d", "r", 0.05, 1.0), ("d", "r", -0.03, 1.0),
                       ("d", "r", -0.09, 1.0), ("d", "r", 0.01, 1.0)])

    def test_median_abs(self) -> None:
        self.assertAlmostEqual(self.ST.median_abs, 0.04)

    def test_max_and_direction(self) -> None:
        self.assertAlmostEqual(self.ST.max_abs, 0.09)
        self.assertEqual(self.ST.up_count, 2)

    def test_empty_is_safe(self) -> None:
        e = ReactionStat([])
        self.assertIsNone(e.median_abs)
        self.assertIn("표본 없음", e.summary())


class TestScenarios(unittest.TestCase):
    def test_symmetric_around_price(self) -> None:
        sc = scenarios(100.0, TestReactionStat.ST)
        moves = [s.move for s in sc]
        self.assertEqual(moves, sorted(moves, reverse=True))
        self.assertAlmostEqual(sc[2].price, 100.0)

    def test_no_scenarios_without_samples(self) -> None:
        self.assertEqual(scenarios(100.0, ReactionStat([])), [])

    def test_basis_is_recorded(self) -> None:
        self.assertTrue(all(s.basis for s in scenarios(100.0, TestReactionStat.ST)))


class TestDetect(unittest.TestCase):
    def test_earnings_soon_marks_confirmed(self) -> None:
        ev = detect(bars([100] * 30), days_to_earnings=2, earnings_confirmed=True)
        tag = next(e for e in ev if e.tag == "실적임박")
        self.assertIn("확정", tag.detail)
        self.assertEqual(tag.severity, 3)

    def test_estimated_earnings_labeled(self) -> None:
        ev = detect(bars([100] * 30), days_to_earnings=5, earnings_confirmed=False)
        self.assertIn("추정", next(e for e in ev if e.tag == "실적임박").detail)

    def test_volume_spike(self) -> None:
        b = bars([100] * 25, [100] * 24 + [500])
        self.assertTrue(any(e.tag == "거래량이상" for e in detect(b)))

    def test_big_move(self) -> None:
        b = bars([100] * 24 + [110])
        self.assertTrue(any(e.tag == "급변" for e in detect(b)))

    def test_near_52w_high(self) -> None:
        b = bars(list(range(80, 130)))
        self.assertTrue(any(e.tag == "52주고점권" for e in detect(b)))

    def test_near_52w_low(self) -> None:
        b = bars(list(range(130, 80, -1)))
        self.assertTrue(any(e.tag == "52주저점권" for e in detect(b)))

    def test_quiet_stock_has_no_events(self) -> None:
        self.assertEqual(detect(bars([100] * 30)), [])

    def test_valuation_gap(self) -> None:
        ev = detect(bars([100] * 30), valuation_gap=0.09)
        self.assertTrue(any(e.tag == "밸류갭" for e in ev))


class TestScore(unittest.TestCase):
    def test_severity_sum_orders_candidates(self) -> None:
        from src.core.events import Candidate
        a = Candidate("A", "A", 1.0, None, [Event("실적임박", "", 3), Event("급변", "", 2)])
        b = Candidate("B", "B", 1.0, None, [Event("시장상위", "", 1)])
        self.assertGreater(a.score, b.score)


if __name__ == "__main__":
    unittest.main(verbosity=2)
