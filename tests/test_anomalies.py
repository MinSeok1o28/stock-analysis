"""급등락 이상치 판별 검증. 네트워크 없이 돈다.

실제로 잘못 읽을 뻔한 사례를 고정한다: 2026-08-25 한국 급락 1위 096610 알에프세미 -95.34%.
액면분할로 짐작했지만 공시로 확인한 원인은 **상장폐지에 따른 정리매매**였다.
그래서 이 모듈은 정황(일봉)과 확정(공시)을 분리한다 — 정황만 보고 원인을 단정하면 틀린다.
"""

from __future__ import annotations

import unittest

from src.core.anomalies import (Anomaly, AnomalyKind, inspect, warnings,
                                worth_checking)
from src.models import Market
from src.sources.open_dart import ACTION_KEYWORDS


def bars(closes, volumes=None):
    volumes = [1000] * len(closes) if volumes is None else volumes
    return [{"date": f"2026-01-{i+1:02d}", "close": c, "volume": v}
            for i, (c, v) in enumerate(zip(closes, volumes))]


def halted(quiet_sessions: int, held: float, resumed: float):
    """정지 구간(무거래·종가고정) 뒤에 재개봉 하나를 붙인 시계열."""
    closes = [held * 1.1] + [held] * (quiet_sessions + 1) + [resumed]
    vols = [500] + [0] * (quiet_sessions + 1) + [3_000_000]
    return bars(closes, vols)


class TestThreshold(unittest.TestCase):
    def test_ordinary_move_is_not_flagged(self) -> None:
        """±20% 미만은 검사하지 않는다 — 평범한 변동에 경고를 붙이지 않는다."""
        self.assertEqual(inspect(-0.18, bars([100, 82])), [])

    def test_none_rate_is_not_flagged(self) -> None:
        self.assertEqual(inspect(None, bars([100, 82])), [])


class TestHaltResume(unittest.TestCase):
    def test_detects_the_rfsemi_shape(self) -> None:
        """096610: 정지 구간 종가 2,965 고정 → 재개 138. -95.34% 는 정지 직전가와의 비교다."""
        rows = halted(10, 2965.0, 138.0)
        hits = inspect(-0.9534, rows)
        kinds = [h.kind for h in hits]
        self.assertIn(AnomalyKind.HALT_RESUME, kinds)
        self.assertTrue(any(h.invalidates_rate for h in hits))

    def test_21_5x_drop_is_not_called_a_split(self) -> None:
        """2,965 → 138 은 21.5배다. 흔한 분할 배수가 아니므로 분할로 몰지 않는다."""
        hits = inspect(-0.9534, halted(10, 2965.0, 138.0))
        self.assertNotIn(AnomalyKind.SPLIT_LIKE, [h.kind for h in hits])

    def test_short_quiet_run_is_not_a_halt(self) -> None:
        """2세션 무거래는 정지로 보지 않는다 (기준 3세션)."""
        hits = inspect(-0.50, halted(1, 100.0, 50.0))
        self.assertNotIn(AnomalyKind.HALT_RESUME, [h.kind for h in hits])

    def test_saturated_window_says_at_least(self) -> None:
        """무거래 구간이 조회 창을 다 채우면 그 이전은 모른다 — '이상' 으로 적는다."""
        rows = bars([100] * 6 + [20], [0] * 6 + [999])
        hit = next(h for h in inspect(-0.80, rows) if h.kind is AnomalyKind.HALT_RESUME)
        self.assertIn("이상", hit.detail)

    def test_bounded_window_is_exact(self) -> None:
        hit = next(h for h in inspect(-0.80, halted(3, 100.0, 20.0))
                   if h.kind is AnomalyKind.HALT_RESUME)
        self.assertNotIn("이상", hit.detail)
        self.assertIn("4거래일", hit.detail)


class TestNoHistory(unittest.TestCase):
    def test_single_candle_cannot_be_compared(self) -> None:
        hits = inspect(-0.246, bars([17670.0]))
        self.assertEqual([h.kind for h in hits], [AnomalyKind.NO_HISTORY])
        self.assertTrue(hits[0].invalidates_rate)

    def test_empty_series(self) -> None:
        self.assertEqual([h.kind for h in inspect(0.5, [])], [AnomalyKind.NO_HISTORY])


class TestSplitLike(unittest.TestCase):
    def test_ten_to_one_split(self) -> None:
        hits = inspect(-0.90, bars([50_000, 5_000]))
        self.assertIn(AnomalyKind.SPLIT_LIKE, [h.kind for h in hits])

    def test_five_to_one_merge(self) -> None:
        hits = inspect(4.0, bars([1_000, 5_000]))
        self.assertIn(AnomalyKind.SPLIT_LIKE, [h.kind for h in hits])

    def test_ratio_off_by_more_than_tolerance(self) -> None:
        """9.5배는 10:1 분할 허용오차 밖이다."""
        hits = inspect(-0.8947, bars([50_000, 5_263]))
        self.assertNotIn(AnomalyKind.SPLIT_LIKE, [h.kind for h in hits])

    def test_ordinary_doubling_is_not_a_merge(self) -> None:
        """실제로 오탐이 났던 자리 — AMIX 5.37 → 10.52 (+96%) 는 그냥 급등이었다.

        ±100% 는 흔한 변동폭이라 허용오차가 넓으면 그 구간이 통째로 분할로 잡힌다.
        진짜 권리락은 기준가가 정확히 배수로 조정되므로 좁혀도 놓치지 않는다.
        """
        hits = inspect(0.9590, bars([5.37, 10.52]))
        self.assertNotIn(AnomalyKind.SPLIT_LIKE, [h.kind for h in hits])

    def test_exact_merge_still_detected(self) -> None:
        hits = inspect(1.0, bars([5.00, 10.00]))
        self.assertIn(AnomalyKind.SPLIT_LIKE, [h.kind for h in hits])


class TestBaseMismatch(unittest.TestCase):
    def test_ranking_rate_disagrees_with_bars(self) -> None:
        """랭킹은 기준가(basePrice), 여기는 직전 종가 — 크게 어긋나면 그대로 읽으면 안 된다."""
        hits = inspect(-0.30, bars([100, 50]))
        self.assertIn(AnomalyKind.BASE_MISMATCH, [h.kind for h in hits])

    def test_agreement_within_tolerance(self) -> None:
        hits = inspect(-0.50, bars([100, 50]))
        self.assertNotIn(AnomalyKind.BASE_MISMATCH, [h.kind for h in hits])


class TestExtreme(unittest.TestCase):
    def test_unexplained_move_says_it_may_be_real(self) -> None:
        """008930 한미사이언스처럼 정황이 없으면 '실제 변동일 수 있다' 로 남긴다."""
        hits = inspect(-0.25, bars([100, 75]))
        self.assertEqual([h.kind for h in hits], [AnomalyKind.EXTREME])
        self.assertFalse(hits[0].invalidates_rate)

    def test_extreme_is_not_a_warning(self) -> None:
        """검사했고 깨끗했다는 결과다. ⚠ 로 띄우면 급등 상위가 전부 경고로 도배된다."""
        hits = inspect(1.22, bars([1.73, 3.84]))
        self.assertEqual(warnings(hits), [])
        self.assertFalse(worth_checking(hits))

    def test_halt_is_a_warning(self) -> None:
        hits = inspect(-0.9534, halted(10, 2965.0, 138.0))
        self.assertTrue(warnings(hits))
        self.assertTrue(worth_checking(hits))


class TestActionKeywords(unittest.TestCase):
    """공시 제목 필터. DART 는 이 유형을 코드 하나로 뽑아주지 않아 제목 매칭을 쓴다."""

    HITS = (
        "주권매매거래정지해제 (상장폐지에 따른 정리매매 개시)",
        "기타시장안내 (상장폐지결정 등 효력정지 가처분 신청 기각에 따른 정리매매절차 재개)",
        "주식분할결정",
        "주식병합결정",
        "감자결정",
        "무상증자결정",
    )
    MISSES = (
        "임원ㆍ주요주주특정증권등소유상황보고서",
        "주식등의대량보유상황보고서(일반)",
        "반기보고서 (2026.06)",
        "최대주주등소유주식변동신고서",
    )

    def test_matches_corporate_actions(self) -> None:
        for nm in self.HITS:
            with self.subTest(nm=nm):
                self.assertTrue(any(k in nm for k in ACTION_KEYWORDS))

    def test_ignores_routine_filings(self) -> None:
        for nm in self.MISSES:
            with self.subTest(nm=nm):
                self.assertFalse(any(k in nm for k in ACTION_KEYWORDS))


class TestMarketOfTicker(unittest.TestCase):
    """대시보드가 한국·미국을 나눠 보여주려면 티커만으로 시장을 갈라야 한다."""

    def test_krx_numeric_codes(self) -> None:
        for t in ("005930", "096610", "900270", "019175"):
            with self.subTest(t=t):
                self.assertIs(Market.of_ticker(t), Market.KR)

    def test_krx_new_style_codes_have_letters(self) -> None:
        """0155E0·0220W0 처럼 문자가 섞여도 첫 글자는 숫자다."""
        for t in ("0155E0", "0220W0", "0068Y0"):
            with self.subTest(t=t):
                self.assertIs(Market.of_ticker(t), Market.KR)

    def test_us_tickers(self) -> None:
        for t in ("AAPL", "NVDA", "SPY", "BRK.B", "F"):
            with self.subTest(t=t):
                self.assertIs(Market.of_ticker(t), Market.US)

    def test_six_letter_us_ticker_is_not_kr(self) -> None:
        self.assertIs(Market.of_ticker("GOOGLE"), Market.US)


class TestAnomalyRendering(unittest.TestCase):
    def test_str_carries_kind_and_detail(self) -> None:
        a = Anomaly(AnomalyKind.HALT_RESUME, "직전 10거래일 무거래")
        self.assertEqual(str(a), "[거래정지 후 재개 정황] 직전 10거래일 무거래")


if __name__ == "__main__":
    unittest.main()
