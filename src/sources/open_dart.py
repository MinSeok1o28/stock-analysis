"""1차 출처: 금융감독원 OpenDART (한국 공시·재무). 무료 키 필요.

미구현. 호출하면 Unavailable을 반환하므로 상위 계층은 정상 동작한다.
구현 시 반환 타입 계약(Sourced | Unavailable)을 유지할 것.
"""

from __future__ import annotations

from ..provenance import Sourced, Unavailable


def annual_financials(corp_code: str, year: int):  # noqa: ARG001
    return Unavailable("한국 상장사 재무제표", "미구현 — src/sources/open_dart.py")
