"""수치 해설·평문 읽기 검증. 네트워크 없이 돈다.

주식·회계를 모르는 사람이 표를 읽을 수 있게 하는 게 목적이다.
그런데 쉽게 쓰다 보면 "그래서 사라/팔아라"로 넘어가기 쉽다 — 그 선을 테스트로 고정한다.
"""

from __future__ import annotations

import unittest

from src.models import Market
from src.pipelines.compare import josa, plain_gap, plain_reading
from src.pipelines.stock_page import SEGMENT_AXIS, Summary
from src.render import glossary as gl

#: 매매를 권하는 말. CLAUDE.md 매매 신호 금지.
#: 다만 **부정문 안에서는 오히려 있어야 한다** — "적정주가가 아니다", "팔라는 뜻이 아니다"
#: 처럼 초보자의 오해를 미리 막는 자리이기 때문이다. 그래서 단순 부재가 아니라
#: '부정문 안에서만 등장하는가' 를 검사한다.
BANNED = ("매수", "매도", "사라", "팔아", "목표주가", "적정주가", "추천", "유망")
NEGATORS = ("아니", "않", "말하지", "금지", "못한다", "없다")


def advocacy(text: str) -> list[str]:
    """매매를 권하는 용례만 골라낸다. 부정문 안의 등장은 통과시킨다."""
    hits = []
    for w in BANNED:
        start = 0
        while (i := text.find(w, start)) >= 0:
            tail = text[i + len(w): i + len(w) + 16]
            if not any(n in tail for n in NEGATORS):
                hits.append(f"{w}: …{text[max(0, i-18):i+22]}…")
            start = i + len(w)
    return hits


def summary(**kw) -> Summary:
    base = dict(ticker="AAPL", name="애플", market=Market.US, currency="USD",
                price=310.0, market_cap=4.5e12, implied=0.175, wacc=0.092,
                rev_cagr=0.018, rev_cagr_label="매출 3년 CAGR")
    base.update(kw)
    return Summary(**base)


class TestTerms(unittest.TestCase):
    def test_every_term_has_definition_and_reading(self) -> None:
        for t in gl.TERMS:
            with self.subTest(t.key):
                self.assertTrue(t.one_line.strip(), f"{t.key}: 정의 없음")
                self.assertTrue(t.how_to_read.strip(), f"{t.key}: 읽는 법 없음")

    def test_trade_words_appear_only_inside_negations(self) -> None:
        blob = " ".join(t.label + " " + t.one_line + " " + t.how_to_read + " " + t.caution
                        for t in gl.TERMS)
        self.assertEqual(advocacy(blob), [], "해설이 매매를 권하고 있다")

    def test_the_check_would_catch_real_advocacy(self) -> None:
        """검사기 자체가 무디지 않은지 확인한다 — 부정문만 통과해야 한다."""
        self.assertEqual(advocacy("적정주가가 아니다"), [])
        self.assertTrue(advocacy("지금이 매수 기회다"))

    def test_risky_terms_carry_a_caution(self) -> None:
        """오해하면 손해로 이어지는 항목은 주의 문구가 반드시 있어야 한다."""
        for key in ("implied", "gap", "market_cap", "reaction", "wacc"):
            with self.subTest(key):
                self.assertTrue(gl.BY_KEY[key].caution.strip(), f"{key}: caution 없음")

    def test_implied_says_it_is_not_a_target_price(self) -> None:
        self.assertIn("적정주가가 아니다", gl.BY_KEY["implied"].caution)

    def test_gap_says_it_is_not_cheap_or_expensive(self) -> None:
        self.assertIn("싸다·비싸다가 아니다", gl.BY_KEY["gap"].caution)

    def test_reaction_says_it_is_not_a_forecast(self) -> None:
        self.assertIn("예측이 아니라", gl.BY_KEY["reaction"].caution)

    def test_tooltip_includes_all_parts(self) -> None:
        tip = gl.BY_KEY["gap"].tooltip
        self.assertIn("읽는 법:", tip)
        self.assertIn("주의:", tip)

    def test_unknown_key_is_silent(self) -> None:
        self.assertEqual(gl.tooltip("없는키"), "")
        self.assertEqual(gl.header("라벨", "없는키"), "라벨")

    def test_header_adds_marker(self) -> None:
        self.assertIn('class="gi"', gl.header("격차", "gap"))

    def test_panel_selects_keys(self) -> None:
        html = gl.panel(["gap"])
        self.assertIn("격차", html)
        self.assertNotIn("WACC", html)

    def test_panel_escapes_then_bolds(self) -> None:
        """**강조** 는 살리되 태그 주입은 막아야 한다."""
        html = gl.panel()
        self.assertIn("<strong>", html)
        self.assertNotIn("<script", html)


class TestJosa(unittest.TestCase):
    def test_batchim(self) -> None:
        self.assertEqual(josa("원", "이", "가"), "이")        # 받침 ㄴ
        self.assertEqual(josa("애플", "이", "가"), "이")      # 받침 ㄹ

    def test_no_batchim(self) -> None:
        self.assertEqual(josa("달러", "이", "가"), "가")
        self.assertEqual(josa("삼성전자", "이", "가"), "가")

    def test_non_hangul_tail_falls_back(self) -> None:
        self.assertEqual(josa("AAPL", "이", "가"), "가")
        self.assertEqual(josa("", "이", "가"), "가")

    def test_trailing_punctuation_is_skipped(self) -> None:
        self.assertEqual(josa("애플)", "이", "가"), "이")


class TestPlainReading(unittest.TestCase):
    def test_trade_words_appear_only_inside_negations(self) -> None:
        lines = " ".join(plain_reading(summary(
            quality_flag="warn", top_segment="iPhone", top_segment_share=0.5,
            top_segment_kind="제품별", reaction_median=0.025, reaction_n=7,
            fcf_latest=9.8e10, fcf_avg=1.02e11)))
        self.assertEqual(advocacy(lines), [], "평문 읽기가 매매를 권하고 있다")

    def test_currency_particle(self) -> None:
        self.assertIn("조원이 듭니다",
                      plain_reading(summary(market=Market.KR, market_cap=1.4e15))[0])
        self.assertIn("B달러가 듭니다", plain_reading(summary())[0])

    def test_gap_positive_wording(self) -> None:
        t = plain_gap(summary(implied=0.20, rev_cagr=0.05))
        self.assertIn("빠른 성장을 기대", t)
        self.assertIn("비싸다는 뜻이 아니라", t)

    def test_gap_negative_wording(self) -> None:
        t = plain_gap(summary(implied=0.03, rev_cagr=0.10))
        self.assertIn("느려져도 설명되는", t)
        self.assertIn("싸다는 뜻이 아니라", t)

    def test_gap_missing_side(self) -> None:
        self.assertIn("비교할 수 없습니다", plain_gap(summary(implied=None)))

    def test_fcf_outlier_is_stated(self) -> None:
        """최신 FCF 가 3년 평균에서 크게 벗어나면 그 사실을 적는다."""
        lines = plain_reading(summary(fcf_latest=3.0e10, fcf_avg=1.0e10))
        self.assertTrue(any("크게 벗어나" in x for x in lines))

    def test_fcf_normal_is_stated(self) -> None:
        lines = plain_reading(summary(fcf_latest=1.0e10, fcf_avg=1.05e10))
        self.assertTrue(any("특이한 해는 아니" in x for x in lines))

    def test_segment_axis_is_named(self) -> None:
        """애플처럼 '부문'이 곧 지역인 회사가 있어 축을 밝히지 않으면 오해된다."""
        lines = plain_reading(summary(top_segment="Americas", top_segment_share=0.43,
                                      top_segment_kind="부문별"))
        self.assertTrue(any("부문별로 나눠 보면" in x for x in lines))

    def test_missing_values_produce_no_lines(self) -> None:
        s = Summary(ticker="Z", name="Z", market=Market.US, currency="USD")
        self.assertEqual(plain_reading(s), [])

    def test_segment_axis_map_covers_source_keys(self) -> None:
        for key in ("segment", "product", "geography"):
            self.assertIn(key, SEGMENT_AXIS)


if __name__ == "__main__":
    unittest.main()
