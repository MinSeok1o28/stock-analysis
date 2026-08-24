"""계층 규칙 검사. CLAUDE.md의 '의존은 항상 안쪽으로만'을 실제로 강제한다.

이 테스트가 있어야 계층이 문서상 약속이 아니라 지켜지는 규칙이 된다.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.ImportFrom):
            out.add("." * node.level)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out


class TestLayering(unittest.TestCase):
    def test_core_does_not_import_sources(self) -> None:
        """핵심은 경계를 몰라야 한다. 계산 함수가 데이터를 가져오면 테스트가 불가능해진다."""
        for f in (SRC / "core").rglob("*.py"):
            for mod in imports_of(f):
                self.assertNotIn("sources", mod, f"{f.relative_to(SRC)} → {mod}")

    def test_core_has_no_io(self) -> None:
        """순수성. requests·urllib·open 사용 금지."""
        banned = {"requests", "urllib", "urllib.request", "http", "socket"}
        for f in (SRC / "core").rglob("*.py"):
            self.assertFalse(
                imports_of(f) & banned,
                f"{f.relative_to(SRC)} 가 I/O 모듈을 import함: {imports_of(f) & banned}",
            )

    def test_models_imports_nothing_internal(self) -> None:
        """모델은 가장 안쪽. 프로젝트 내 어떤 것도 import하지 않는다."""
        for mod in imports_of(SRC / "models.py"):
            self.assertFalse(mod.startswith("."), f"models.py → {mod}")

    def test_provenance_knows_only_models(self) -> None:
        for mod in imports_of(SRC / "provenance.py"):
            if mod.startswith("."):
                self.assertIn("models", mod, f"provenance.py → {mod}")

    def test_sources_do_not_import_render(self) -> None:
        for f in (SRC / "sources").rglob("*.py"):
            for mod in imports_of(f):
                self.assertNotIn("render", mod, f"{f.relative_to(SRC)} → {mod}")


class TestBrokerBoundary(unittest.TestCase):
    """CLAUDE.md 개정 조항: 브로커 연동은 시세·종목 조회로만 제한한다.

    문서상 약속이 아니라 검사되는 규칙으로 만든다.
    """

    TOSS = SRC / "sources" / "toss.py"

    def test_no_account_header_in_code(self) -> None:
        """계좌 헤더 문자열이 docstring 밖 어디에도 없어야 한다.

        토스는 계좌·자산·주문 API에 이 헤더를 요구한다. 코드가 이 문자열을
        만들지 않으면 그 API들은 호출 자체가 불가능하다. (설명 문구는 예외)
        """
        tree = ast.parse(self.TOSS.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in docstrings:
                    continue
                self.assertNotIn(
                    "X-Tossinvest-Account", node.value,
                    f"line {node.lineno}: 계좌 헤더가 코드 문자열로 존재함",
                )

    def test_no_write_http_methods(self) -> None:
        """GET 외의 HTTP 동사는 토큰 발급(POST /oauth2/token)에만 허용된다."""
        tree = ast.parse(self.TOSS.read_text(encoding="utf-8"))
        posts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"post", "put", "delete", "patch"}:
                posts.append(node.attr)
        self.assertEqual(posts, ["post"], f"쓰기 동사 사용: {posts} (토큰 발급 1건만 허용)")

    def test_allowlist_excludes_account_and_order_paths(self) -> None:
        from src.sources.toss import ALLOWED_PATHS
        for banned in ("/api/v1/orders", "/api/v1/conditional-orders", "/api/v1/holdings",
                       "/api/v1/accounts", "/api/v1/buying-power", "/api/v1/sellable-quantity"):
            self.assertNotIn(banned, ALLOWED_PATHS, f"{banned} 가 허용 목록에 있음")

    def test_get_rejects_path_outside_allowlist(self) -> None:
        """네트워크에 닿기 전에 막혀야 한다 (자격증명 없이도 예외가 나야 함)."""
        from src.sources.toss import OrderPathBlocked, _get
        for banned in ("/api/v1/orders", "/api/v1/holdings", "/api/v1/accounts"):
            with self.assertRaises(OrderPathBlocked, msg=banned):
                _get(banned)

    def test_token_not_written_to_disk(self) -> None:
        """액세스 토큰은 메모리에만 둔다. 비밀을 디스크에 쓰지 않는다."""
        body = self.TOSS.read_text(encoding="utf-8")
        for bad in ("write_text", "json.dump", "write_bytes"):
            self.assertNotIn(bad, body, f"토큰이 디스크에 쓰일 수 있다: {bad}")


class TestPriceAdapter(unittest.TestCase):
    def test_skills_layer_never_imports_toss_directly(self) -> None:
        """벤더 교체 비용을 1파일로 유지한다. 상위는 prices 어댑터만 본다."""
        for f in SRC.rglob("*.py"):
            if f.name in {"toss.py", "prices.py"}:
                continue
            self.assertNotIn("toss", "".join(imports_of(f)), f"{f.relative_to(SRC)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
