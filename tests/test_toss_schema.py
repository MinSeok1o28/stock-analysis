"""토스 응답 파싱 검증. 2026-08-24 실제 응답을 픽스처로 고정한다.

이 테스트가 있는 이유: 캔들이 최신순으로 오는데 시간순으로 가정하면
변동률 **부호가 뒤집힌다.** 조용히 틀리는 종류의 버그라 반드시 고정해야 한다.
네트워크 없이 돈다.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.sources import toss
from src.provenance import Unavailable

PRICES = {"result": [
    {"symbol": "AAPL", "timestamp": "2026-08-24T16:19:18.000+09:00",
     "lastPrice": "309.66", "currency": "USD"},
    {"symbol": "MSFT", "timestamp": "2026-08-24T16:19:05.000+09:00",
     "lastPrice": "483.65", "currency": "USD"},
]}

# API는 최신순으로 준다 (08-24 가 먼저).
CANDLES = {"result": {"candles": [
    {"timestamp": "2026-08-24T13:00:00.000+09:00", "openPrice": "310.01", "highPrice": "310.33",
     "lowPrice": "309.17", "closePrice": "309.66", "volume": "47853", "currency": "USD"},
    {"timestamp": "2026-08-21T13:00:00.000+09:00", "openPrice": "312.05", "highPrice": "312.38",
     "lowPrice": "307.01", "closePrice": "309.35", "volume": "46876815", "currency": "USD"},
], "nextBefore": "2026-08-20T13:00:00.000+09:00"}}

FX = {"result": {"baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1383.7",
                 "midRate": "1383.5", "rateChangeType": "UP",
                 "validFrom": "2026-08-24T16:18:41.000+09:00"}}


class TestPrices(unittest.TestCase):
    def test_parses_lastPrice_string(self) -> None:
        with patch.object(toss, "_get", return_value=PRICES):
            got = toss.prices(["AAPL", "MSFT"])
        self.assertEqual(got["AAPL"].value, 309.66)
        self.assertIsInstance(got["AAPL"].value, float)

    def test_tier_is_vendor_so_numeric_allowed(self) -> None:
        with patch.object(toss, "_get", return_value=PRICES):
            got = toss.prices(["AAPL"])
        self.assertEqual(int(got["AAPL"].source.tier), 2)

    def test_over_200_symbols_refused(self) -> None:
        self.assertIsInstance(toss.prices([f"T{i}" for i in range(201)]), Unavailable)

    def test_unknown_schema_fails_loudly(self) -> None:
        with patch.object(toss, "_get", return_value={"result": [{"sym": "X", "p": 1}]}):
            self.assertIsInstance(toss.prices(["X"]), Unavailable)


class TestCandles(unittest.TestCase):
    def test_normalized_to_chronological(self) -> None:
        """API는 최신순. 시간순으로 뒤집어야 한다."""
        with patch.object(toss, "_get", return_value=CANDLES):
            c = toss.daily_candles("AAPL", 2)
        stamps = [r["timestamp"][:10] for r in c.value]
        self.assertEqual(stamps, ["2026-08-21", "2026-08-24"])

    def test_field_names_normalized(self) -> None:
        with patch.object(toss, "_get", return_value=CANDLES):
            c = toss.daily_candles("AAPL", 2)
        self.assertEqual(set(c.value[0]), {"timestamp", "open", "high", "low",
                                           "close", "volume", "currency"})
        self.assertEqual(c.value[-1]["close"], 309.66)

    def test_overnight_move_sign_is_correct(self) -> None:
        """309.35(8/21) → 309.66(8/24) 이면 +0.10%. 부호가 뒤집히면 실패한다."""
        with patch.object(toss, "_get", return_value=CANDLES):
            m = toss.overnight_move("AAPL")
        self.assertAlmostEqual(m.value, (309.66 - 309.35) / 309.35, places=9)
        self.assertGreater(m.value, 0)

    def test_single_candle_is_unavailable(self) -> None:
        one = {"result": {"candles": CANDLES["result"]["candles"][:1]}}
        with patch.object(toss, "_get", return_value=one):
            self.assertIsInstance(toss.overnight_move("AAPL"), Unavailable)


class TestExchangeRate(unittest.TestCase):
    def test_parses_rate_string(self) -> None:
        with patch.object(toss, "_get", return_value=FX):
            self.assertEqual(toss.exchange_rate().value, 1383.7)


class TestNum(unittest.TestCase):
    def test_handles_strings_and_commas(self) -> None:
        self.assertEqual(toss._num("1,383.7"), 1383.7)

    def test_returns_none_not_zero(self) -> None:
        """조용히 0으로 만들면 계산이 오염된다."""
        self.assertIsNone(toss._num("N/A"))
        self.assertIsNone(toss._num(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
