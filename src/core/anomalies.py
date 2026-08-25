"""급등락 랭킹 이상치 판별. 순수 함수 — I/O 없음.

랭킹의 등락률을 그대로 싣지 않는 이유: **직전 종가가 비교 대상이 못 되는 경우**가
섞여 들어온다. 거래정지 후 재개, 신규상장, 액면분할·병합이 그렇다.

관측 사례 (2026-08-25 한국 급락 1위):
    096610 알에프세미 -95.34%.
    일봉을 보면 **28거래일 이상 종가 2,965원·거래량 0** 으로 고정돼 있다가
    08-25 에 138원·거래량 305만주로 재개됐다. 랭킹의 -95.34% 는 정지 직전 가격과의 비교다.
    공시로 확정한 원인은 액면분할이 아니라 **상장폐지에 따른 정리매매 개시**였다.

이 모듈은 **정황만** 만든다. 원인 확정은 공시(1차)로 한다 —
`sources/open_dart.corporate_actions()` 가 분할·병합·감자·거래정지·상장폐지 공시를 찾아온다.
정황과 확정을 섞지 않는 이유는 CLAUDE.md 의 사실/해석 분리와 같다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ── 임계값. 여기만 고치면 민감도가 바뀐다 ──────────────────────
EXTREME_MOVE = 0.20        # 이 아래 변동은 검사하지 않는다 (외부 호출 절약)
HALT_MIN_SESSIONS = 3      # 무거래·종가고정 연속 세션 → 거래정지 정황
MIN_ROWS = 2               # 직전 종가 비교에 필요한 최소 봉 수
#: 분할 배수 허용 오차. 3% 는 너무 넓었다 — AMIX 가 5.37→10.52(비율 0.5105)로
#: "2:1 병합" 에 걸렸는데 실제로는 그냥 +96% 급등이었다. ±100% 는 흔한 변동폭이라
#: 3% 창을 두면 그 구간이 통째로 분할로 잡힌다.
#: 진짜 권리락은 기준가가 **정확히** 배수로 조정되므로 좁혀도 놓치지 않는다.
SPLIT_TOL = 0.015
BASE_TOL = 0.02            # 랭킹 등락률 vs 일봉 등락률 허용 오차

#: 액면분할·병합에서 실제로 쓰이는 배수. 이 배수에 근접해야만 정황으로 본다.
SPLIT_RATIOS = (2, 2.5, 3, 4, 5, 10, 20, 50, 100)


class AnomalyKind(Enum):
    """랭킹 등락률을 액면 그대로 읽으면 안 되는 이유들."""

    NO_HISTORY = "이력 부족"
    HALT_RESUME = "거래정지 후 재개 정황"
    SPLIT_LIKE = "액면분할·병합 정황"
    BASE_MISMATCH = "랭킹 기준가와 일봉 불일치"
    EXTREME = "설명되지 않는 극단 변동"


@dataclass(frozen=True)
class Anomaly:
    kind: AnomalyKind
    detail: str
    #: True 면 등락률 자체가 비교 불가다. False 면 값은 살아 있고 맥락만 덧붙는다.
    invalidates_rate: bool = False

    @property
    def is_warning(self) -> bool:
        """표에 ⚠ 를 붙일 값어치가 있는가.

        EXTREME 은 '검사했고 인위적 요인이 없었다' 는 결과다. 그걸 경고로 띄우면
        급등 상위가 전부 ⚠ 로 도배돼 진짜 경고가 묻힌다.
        """
        return self.kind is not AnomalyKind.EXTREME

    def __str__(self) -> str:
        return f"[{self.kind.value}] {self.detail}"


def _flat_run(rows: list[dict]) -> int:
    """마지막 봉 **직전까지** 이어지는 무거래·종가고정 세션 수.

    거래정지 구간은 거래량 0 에 종가가 정지 직전 값으로 고정된다.
    둘 중 하나만 봐도 되지만, 벤더가 거래량을 채워 보내는 경우가 있어 둘 다 본다.
    """
    n = 0
    for i in range(len(rows) - 2, -1, -1):
        vol = rows[i].get("volume")
        if vol is not None and vol == 0:
            quiet = True
        elif i > 0:
            quiet = rows[i]["close"] == rows[i - 1]["close"]
        else:
            quiet = False      # 첫 봉은 비교 대상이 없다 — 거래량으로만 판정한다
        if not quiet:
            break
        n += 1
    return n


def _split_ratio(prev: float, cur: float) -> tuple[float, str] | None:
    """직전/현재 종가 비율이 흔한 분할·병합 배수에 근접하면 (배수, 방향)."""
    if not prev or not cur:
        return None
    r = prev / cur
    for k in SPLIT_RATIOS:
        if abs(r / k - 1) <= SPLIT_TOL:
            return (k, "분할")
        if abs(r * k - 1) <= SPLIT_TOL:
            return (k, "병합")
    return None


def inspect(change_rate: float | None, rows: list[dict], *,
            extreme: float = EXTREME_MOVE) -> list[Anomaly]:
    """랭킹 한 줄을 일봉과 대조한다. `rows` 는 시간순 일봉(close·volume).

    ±`extreme` 미만이면 빈 리스트를 돌려준다 — 평범한 변동에 경고를 붙이지 않는다.
    ("변화가 없으면 '변화 없음'이 정답이다", CLAUDE.md)
    """
    if change_rate is None or abs(change_rate) < extreme:
        return []

    if len(rows) < MIN_ROWS:
        return [Anomaly(AnomalyKind.NO_HISTORY,
                        f"일봉 {len(rows)}개 — 직전 종가 비교 불가 (신규상장·거래재개 가능)",
                        invalidates_rate=True)]

    prev, cur = rows[-2]["close"], rows[-1]["close"]
    found: list[Anomaly] = []

    quiet = _flat_run(rows)
    if quiet >= HALT_MIN_SESSIONS:
        # 조회 창을 다 채웠으면 그 이전은 모른다. "28거래일" 로 단정하지 않는다.
        span = f"{quiet}거래일" + (" 이상" if quiet >= len(rows) - 1 else "")
        found.append(Anomaly(
            AnomalyKind.HALT_RESUME,
            f"직전 {span} 무거래·종가 {prev:,.0f} 고정 후 재개 — "
            f"비교 대상이 정지 직전 가격이다",
            invalidates_rate=True))

    hit = _split_ratio(prev, cur)
    if hit:
        k, way = hit
        found.append(Anomaly(
            AnomalyKind.SPLIT_LIKE,
            f"{prev:,.0f} → {cur:,.0f} 이 {k:g}:1 {way} 배수에 근접 — "
            f"수정주가 미반영 가능. 공시 확인 필요",
            invalidates_rate=True))

    if prev:
        from_bars = (cur - prev) / prev
        if abs(from_bars - change_rate) > BASE_TOL:
            found.append(Anomaly(
                AnomalyKind.BASE_MISMATCH,
                f"랭킹 {change_rate:+.2%} vs 일봉 {from_bars:+.2%} — 기준가가 다르다"))

    if not found:
        found.append(Anomaly(
            AnomalyKind.EXTREME,
            f"직전 종가 대비 {change_rate:+.2%} · 정지·분할 정황 없음 — 실제 변동일 수 있다"))
    return found


def worth_checking(anomalies: list[Anomaly]) -> bool:
    """공시(1차)까지 확인할 값어치가 있는가. 단순 극단치는 제외한다."""
    return any(a.is_warning for a in anomalies)


def warnings(anomalies: list[Anomaly]) -> list[Anomaly]:
    """표시할 경고만. 검사했고 깨끗했던 결과는 빼낸다."""
    return [a for a in anomalies if a.is_warning]
