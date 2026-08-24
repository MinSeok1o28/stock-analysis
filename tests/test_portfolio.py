"""보유·관심 종목 로더 검증. 잘못된 입력이 조용히 통과하면 모든 계산이 오염된다."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.models import AssetType, Market
from src.portfolio_io import (PortfolioError, WatchItem, can_compute_contribution,
                              list_snapshots, load_holdings, load_watchlist,
                              upcoming_earnings, write_snapshot)
from src.provenance import ProvenanceError, SourceKind, Unavailable, require_sourced


def _tmp(text: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    f.write(text); f.close()
    return Path(f.name)


GOOD = """
updated: 2026-08-20
base_currency: KRW
holdings:
  - {ticker: AAPL, name: Apple, asset_type: single_stock, market: US, quantity: 10, avg_cost: 180.5, currency: USD}
  - {ticker: SPY,  name: S&P500 ETF, asset_type: index_etf, market: US, quantity: 5, avg_cost: 520, currency: USD}
  - {ticker: IAU,  name: Gold ETF, asset_type: commodity_etf, market: US, quantity: 30, avg_cost: 44, currency: USD}
  - {ticker: "005930", name: 삼성전자, asset_type: single_stock, market: KR, quantity: 100, avg_cost: 71000, currency: KRW}
"""


class TestLoad(unittest.TestCase):
    def setUp(self) -> None:
        self.p = load_holdings(_tmp(GOOD))
        self.assertNotIsInstance(self.p, Unavailable)

    def test_parses_all_rows(self) -> None:
        self.assertEqual(len(self.p.holdings), 4)
        self.assertEqual(self.p.tickers, ["AAPL", "SPY", "IAU", "005930"])

    def test_values_are_sourced_as_user_input(self) -> None:
        """보유 수량은 산출물에 나가는 숫자다 — 출처 없이 렌더로 갈 수 없어야 한다."""
        s = self.p.holdings[0]
        self.assertEqual(s.source.kind, SourceKind.USER_INPUT)
        self.assertIn("updated=2026-08-20", s.source.locator.section)
        require_sourced("보유", s)              # 예외 없어야 함

    def test_asset_type_drives_valuation_basis(self) -> None:
        by = self.p.by_type()
        self.assertTrue(AssetType.SINGLE_STOCK in by)
        self.assertFalse(AssetType.COMMODITY_ETF.supports_reverse_dcf)
        self.assertIn("실질금리", AssetType.COMMODITY_ETF.alt_metrics)

    def test_staleness(self) -> None:
        days, stale = self.p.staleness(date(2026, 8, 24))
        self.assertEqual(days, 4)
        self.assertFalse(stale)
        _, stale2 = self.p.staleness(date(2026, 9, 10))
        self.assertTrue(stale2)

    def test_book_value_and_fx_flag(self) -> None:
        aapl = self.p.holdings[0].value
        self.assertAlmostEqual(aapl.book_value, 1805.0)
        self.assertTrue(aapl.is_foreign_currency)
        self.assertFalse(self.p.holdings[3].value.is_foreign_currency)


class TestValidation(unittest.TestCase):
    def test_missing_file_is_unavailable_not_crash(self) -> None:
        self.assertIsInstance(load_holdings(Path("nope.yaml")), Unavailable)

    def test_placeholder_row_skipped(self) -> None:
        p = load_holdings(_tmp(
            "updated: 2026-08-24\nholdings:\n"
            "  - {ticker: EXAMPLE, asset_type: single_stock, market: US, quantity: 0, avg_cost: 0}\n"))
        self.assertTrue(p.is_empty)

    def test_bad_asset_type_fails_loudly(self) -> None:
        with self.assertRaises(PortfolioError) as ctx:
            load_holdings(_tmp(
                "holdings:\n  - {ticker: X, asset_type: 코인, market: US, quantity: 1, avg_cost: 1}\n"))
        self.assertIn("허용", str(ctx.exception))

    def test_bad_market_fails_loudly(self) -> None:
        with self.assertRaises(PortfolioError):
            load_holdings(_tmp(
                "holdings:\n  - {ticker: X, asset_type: single_stock, market: JP, quantity: 1, avg_cost: 1}\n"))

    def test_negative_quantity_rejected(self) -> None:
        with self.assertRaises(PortfolioError):
            load_holdings(_tmp(
                "holdings:\n  - {ticker: X, asset_type: single_stock, market: US, quantity: -1, avg_cost: 1}\n"))

    def test_non_numeric_rejected(self) -> None:
        with self.assertRaises(PortfolioError):
            load_holdings(_tmp(
                "holdings:\n  - {ticker: X, asset_type: single_stock, market: US, quantity: 열개, avg_cost: 1}\n"))

    def test_duplicate_ticker_rejected(self) -> None:
        with self.assertRaises(PortfolioError) as ctx:
            load_holdings(_tmp(
                "holdings:\n"
                "  - {ticker: AAPL, asset_type: single_stock, market: US, quantity: 1, avg_cost: 1}\n"
                "  - {ticker: aapl, asset_type: single_stock, market: US, quantity: 2, avg_cost: 2}\n"))
        self.assertIn("중복", str(ctx.exception))

    def test_broken_yaml_is_unavailable(self) -> None:
        self.assertIsInstance(load_holdings(_tmp("holdings: [{a: 1\n")), Unavailable)


class TestWatchlist(unittest.TestCase):
    WL = """
updated: 2026-08-24
watching:
  - {ticker: AAPL, earnings_date: 2026-08-28, note: 서비스 매출}
  - {ticker: MSFT, earnings_date: 2026-10-29}
  - {ticker: NVDA}
  - TSLA
"""

    def test_parses_mixed_forms(self) -> None:
        items = load_watchlist(_tmp(self.WL))
        self.assertEqual([s.value.ticker for s in items], ["AAPL", "MSFT", "NVDA", "TSLA"])

    def test_upcoming_within_window(self) -> None:
        items = load_watchlist(_tmp(self.WL))
        soon = upcoming_earnings(items, within_days=7, today=date(2026, 8, 24))
        self.assertEqual([s.value.ticker for s in soon], ["AAPL"])

    def test_past_earnings_excluded(self) -> None:
        items = load_watchlist(_tmp(self.WL))
        self.assertEqual(upcoming_earnings(items, 7, today=date(2026, 9, 5)), [])

    def test_no_date_is_not_a_signal(self) -> None:
        self.assertIsNone(WatchItem("NVDA").days_to_earnings(date(2026, 8, 24)))


class TestSnapshots(unittest.TestCase):
    def test_write_and_reload_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = load_holdings(_tmp(GOOD))
            dest = write_snapshot(p, date(2026, 8, 24), Path(d))
            back = load_holdings(dest)
            self.assertEqual(back.tickers, p.tickers)
            self.assertEqual(list_snapshots(Path(d)), [date(2026, 8, 24)])
            self.assertFalse(can_compute_contribution(Path(d)))
            write_snapshot(p, date(2026, 9, 1), Path(d))
            self.assertTrue(can_compute_contribution(Path(d)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
