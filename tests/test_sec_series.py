"""SEC companyfacts 연도 매핑 검증. 네트워크 없이 돈다.

실제로 발생한 버그를 고정한다:
  1. `fy` 는 그 값이 실린 *보고서*의 회계연도다. 데이터 기간이 아니다.
     FY2022 10-K 에 FY2020 비교치가 fy=2022 로 들어있다 → 연도가 통째로 밀린다.
  2. 10-K 안에 분기값(90일)도 함께 실린다. fp=="FY" 만 보면 통과한다.
둘 다 조용히 틀리는 종류라 다운스트림(역DCF·성장률)을 전부 오염시킨다.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from src.sources import sec_edgar as se
from src.provenance import Unavailable

# 애플 실제 응답 구조 (2026-08-24 확인)
ENTRIES = [
    # 분기값 — 걸러져야 한다
    {"form": "10-K", "fp": "FY", "fy": 2020, "filed": "2020-10-30",
     "start": "2020-06-28", "end": "2020-09-26", "val": 64_698_000_000, "frame": "CY2020Q3"},
    {"form": "10-K", "fp": "FY", "fy": 2020, "filed": "2020-10-30",
     "start": "2019-06-30", "end": "2019-09-28", "val": 64_040_000_000, "frame": "CY2019Q3"},
    # 연간값 — fy 는 제각각이지만 end 로 정렬하면 맞다
    {"form": "10-K", "fp": "FY", "fy": 2022, "filed": "2022-10-28",
     "start": "2019-09-29", "end": "2020-09-26", "val": 274_515_000_000, "frame": "CY2020"},
    {"form": "10-K", "fp": "FY", "fy": 2023, "filed": "2023-11-03",
     "start": "2020-09-27", "end": "2021-09-25", "val": 365_817_000_000, "frame": "CY2021"},
    {"form": "10-K", "fp": "FY", "fy": 2024, "filed": "2024-11-01",
     "start": "2021-09-26", "end": "2022-09-24", "val": 394_328_000_000, "frame": "CY2022"},
    # 같은 기간의 재표시 — 최신 제출본을 써야 한다
    {"form": "10-K", "fp": "FY", "fy": 2023, "filed": "2023-11-03",
     "start": "2021-09-26", "end": "2022-09-24", "val": 394_000_000_000},
    # 10-Q 는 제외
    {"form": "10-Q", "fp": "Q3", "fy": 2024, "filed": "2024-08-01",
     "start": "2023-10-01", "end": "2024-06-29", "val": 296_000_000_000},
]
FACTS = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": ENTRIES}}}}}


class TestAnnualFilter(unittest.TestCase):
    def test_quarterly_rejected(self) -> None:
        self.assertFalse(se._is_annual({"start": "2020-06-28", "end": "2020-09-26"}))

    def test_annual_accepted(self) -> None:
        self.assertTrue(se._is_annual({"start": "2019-09-29", "end": "2020-09-26"}))

    def test_53_week_year_accepted(self) -> None:
        """52/53주 회계연도(370일)도 연간이다."""
        self.assertTrue(se._is_annual({"start": "2022-09-25", "end": "2023-09-30"}))

    def test_instant_fact_accepted(self) -> None:
        """주식수 등 시점 항목은 start 가 없다."""
        self.assertTrue(se._is_annual({"end": "2025-09-27"}))


class TestSeriesMapping(unittest.TestCase):
    def _run(self):
        with patch.object(se, "cik_for", return_value="0000320193"), \
             patch.object(se, "get_json", return_value=FACTS), \
             patch.object(se, "_headers", return_value={}):
            return se.annual_series("AAPL", "Revenues")

    def test_year_comes_from_period_end_not_fy_field(self) -> None:
        """fy=2022 인 항목이 FY2020 으로 매핑돼야 한다."""
        got = {s.value.fiscal_year: s.value.value for s in self._run()}
        self.assertEqual(got[2020], 274_515_000_000)
        self.assertEqual(got[2021], 365_817_000_000)
        self.assertEqual(got[2022], 394_328_000_000)

    def test_quarterly_values_absent(self) -> None:
        vals = {s.value.value for s in self._run()}
        self.assertNotIn(64_698_000_000, vals)
        self.assertNotIn(64_040_000_000, vals)

    def test_latest_filing_wins_on_restatement(self) -> None:
        got = {s.value.fiscal_year: s.value.value for s in self._run()}
        self.assertEqual(got[2022], 394_328_000_000)   # filed 2024-11-01 쪽

    def test_10q_excluded(self) -> None:
        self.assertNotIn(296_000_000_000, {s.value.value for s in self._run()})

    def test_chronological_order(self) -> None:
        years = [s.value.fiscal_year for s in self._run()]
        self.assertEqual(years, sorted(years))

    def test_period_end_recorded_in_citation(self) -> None:
        s = self._run()[0]
        self.assertIn("기간종료", s.source.locator.section)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBestTagSelection(unittest.TestCase):
    """후보 태그 중 최신 데이터를 가진 것을 골라야 한다.

    실제 사례: NVDA 는 CapEx 를 PaymentsToAcquireProductiveAssets 로 태깅하는데,
    폐기된 PaymentsToAcquirePropertyPlantAndEquipment 에 2010~2012 3건이 남아 있다.
    첫 매치를 쓰면 옛 3건이 선택되고, FCF 가 FY2010~2012 만 반환되어
    시가총액 대비 6,783배 같은 그럴듯한 오답이 나온다.
    """

    OLD = [{"form": "10-K", "fp": "FY", "fy": 2012, "filed": "2012-03-01",
            "start": "2011-01-31", "end": "2012-01-29", "val": 1e8}]
    NEW = [{"form": "10-K", "fp": "FY", "fy": y, "filed": f"{y}-03-01",
            "start": f"{y-1}-01-26", "end": f"{y}-01-25", "val": 6e9}
           for y in (2024, 2025, 2026)]
    FACTS = {"facts": {"us-gaap": {
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": OLD}},
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": NEW}},
    }}}

    def _run(self):
        with patch.object(se, "cik_for", return_value="0001045810"), \
             patch.object(se, "get_json", return_value=self.FACTS), \
             patch.object(se, "_headers", return_value={}):
            return se.annual_series("NVDA", "CapEx")

    def test_picks_tag_with_recent_data(self) -> None:
        got = self._run()
        self.assertEqual(got[-1].value.period_end, date(2026, 1, 25))
        self.assertEqual(len(got), 3)

    def test_stale_tag_not_used(self) -> None:
        self.assertNotIn(2012, {s.value.fiscal_year for s in self._run()})


class TestFcfGuard(unittest.TestCase):
    """최신 OCF 기간이 교집합에 없으면 실패로 보고한다 (조용한 절삭 금지)."""

    def _fake(self, ocf_ends, capex_ends):
        from src.models import FinancialFact
        from src.provenance import Sourced, primary_api

        def mk(ends, concept, val):
            return [Sourced(FinancialFact(concept, "USD", date.fromisoformat(e),
                                          date.fromisoformat(e).year, val),
                            primary_api("t", "https://x")) for e in ends]
        return mk(ocf_ends, "OperatingCashFlow", 100.0), mk(capex_ends, "CapEx", 10.0)

    def test_latest_ocf_missing_from_intersection_fails(self) -> None:
        ocf, capex = self._fake(["2012-01-29", "2025-01-26", "2026-01-25"], ["2012-01-29"])
        with patch.object(se, "annual_series", side_effect=[ocf, capex]):
            r = se.free_cash_flow("NVDA")
        self.assertIsInstance(r, Unavailable)
        self.assertIn("CapEx 태그 불일치", r.reason)

    def test_aligned_series_passes(self) -> None:
        ends = ["2024-01-28", "2025-01-26", "2026-01-25"]
        ocf, capex = self._fake(ends, ends)
        with patch.object(se, "annual_series", side_effect=[ocf, capex]):
            r = se.free_cash_flow("NVDA")
        self.assertEqual(len(r), 3)
        self.assertAlmostEqual(r[-1].value.value, 90.0)

    def test_too_few_years_fails(self) -> None:
        ends = ["2025-01-26", "2026-01-25"]
        ocf, capex = self._fake(ends, ends)
        with patch.object(se, "annual_series", side_effect=[ocf, capex]):
            r = se.free_cash_flow("NVDA")
        self.assertIsInstance(r, Unavailable)
        self.assertIn("최소", r.reason)
