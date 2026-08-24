"""1차 출처: Frankfurter (ECB 기준환율). 키 불필요.

중요: ECB는 영업일 1회만 발표한다. 실시간 시세가 아니다.
'환율 급변'을 매일 본다면 이 사실이 결론을 바꾸므로 반환값에 명시한다.
"""

from __future__ import annotations

from ..provenance import Sourced, Unavailable, primary_api
from ._http import SourceUnavailable, get_json

BASE = "https://api.frankfurter.dev/v1"
NAME = "Frankfurter (ECB 기준환율, 영업일 종가)"


def rate(base: str = "USD", quote: str = "KRW", on: str = "latest") -> Sourced[float] | Unavailable:
    try:
        data = get_json(f"{BASE}/{on}", params={"base": base, "symbols": quote},
                        cache_key=f"{base}{quote}_{on}", ttl_sec=3_600)
        value = float(data["rates"][quote])
    except (SourceUnavailable, KeyError, TypeError) as exc:
        return Unavailable(f"{base}/{quote} 환율", f"{NAME}: {exc}")
    return Sourced(value, primary_api(
        NAME, f"{BASE}/{on}?base={base}&symbols={quote}", section=f"기준일 {data.get('date')}"
    ))


def series(base: str, quote: str, start: str, end: str) -> Sourced[dict] | Unavailable:
    """기간 시계열. 주말·공휴일은 값이 없다 (마지막 영업일 값으로 보간되지 않음)."""
    try:
        data = get_json(f"{BASE}/{start}..{end}", params={"base": base, "symbols": quote},
                        cache_key=f"{base}{quote}_{start}_{end}", ttl_sec=86_400)
        pts = {d: float(v[quote]) for d, v in data["rates"].items()}
    except (SourceUnavailable, KeyError) as exc:
        return Unavailable(f"{base}/{quote} 시계열", f"{NAME}: {exc}")
    return Sourced(pts, primary_api(NAME, f"{BASE}/{start}..{end}?base={base}&symbols={quote}"))


if __name__ == "__main__":
    r = rate()
    print("USD/KRW:", r if isinstance(r, Unavailable) else f"{r.value:,.2f}")
    if not isinstance(r, Unavailable):
        print(" ", r.cite())
