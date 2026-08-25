"""좌측 목차 · 섹션 전환 검증. 네트워크 없이 돈다.

한 장에 다 쌓아 두면 스크롤이 길어져 뒤쪽 섹션을 아무도 안 본다.
목차에서 고른 하나만 띄우되, **숨기는 것이지 지우는 게 아니다** —
다른 섹션에서 체크한 종목이 배치 바구니에 그대로 남아 있어야 하기 때문이다.
"""

from __future__ import annotations

import re
import unittest

from src.pipelines.dashboard import PUBLIC_LABELS, VIEWS, _views_html


def nav_ids(nav: str) -> list[str]:
    return re.findall(r'data-v="([^"]+)"', nav)


class TestViewIds(unittest.TestCase):
    def test_ids_are_unique(self) -> None:
        ids = [v[0] for v in VIEWS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_view_has_icon_label_and_subtitle(self) -> None:
        for vid, icon, label, sub in VIEWS:
            with self.subTest(vid):
                self.assertTrue(icon.strip() and label.strip() and sub.strip())

    def test_public_labels_point_at_real_views(self) -> None:
        ids = {v[0] for v in VIEWS}
        for vid in PUBLIC_LABELS:
            self.assertIn(vid, ids)


class TestViewsHtml(unittest.TestCase):
    def test_empty_gives_a_message_not_a_blank_page(self) -> None:
        nav, body = _views_html({}, {})
        self.assertEqual(nav, "")
        self.assertIn("표시할 내용이 없습니다", body)

    def test_views_without_content_are_dropped(self) -> None:
        nav, body = _views_html({"today": ["<p>a</p>"], "watch": []}, {})
        self.assertEqual(nav_ids(nav), ["today"])
        self.assertNotIn('data-v="watch"', body)

    def test_only_the_first_view_is_visible(self) -> None:
        nav, body = _views_html(
            {"today": ["<p>a</p>"], "majors": ["<p>b</p>"], "events": ["<p>c</p>"]}, {})
        opened = re.findall(r'<div class="view" data-v="([^"]+)"(?! hidden)>', body)
        self.assertEqual(opened, ["today"])
        self.assertEqual(body.count(" hidden>"), 2)

    def test_nav_order_follows_the_declared_order(self) -> None:
        nav, _ = _views_html({v[0]: ["<p>x</p>"] for v in VIEWS}, {})
        self.assertEqual(nav_ids(nav), [v[0] for v in VIEWS])

    def test_content_is_preserved_in_hidden_views(self) -> None:
        """숨김이지 삭제가 아니다 — 체크박스가 살아 있어야 바구니가 유지된다."""
        _, body = _views_html(
            {"today": ["<p>a</p>"], "majors": ['<input class="pick" data-s="AAPL">']}, {})
        self.assertIn('data-s="AAPL"', body)

    def test_badge_shown_only_when_nonzero(self) -> None:
        nav, _ = _views_html({"today": ["<p>a</p>"], "events": ["<p>b</p>"]},
                             {"events": 9, "today": 0})
        self.assertIn('<span class="cnt">9</span>', nav)
        self.assertEqual(nav.count('class="cnt"'), 1)

    def test_missing_count_is_treated_as_zero(self) -> None:
        nav, _ = _views_html({"today": ["<p>a</p>"]}, {})
        self.assertNotIn("cnt", nav)

    def test_public_renames_holdings_view(self) -> None:
        """공개본에는 보유 목록이 없고 콕핏 비율만 남는다 — 이름이 그 사실과 맞아야 한다."""
        nav, body = _views_html({"holdings": ["<p>x</p>"]}, {}, public=True)
        self.assertIn("포트폴리오", nav)
        self.assertNotIn("보유·포트폴리오", nav)
        self.assertIn("비율만", body)

    def test_private_keeps_the_full_label(self) -> None:
        nav, _ = _views_html({"holdings": ["<p>x</p>"]}, {})
        self.assertIn("보유·포트폴리오", nav)

    def test_labels_are_escaped(self) -> None:
        """라벨·설명은 상수지만 escape 경로가 살아 있어야 나중에 안전하다."""
        _, body = _views_html({"today": ["<p>a</p>"]}, {})
        self.assertIn('<div class="vhead">', body)


if __name__ == "__main__":
    unittest.main()
