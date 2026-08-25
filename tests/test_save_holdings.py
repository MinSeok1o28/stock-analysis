"""보유 저장 검증. 네트워크 없이 돈다.

보유 현황은 **이 시스템의 유일한 진실의 원천**이다 (CLAUDE.md).
깨진 파일을 남기느니 저장을 실패시켜야 한다 — 이 파일이 그 경계를 고정한다.

실제로 새어 나갔던 자리: `load_holdings` 는 형식 오류에 `Unavailable` 이 아니라
`PortfolioError` 를 **던진다**. 반환값만 검사하면 예외가 그대로 빠져나가
원복 코드에 도달하지 못하고 깨진 파일이 남는다.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

from src.portfolio_io import PortfolioError, load_holdings, save_holdings

GOOD = [
    {"ticker": "AAPL", "name": "애플", "asset_type": "single_stock",
     "market": "US", "quantity": 30, "avg_cost": 180.5, "currency": "USD"},
    {"ticker": "005930", "name": "삼성전자", "asset_type": "single_stock",
     "market": "KR", "quantity": 100, "avg_cost": 71000, "currency": "KRW"},
]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "holdings.yaml"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seed(self, rows=GOOD) -> None:
        save_holdings(rows, "KRW", self.path)

    def tickers(self) -> list[str]:
        d = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        return [h["ticker"] for h in d["holdings"]]


class TestHappyPath(Base):
    def test_writes_and_reloads(self) -> None:
        p = save_holdings(GOOD, "KRW", self.path)
        self.assertEqual([s.value.ticker for s in p.holdings], ["AAPL", "005930"])
        self.assertEqual(p.base_currency, "KRW")

    def test_stamps_today(self) -> None:
        save_holdings(GOOD, "KRW", self.path)
        d = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertEqual(d["updated"], date.today().isoformat())

    def test_ticker_is_uppercased_and_trimmed(self) -> None:
        save_holdings([{"ticker": " aapl ", "quantity": 1, "avg_cost": 1,
                        "currency": "USD"}], "USD", self.path)
        self.assertEqual(self.tickers(), ["AAPL"])

    def test_market_inferred_from_ticker(self) -> None:
        """사람이 티커만 골라도 되게 — 시장은 표기로 가른다."""
        save_holdings([{"ticker": "005930", "quantity": 1, "avg_cost": 1,
                        "currency": "KRW"}], "KRW", self.path)
        d = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertEqual(d["holdings"][0]["market"], "KR")

    def test_name_defaults_to_ticker(self) -> None:
        save_holdings([{"ticker": "ZZZZ", "quantity": 1, "avg_cost": 1,
                        "currency": "USD"}], "USD", self.path)
        d = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertEqual(d["holdings"][0]["name"], "ZZZZ")

    def test_blank_tickers_dropped(self) -> None:
        save_holdings(GOOD + [{"ticker": "  "}], "KRW", self.path)
        self.assertEqual(self.tickers(), ["AAPL", "005930"])

    def test_backup_is_written(self) -> None:
        self.seed()
        save_holdings([GOOD[0]], "KRW", self.path)
        bak = self.path.with_suffix(".yaml.bak")
        self.assertTrue(bak.exists())
        self.assertIn("005930", bak.read_text(encoding="utf-8"))


class TestRollback(Base):
    def test_negative_quantity_restores_previous_file(self) -> None:
        """실제로 새어 나갔던 자리 — load_holdings 가 예외를 던지는 경로."""
        self.seed()
        bad = [{"ticker": "AAPL", "name": "애플", "asset_type": "single_stock",
                "market": "US", "quantity": -5, "avg_cost": 1, "currency": "USD"}]
        with self.assertRaises(PortfolioError):
            save_holdings(bad, "KRW", self.path)
        self.assertEqual(self.tickers(), ["AAPL", "005930"], "깨진 파일이 남았다")

    def test_unknown_asset_type_restores(self) -> None:
        self.seed()
        with self.assertRaises(PortfolioError):
            save_holdings([{"ticker": "AAPL", "asset_type": "없는유형",
                            "market": "US", "quantity": 1, "avg_cost": 1,
                            "currency": "USD"}], "KRW", self.path)
        self.assertEqual(self.tickers(), ["AAPL", "005930"])

    def test_duplicate_tickers_restores(self) -> None:
        self.seed()
        dup = [dict(GOOD[0]), dict(GOOD[0])]
        with self.assertRaises(PortfolioError):
            save_holdings(dup, "KRW", self.path)
        self.assertEqual(self.tickers(), ["AAPL", "005930"])

    def test_empty_rows_refused_before_touching_the_file(self) -> None:
        self.seed()
        with self.assertRaises(PortfolioError):
            save_holdings([], "KRW", self.path)
        self.assertEqual(self.tickers(), ["AAPL", "005930"])

    def test_no_stray_file_when_there_was_none(self) -> None:
        """원래 파일이 없었다면 실패 후에도 흔적을 남기지 않는다."""
        with self.assertRaises(PortfolioError):
            save_holdings([{"ticker": "AAPL", "market": "US", "quantity": -1,
                            "avg_cost": 1, "currency": "USD"}], "KRW", self.path)
        self.assertFalse(self.path.exists())

    def test_still_loadable_after_failure(self) -> None:
        self.seed()
        with self.assertRaises(PortfolioError):
            save_holdings([{"ticker": "AAPL", "market": "US", "quantity": -1,
                            "avg_cost": 1, "currency": "USD"}], "KRW", self.path)
        got = load_holdings(self.path)
        self.assertFalse(hasattr(got, "reason"))
        self.assertEqual(len(got.holdings), 2)


if __name__ == "__main__":
    unittest.main()
