"""SEC 렌더링 재무제표(R-file) 파싱 검증. 네트워크 없이 돈다.

실제 발생한 버그 2건을 고정한다:
  1. '$ 109,158' 의 선행 공백 — 기호 제거 후 strip 안 하면 그룹마다 첫 값 행이 통째로
     누락된다. 애플에서 Services(109B)와 총계(416B)가 사라졌다.
  2. 총계 행을 그룹으로 오인 — 비중이 반토막 난다(NVDA "Operating Segments" 50%).
     "나머지 합과 비슷하면 총계"라는 휴리스틱은 애플 iPhone(50%)을 총계로 오판한다.
     표 자체의 총계(__total__)를 기준으로 삼아야 한다.
"""

from __future__ import annotations

import unittest

from src.sources.sec_segments import SegmentTable, _clean_group, _num, _parse, _table_rows

APPLE_PRODUCT = [
    ["Revenue - Disaggregated Net Sales (Details) - USD ($) $ in Millions", "12 Months Ended"],
    ["Sep. 27, 2025", "Sep. 28, 2024", "Sep. 30, 2023"],
    ["Disaggregation of Revenue [Line Items]"],
    ["Net sales", "$ 416,161", "$ 391,035", "$ 383,285"],
    ["iPhone"], ["Disaggregation of Revenue [Line Items]"],
    ["Net sales", "209,586", "201,183", "200,583"],
    ["Services"], ["Disaggregation of Revenue [Line Items]"],
    ["Net sales", "$ 109,158", "$ 96,169", "$ 85,200"],
    ["Mac"], ["Disaggregation of Revenue [Line Items]"],
    ["Net sales", "33,708", "29,984", "29,357"],
]

NVDA_SEGMENT = [
    ["Segment Information - Reportable Segments (Details) - USD ($) $ in Millions", "12 Months"],
    ["Jan. 25, 2026", "Jan. 26, 2025"],
    ["Net sales", "$ 215,900", "$ 130,497"],
    ["Operating Segments"], ["Segment Reporting Information [Line Items]"],
    ["Net sales", "215,900", "130,497"], ["Operating income", "139,200", "81,453"],
    ["Compute & Networking | Operating Segments"],
    ["Net sales", "193,451", "116,193"], ["Operating income", "130,200", "80,000"],
    ["Graphics | Operating Segments"],
    ["Net sales", "22,449", "14,304"], ["Operating income", "9,000", "5,500"],
]


class TestNum(unittest.TestCase):
    def test_dollar_with_space(self) -> None:
        """'$ 109,158' — 기호 제거 후 strip 하지 않으면 None 이 된다."""
        self.assertEqual(_num("$ 109,158"), 109158.0)

    def test_parentheses_are_negative(self) -> None:
        self.assertEqual(_num("(220,960)"), -220960.0)

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(_num("Net sales"))
        self.assertIsNone(_num(""))


class TestCleanGroup(unittest.TestCase):
    def test_axis_suffix_removed(self) -> None:
        self.assertEqual(_clean_group("Americas | Operating segments"), "Americas")
        self.assertEqual(_clean_group("Compute & Networking | Operating Segments"),
                         "Compute & Networking")

    def test_plain_label_untouched(self) -> None:
        self.assertEqual(_clean_group("iPhone"), "iPhone")


class TestParsing(unittest.TestCase):
    def test_dollar_rows_not_dropped(self) -> None:
        t = _parse(APPLE_PRODUCT, "product", "R38.htm")
        self.assertIn("Services", t.rows)
        self.assertAlmostEqual(t.revenue["Services"], 109_158e6)

    def test_unit_scale_detected(self) -> None:
        self.assertEqual(_parse(APPLE_PRODUCT, "product", "R38.htm").unit_scale, 1e6)

    def test_periods_captured(self) -> None:
        self.assertEqual(len(_parse(APPLE_PRODUCT, "product", "R38.htm").periods), 3)


class TestTotalDetection(unittest.TestCase):
    def test_dominant_group_not_mistaken_for_total(self) -> None:
        """애플 iPhone(50.4%)은 나머지 합과 비슷하지만 총계가 아니다."""
        t = _parse(APPLE_PRODUCT, "product", "R38.htm")
        groups = {g for g, *_ in t.shares()}
        self.assertIn("iPhone", groups)
        self.assertNotIn("__total__", groups)

    def test_explicit_total_row_excluded(self) -> None:
        """NVDA 'Operating Segments' 는 총계다 — 그룹으로 세면 비중이 반토막."""
        t = _parse(NVDA_SEGMENT, "segment", "R82.htm")
        groups = {g for g, *_ in t.shares()}
        self.assertNotIn("Operating Segments", groups)
        self.assertEqual(groups, {"Compute & Networking", "Graphics"})

    def test_shares_sum_to_one(self) -> None:
        for rows, kind in ((APPLE_PRODUCT, "product"), (NVDA_SEGMENT, "segment")):
            t = _parse(rows, kind, "R.htm")
            self.assertAlmostEqual(sum(s for _, _, s, _ in t.shares()), 1.0, places=6)

    def test_total_revenue_uses_table_total(self) -> None:
        t = _parse(APPLE_PRODUCT, "product", "R38.htm")
        self.assertAlmostEqual(t.total_revenue, 416_161e6)

    def test_operating_margin_computed(self) -> None:
        t = _parse(NVDA_SEGMENT, "segment", "R82.htm")
        m = {g: mg for g, _, _, mg in t.shares()}
        self.assertAlmostEqual(m["Compute & Networking"], 130_200 / 193_451, places=6)


class TestTableRows(unittest.TestCase):
    def test_extracts_cells(self) -> None:
        html = "<table><tr><td>iPhone</td><td>$ 1,000</td></tr></table>"
        self.assertEqual(_table_rows(html), [["iPhone", "$ 1,000"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
