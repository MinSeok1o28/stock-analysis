"""배치 분석 검증. 네트워크·CLI 없이 돈다 — generate() 를 갈아끼운다.

배치를 만든 이유: 순차로 돌리면 이득이 없다. 5종목 × 60초 = 300초는 나눠 기다리든
한 번에 기다리든 같다. **동시에 돌려야** 총 대기가 준다.
실측(2026-08-25): 단건 100초 · 3종목 동시 119초 → 2.5배.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from src.models import Market
from src.pipelines import compare, serve
from src.pipelines.stock_page import Summary


def wait(job, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not job.done and time.monotonic() < end:
        time.sleep(0.01)


def ok(tk: str) -> dict:
    return {"ok": True, "url": f"/stocks/{tk}.html", "note": "테스트",
            "summary": summary(tk)}


def summary(tk: str, market: Market = Market.US, **kw) -> Summary:
    base = dict(ticker=tk, name=f"{tk} 이름", market=market, currency="USD",
                price=100.0, market_cap=1e12, implied=0.20, wacc=0.09,
                rev_cagr=0.05, rev_cagr_label="매출 3년 CAGR")
    base.update(kw)
    return Summary(**base)


class TestStartBatch(unittest.TestCase):
    def test_dedupes_and_keeps_order(self) -> None:
        with patch.object(serve, "generate", side_effect=lambda t, **k: ok(t)):
            job = serve.start_batch(["nvda", "AAPL", "NVDA", " aapl ", "MSFT"])
            wait(job)
        self.assertEqual(job.tickers, ["NVDA", "AAPL", "MSFT"])

    def test_caps_ticker_count(self) -> None:
        many = [f"T{i}" for i in range(serve.BATCH_MAX_TICKERS + 7)]
        with patch.object(serve, "generate", side_effect=lambda t, **k: ok(t)):
            job = serve.start_batch(many)
            wait(job)
        self.assertEqual(len(job.tickers), serve.BATCH_MAX_TICKERS)

    def test_workers_clamped(self) -> None:
        with patch.object(serve, "generate", side_effect=lambda t, **k: ok(t)):
            hi = serve.start_batch(["A"], workers=99); wait(hi)
            lo = serve.start_batch(["B"], workers=0); wait(lo)
        self.assertEqual(hi.workers, 8)
        self.assertEqual(lo.workers, 1)

    def test_blank_tickers_dropped(self) -> None:
        with patch.object(serve, "generate", side_effect=lambda t, **k: ok(t)):
            job = serve.start_batch(["", "  ", "AAPL"]); wait(job)
        self.assertEqual(job.tickers, ["AAPL"])


class TestBatchOutcomes(unittest.TestCase):
    def test_one_failure_does_not_stop_the_rest(self) -> None:
        def gen(t, **k):
            if t == "BAD":
                return {"ok": False, "error": "재무 미확보"}
            return ok(t)
        with patch.object(serve, "generate", side_effect=gen):
            job = serve.start_batch(["AAPL", "BAD", "MSFT"]); wait(job)
        self.assertEqual(job.state["AAPL"], "ok")
        self.assertEqual(job.state["MSFT"], "ok")
        self.assertEqual(job.state["BAD"], "error")
        self.assertIn("재무 미확보", job.note["BAD"])

    def test_exception_is_caught_per_ticker(self) -> None:
        """한 종목이 예외로 죽어도 나머지는 간다."""
        def gen(t, **k):
            if t == "BOOM":
                raise RuntimeError("터졌다")
            return ok(t)
        with patch.object(serve, "generate", side_effect=gen):
            job = serve.start_batch(["AAPL", "BOOM"]); wait(job)
        self.assertEqual(job.state["AAPL"], "ok")
        self.assertEqual(job.state["BOOM"], "error")
        self.assertIn("RuntimeError", job.note["BOOM"])

    def test_runs_concurrently(self) -> None:
        """동시 실행이 이 기능의 존재 이유다 — 순차면 만들 값어치가 없다."""
        peak, live, lk = 0, 0, threading.Lock()

        def gen(t, **k):
            nonlocal peak, live
            with lk:
                live += 1
                peak = max(peak, live)
            time.sleep(0.15)
            with lk:
                live -= 1
            return ok(t)

        with patch.object(serve, "generate", side_effect=gen):
            job = serve.start_batch(["A", "B", "C", "D"], workers=4)
            wait(job, timeout=10)
        self.assertGreaterEqual(peak, 2, "동시에 두 개도 안 돌면 병렬이 아니다")

    def test_snapshot_shape(self) -> None:
        with patch.object(serve, "generate", side_effect=lambda t, **k: ok(t)):
            job = serve.start_batch(["AAPL", "MSFT"]); wait(job)
        snap = job.snapshot()
        self.assertEqual(snap["total"], 2)
        self.assertEqual(snap["finished"], 2)
        self.assertTrue(snap["done"])
        self.assertEqual([r["t"] for r in snap["rows"]], ["AAPL", "MSFT"])


class TestCompareRender(unittest.TestCase):
    def test_currency_unit_follows_market(self) -> None:
        """통화가 섞이면 한 열에 조와 B 가 같이 온다 — 셀마다 단위를 붙여야 한다."""
        html = compare.render(
            [summary("AAPL"), summary("005930", Market.KR, market_cap=1.4e15)], [])
        self.assertIn("1,000.0B", html)     # 미국 1e12 → B
        self.assertIn("1,400.0조", html)    # 한국 1.4e15 → 조

    def test_gap_is_implied_minus_history(self) -> None:
        s = summary("AAPL", implied=0.20, rev_cagr=0.05)
        self.assertAlmostEqual(s.gap, 0.15)
        self.assertIn("+15.0%p", compare.render([s], []))

    def test_gap_is_none_when_either_side_missing(self) -> None:
        self.assertIsNone(summary("A", implied=None).gap)
        self.assertIsNone(summary("A", rev_cagr=None).gap)

    def test_failures_are_listed_not_hidden(self) -> None:
        html = compare.render([], [("BAD", "재무 미확보")])
        self.assertIn("만들지 못한 종목", html)
        self.assertIn("재무 미확보", html)

    def test_empty_selection(self) -> None:
        self.assertIn("선택된 종목이 없습니다", compare.render([], []))

    def test_no_trade_language(self) -> None:
        """CLAUDE.md 매매 신호 금지. 비교 표가 순위·추천으로 읽히면 안 된다."""
        html = compare.render([summary("AAPL"), summary("MSFT")], [])
        for word in ("매수", "매도", "목표주가", "추천", "1위"):
            self.assertNotIn(word, html, f"비교 페이지에 {word!r} 가 있다")
        self.assertIn("싸다·비싸다가 아닙니다", html)

    def test_missing_narrative_is_stated(self) -> None:
        html = compare.render([summary("ZZZZ", has_narrative=False)], [])
        self.assertIn("서사가 없습니다", html)


if __name__ == "__main__":
    unittest.main()
