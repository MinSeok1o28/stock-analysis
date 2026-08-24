"""1차 출처: 한국은행 ECOS (기준금리·환율·CPI). 무료 키 필요.

미구현. 호출하면 Unavailable을 반환하므로 상위 계층은 정상 동작한다.
구현 시 반환 타입 계약(Sourced | Unavailable)을 유지할 것.
"""

from __future__ import annotations

from ..provenance import Sourced, Unavailable


def latest(stat_code: str):  # noqa: ARG001
    return Unavailable("한국 매크로 지표", "미구현 — src/sources/bok_ecos.py")
