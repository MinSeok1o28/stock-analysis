"""출처 규칙이 실제로 막는지 검사. 이게 통과해야 CLAUDE.md 1번 원칙이 유효하다."""

from __future__ import annotations

import unittest

from src.provenance import (Locator, ProvenanceError, Source, SourceKind, Sourced,
                            Tier, Unavailable, local_filing, primary_api,
                            require_sourced, web)


class TestSourceTiers(unittest.TestCase):
    def test_primary_numeric_allowed(self) -> None:
        s = Sourced(391.0, primary_api("SEC", "https://data.sec.gov/x"))
        self.assertEqual(s.value, 391.0)

    def test_scraped_numeric_rejected(self) -> None:
        with self.assertRaises(ProvenanceError):
            Sourced(123.4, web("검색", "https://e.com"))

    def test_scraped_text_allowed(self) -> None:
        s = Sourced("실적 임박", web("뉴스", "https://n.com"))
        self.assertEqual(s.source.tier, Tier.SCRAPED)

    def test_bool_is_not_numeric(self) -> None:
        Sourced(True, web("검색", "https://e.com"))   # 예외 없어야 함


class TestPageLocator(unittest.TestCase):
    def test_page_on_local_document_allowed(self) -> None:
        s = local_filing("10-K", "data/raw/a.htm", page=42)
        self.assertEqual(s.locator.page, 42)

    def test_page_on_web_rejected(self) -> None:
        """웹 출처에 페이지 번호 = 가장 위험한 환각. 생성 단계에서 막는다."""
        with self.assertRaises(ProvenanceError):
            Source("검색", Tier.SCRAPED, SourceKind.WEB, Locator(url="https://e.com", page=7))

    def test_page_on_api_rejected(self) -> None:
        with self.assertRaises(ProvenanceError):
            Source("FMP", Tier.VENDOR, SourceKind.API, Locator(url="https://f.com", page=3))

    def test_web_requires_url(self) -> None:
        with self.assertRaises(ProvenanceError):
            Source("검색", Tier.SCRAPED, SourceKind.WEB, Locator())


class TestRenderGuard(unittest.TestCase):
    def test_bare_number_rejected(self) -> None:
        with self.assertRaises(ProvenanceError):
            require_sourced("매출", 12345)

    def test_bare_string_rejected(self) -> None:
        with self.assertRaises(ProvenanceError):
            require_sourced("설명", "매출 391B")

    def test_unavailable_passes(self) -> None:
        u = require_sourced("트랜스크립트", Unavailable("트랜스크립트", "무료 소스 없음"))
        self.assertIn("확인 필요", str(u))

    def test_map_preserves_source(self) -> None:
        s = Sourced(100.0, primary_api("SEC", "https://data.sec.gov/x"))
        self.assertEqual(s.map(lambda v: v / 1e2).source, s.source)


class TestNoTradeSignal(unittest.TestCase):
    def test_signal_rejects_trade_language(self) -> None:
        from src.models import Signal, SignalKind
        for bad in ("지금 매수", "전량 매도 권장", "목표주가 200달러", "Strong buy"):
            with self.assertRaises(ValueError, msg=bad):
                Signal(SignalKind.RUN_PRICE_DECODER, "AAPL", bad)

    def test_signal_kinds_contain_no_trade_action(self) -> None:
        from src.models import SignalKind
        for k in SignalKind:
            self.assertNotIn("매수", k.value)
            self.assertNotIn("매도", k.value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
