"""ETF 구성종목 파싱 검증. 네트워크 없이 돈다 (SSGA 실제 형식을 픽스처로)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.models import AssetType, Holding, Market
from src.sources import etf_holdings as eh
from src.provenance import Unavailable

# SSGA 실제 형식 (2026-08-24 확인)
ROWS = [
    ["Fund Name:", "State Street® SPDR® S&P 500® ETF Trust"],
    ["Ticker Symbol:", "SPY"],
    ["Holdings:", "As of 20-Aug-2026"],
    ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency"],
    ["NVIDIA CORP", "NVDA", "67066G104", "2379504", "7.978529", "-", "3.0E8", "USD"],
    ["APPLE INC", "AAPL", "037833100", "2046251", "6.945456", "-", "1.8E8", "USD"],
    ["MICROSOFT CORP", "MSFT", "594918104", "2588173", "5.429405", "-", "9.2E7", "USD"],
    ["US DOLLAR", "-", "", "", "0.010000", "-", "0", "USD"],
    ["CASH", "CASH", "", "", "0.005000", "-", "0", "USD"],
]


class TestParsing(unittest.TestCase):
    def test_weights_are_fractions_not_percent(self) -> None:
        w = eh._weights(ROWS)
        self.assertAlmostEqual(w["AAPL"], 0.06945456, places=8)

    def test_cash_and_placeholder_rows_dropped(self) -> None:
        w = eh._weights(ROWS)
        self.assertNotIn("-", w)
        self.assertNotIn("CASH", w)
        self.assertEqual(set(w), {"NVDA", "AAPL", "MSFT"})

    def test_as_of_date_extracted(self) -> None:
        """신선도는 반드시 산출물에 표기돼야 한다 (T+1 지연)."""
        self.assertEqual(eh._as_of(ROWS), "20-Aug-2026")

    def test_unrecognized_format_yields_empty(self) -> None:
        self.assertEqual(eh._weights([["a", "b"], ["1", "2"]]), {})


class TestAssetTypeFilter(unittest.TestCase):
    def _h(self, t: str, at: AssetType) -> Holding:
        return Holding(t, t, at, Market.US, 1, 1, "USD")

    def test_only_equity_etfs_are_looked_through(self) -> None:
        hs = [self._h("AAPL", AssetType.SINGLE_STOCK),
              self._h("SPY", AssetType.INDEX_ETF),
              self._h("XLK", AssetType.SECTOR_ETF),
              self._h("IAU", AssetType.COMMODITY_ETF),
              self._h("AGG", AssetType.BOND_ETF)]
        self.assertEqual(eh.equity_etfs(hs), ["SPY", "XLK"])

    def test_commodity_etf_has_no_constituents(self) -> None:
        self.assertFalse(AssetType.COMMODITY_ETF.has_equity_constituents)
        self.assertFalse(AssetType.BOND_ETF.has_equity_constituents)
        self.assertTrue(AssetType.INDEX_ETF.has_equity_constituents)


class TestFailureIsLoud(unittest.TestCase):
    def test_blocked_issuer_returns_actionable_message(self) -> None:
        with patch.object(eh, "_fetch", side_effect=eh.SourceUnavailable("404")):
            r = eh.holdings("IVV")
        self.assertIsInstance(r, Unavailable)
        self.assertIn("portfolio/etf_holdings/IVV.csv", r.reason)

    def test_look_through_map_reports_missing(self) -> None:
        with patch.object(eh, "_fetch", side_effect=eh.SourceUnavailable("404")):
            ok, missing = eh.look_through_map(["IVV"])
        self.assertEqual(ok, {})
        self.assertEqual(len(missing), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
